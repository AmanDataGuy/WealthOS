"""
# assistant.py - numbers and chat: the metric catalog, checked metric / SQL queries, questions, saved chats,
remembered meanings, and the filings chat can answer from

- Routes under `/metrics`, `/query`, `/assistant` and `/research`.
- `/assistant/ask/stream` answers the same question as `/assistant/ask` (same code), sending each finished step as a
  server-sent event and the full answer last. The answer text is never sent early, only after its number check.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.data import engine
from app.main import USER, USER_WS, verify_user_token

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Numbers: trusted metrics and checked read-only SQL (every answer says where it came from) ──────────────

# A metrics question: which metrics, split by what, for which period.
class MetricQueryBody(BaseModel):
    user_id: str
    workspace_id: Optional[str] = None  # None = all of the user's data
    metrics: list[str]
    dimensions: list[str] = []
    time: str = "fy"
    filters: list[dict] = []


# One read-only SQL query.
class SqlBody(BaseModel):
    user_id: str
    sql: str
    workspace_id: Optional[str] = None


@router.get("/metrics")
def metric_catalog():
    """Every built-in metric: what it reads and its unit."""
    return {k: {f: v[f] for f in ("name", "shape", "unit", "time_semantics")} for k, v in engine.load_metrics().items()}


@router.post("/query/metric")
async def query_metric(body: MetricQueryBody, authorization: Optional[str] = Header(None)):
    """Compute metrics for a period (time = "fy", "mtd", "2026-01-01..2026-03-31" ...)."""
    await verify_user_token(body.user_id, authorization)
    q = engine.MetricQuery(metrics=body.metrics, dimensions=body.dimensions, time=body.time, filters=body.filters)
    return (await asyncio.to_thread(engine.run_metric, body.user_id, q, None, body.workspace_id)).model_dump()


@router.post("/query/sql")
async def query_sql(body: SqlBody, authorization: Optional[str] = Header(None)):
    """One checked read-only SELECT over the user's own tables."""
    await verify_user_token(body.user_id, authorization)
    return (await asyncio.to_thread(engine.run_sql, body.user_id, body.sql, body.workspace_id)).model_dump()


# ── Chat: plain-English questions, saved chats, remembered meanings ─────────────────────────────────────────

# A chat question.
class AskBody(BaseModel):
    user_id: str
    question: str
    workspace_id: Optional[str] = None
    previous_plan: Optional[dict] = None  # the last answer's plan, for follow-up questions
    define: Optional[dict] = None         # {"term", "value"} when answering a clarify question
    chat_id: Optional[str] = None         # continue this saved chat (its workspace wins); none = a new chat


@router.post("/assistant/ask")
async def assistant_ask(body: AskBody, authorization: Optional[str] = Header(None)):
    """Answer a question with its numbers, where they came from, and whether the text passed the number check."""
    await verify_user_token(body.user_id, authorization)
    return await _answer(body, *_chat_for(body))


@router.post("/assistant/ask/stream")
async def assistant_ask_stream(body: AskBody, authorization: Optional[str] = Header(None)):
    """The same answer as /assistant/ask, as server-sent events: `step` for each finished step, then `answer`
    (or `error`)."""
    await verify_user_token(body.user_id, authorization)
    workspace, chat_id = _chat_for(body)  # an unknown chat is a plain 404 before the stream starts
    queue: asyncio.Queue = asyncio.Queue()

    # Answer in the background, putting each step and then the answer (or the error) on the queue.
    async def run():
        try:
            answer = await _answer(body, workspace, chat_id, lambda step: queue.put_nowait(("step", step)))
            queue.put_nowait(("answer", answer))
        except Exception:
            logger.exception("streamed answer failed")
            queue.put_nowait(("error", {"detail": "the answer could not be completed"}))

    # Send queued events until the answer or the error.
    async def events():
        task = asyncio.create_task(run())  # kept running if the reader leaves, so the turn is still saved
        while True:
            kind, data = await queue.get()
            yield f"event: {kind}\ndata: {json.dumps(jsonable_encoder(data))}\n\n"
            if kind != "step":
                await task
                return
    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# The chat's workspace and id: a saved chat keeps its workspace; a new chat gets its id now, so its state is saved
# under it.
def _chat_for(body: AskBody) -> tuple[str, str]:
    from app.ai import agent
    workspace = agent.get_chat(body.user_id, body.chat_id)["workspace_id"] if body.chat_id else body.workspace_id or "default"
    return workspace, body.chat_id or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")


# Answer one question, name a new chat and save the turn. Both ask routes use this, so their answers are the same.
async def _answer(body: AskBody, workspace: str, chat_id: str, on_step=None) -> dict:
    from app.ai import agent
    answer = (await agent.ask(body.user_id, body.question, body.previous_plan, body.define, workspace, chat_id,
                              on_step)).model_dump()
    title = None if body.chat_id else await agent.chat_title(body.user_id, body.question)
    agent.save_turn(body.user_id, chat_id, workspace, body.question, answer, title)
    return {**answer, "chat_id": chat_id}


@router.get("/assistant/{user_id}/chats", dependencies=USER)
def assistant_chats(user_id: str, workspace_id: Optional[str] = None):
    """The user's saved chats, newest first."""
    from app.ai.agent import list_chats
    return list_chats(user_id, workspace_id)


@router.get("/assistant/{user_id}/chats/{chat_id}", dependencies=USER)
def assistant_chat(user_id: str, chat_id: str):
    """One saved chat with every question and answer."""
    from app.ai.agent import get_chat
    return get_chat(user_id, chat_id)


@router.delete("/assistant/{user_id}/chats/{chat_id}", dependencies=USER)
def assistant_delete_chat(user_id: str, chat_id: str):
    """Delete one saved chat."""
    from app.ai.agent import delete_chat
    delete_chat(user_id, chat_id)
    return {"deleted": chat_id}


@router.get("/assistant/{user_id}/definitions", dependencies=USER)
def assistant_definitions(user_id: str):
    """What the user told the assistant a word means ("rent" = the Rent description ...)."""
    from app.ai.agent import definitions
    return definitions(user_id)


@router.delete("/assistant/{user_id}/definitions/{term}", dependencies=USER)
def assistant_forget(user_id: str, term: str):
    """Forget one remembered meaning."""
    from app.ai.agent import delete_definition
    return delete_definition(user_id, term)


@router.get("/research/{user_id}/{workspace_id}", dependencies=USER_WS)
def research_docs(user_id: str, workspace_id: str):
    """The workspace's filings / annual reports whose text chat can answer from."""
    from app.ai.research import list_docs
    return list_docs(user_id, workspace_id)


@router.delete("/research/{user_id}/{workspace_id}/{doc_id}", dependencies=USER_WS)
def research_delete(user_id: str, workspace_id: str, doc_id: str):
    """Stop using one document in chat (its statements table stays)."""
    from app.ai.research import delete_doc
    delete_doc(user_id, workspace_id, doc_id)
    return {"deleted": doc_id}
