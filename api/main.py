# api/main.py
"""
WealthOS FastAPI backend.

Endpoints:
  POST /analyze            → run full pipeline, return memo
  POST /analyze/stream     → run pipeline, stream memo chunks
  GET  /health             → service health check
  GET  /state/{ticker}     → last known state for a ticker (from Redis)
  POST /briefing/send-now  → trigger morning briefing immediately (testing)
  GET  /briefing/history/{user_id} → last 7 briefings from Redis
"""

import os
import json
import time
import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import redis.asyncio as aioredis
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
os.makedirs(LOGS_DIR, exist_ok=True)
QUERY_LOG_PATH = os.path.join(LOGS_DIR, "query_log.jsonl")

load_dotenv()

from graph.graph  import wealthos_graph
from graph.state  import WealthOSState
from observability.langsmith_config import verify_langsmith
from observability.weave_config     import init_weave
from services.llm_client            import get_session_cost
from agents.agent_cards             import get_agent_card, list_all_agents

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")


@asynccontextmanager
async def lifespan(app: FastAPI):
    verify_langsmith()
    init_weave()
    yield


app = FastAPI(
    title="WealthOS API",
    description="7-agent personal financial intelligence platform",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:8501").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response schemas ─────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    query:         str
    ticker:        str
    user_id:       str = "test-user"
    invest_amount: Optional[float] = 20000.0

class AnalyzeResponse(BaseModel):
    ticker:     str
    verdict:    Optional[str]
    memo:       Optional[str]
    risk_score: Optional[int]
    dcf_value:  Optional[float]
    messages:   list[str]
    error:      Optional[str]

class BriefingRequest(BaseModel):
    user_id: str = "test-user"


# ── Query logging ─────────────────────────────────────────────────────────────

def _write_query_log(entry: dict):
    """Append one JSON line to logs/query_log.jsonl. Swallows errors so a log
    failure never breaks a real request."""
    try:
        with open(QUERY_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning("[query_log] Failed to write log entry: %s", e)


# ── Health check ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0", "agents": 7}


# ── Main analysis endpoint ─────────────────────────────────────────────────────

@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest):
    ticker = req.ticker.upper().strip()

    initial_state: WealthOSState = {
        "query":              req.query,
        "tickers":            [ticker],
        "user_id":            req.user_id,
        "user_memory":        None,
        "personal_finance":   None,
        "financial_snapshot": None,
        "research_output":    None,
        "risk_report":        None,
        "code_output":        None,
        "rebalance_suggestion": None,
        "final_memo":         None,
        "error":              None,
        "messages":           [],
    }

    t0 = time.monotonic()
    error_detail = None
    result = {}
    try:
        result = await wealthos_graph.ainvoke(initial_state)
    except Exception as e:
        error_detail = str(e)
        raise HTTPException(status_code=500, detail=error_detail)
    finally:
        latency_ms = round((time.monotonic() - t0) * 1000)
        cost_snap  = get_session_cost()
        log_entry  = {
            "query":              req.query[:100],
            "tickers":            [ticker],
            "user_id":            req.user_id,
            "latency_ms":         latency_ms,
            "total_tokens":       cost_snap["total_tokens"],
            "estimated_cost_usd": round(cost_snap["estimated_cost_usd"], 6),
            "agents_invoked":     [m.split("]")[0].lstrip("[") for m in result.get("messages", []) if "✅" in m or "❌" in m],
            "success":            error_detail is None,
            "error":              error_detail,
        }
        logger.info("[analyze] %s", json.dumps(log_entry))
        _write_query_log(log_entry)

    risk    = result.get("risk_report") or {}
    code    = result.get("code_output") or {}
    dcf_val = None
    if code.get("dcf"):
        dcf_val = code["dcf"].get("intrinsic_value")

    try:
        redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        try:
            await redis.set(
                f"analysis:{ticker}",
                json.dumps({
                    "memo":       result.get("final_memo"),
                    "risk_score": risk.get("risk_score"),
                    "verdict":    risk.get("recommendation"),
                    "messages":   result.get("messages", []),
                }),
                ex=3600,
            )
        finally:
            await redis.aclose()
    except Exception:
        pass

    return AnalyzeResponse(
        ticker=ticker,
        verdict=risk.get("recommendation"),
        memo=result.get("final_memo"),
        risk_score=risk.get("risk_score"),
        dcf_value=dcf_val,
        messages=result.get("messages", []),
        error=result.get("error"),
    )


# ── Streaming endpoint ─────────────────────────────────────────────────────────

@app.post("/analyze/stream")
async def analyze_stream(req: AnalyzeRequest):
    ticker = req.ticker.upper().strip()

    initial_state: WealthOSState = {
        "query":              req.query,
        "tickers":            [ticker],
        "user_id":            req.user_id,
        "user_memory":        None,
        "personal_finance":   None,
        "financial_snapshot": None,
        "research_output":    None,
        "risk_report":        None,
        "code_output":        None,
        "rebalance_suggestion": None,
        "final_memo":         None,
        "error":              None,
        "messages":           [],
    }

    async def event_stream():
        yield f"data: {json.dumps({'event': 'start', 'ticker': ticker})}\n\n"
        try:
            result = await wealthos_graph.ainvoke(initial_state)
            memo   = result.get("final_memo", "")
            words  = memo.split(" ")
            for i, word in enumerate(words):
                chunk = word + (" " if i < len(words) - 1 else "")
                yield f"data: {json.dumps({'event': 'chunk', 'text': chunk})}\n\n"
                await asyncio.sleep(0.02)
            yield f"data: {json.dumps({'event': 'done', 'messages': result.get('messages', [])})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'event': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Last analysis state ────────────────────────────────────────────────────────

@app.get("/state/{ticker}")
async def get_state(ticker: str):
    try:
        redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        try:
            raw   = await redis.get(f"analysis:{ticker.upper()}")
        finally:
            await redis.aclose()
        if not raw:
            raise HTTPException(status_code=404, detail=f"No cached analysis for {ticker}")
        return json.loads(raw)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Phase 6: Morning briefing endpoints ───────────────────────────────────────

@app.post("/briefing/send-now")
async def send_briefing_now(req: BriefingRequest):
    """
    Trigger a morning briefing immediately for a user.
    Used for testing — no need to wait for 8 AM cron.
    Requires Temporal worker to be running.
    """
    try:
        from temporalio.client import Client
        from workflows.morning_briefing import MorningBriefingWorkflow, TASK_QUEUE

        client = await Client.connect("localhost:7233")
        result = await client.execute_workflow(
            MorningBriefingWorkflow.run,
            req.user_id,
            id=f"briefing-now-{req.user_id}-{int(asyncio.get_event_loop().time())}",
            task_queue=TASK_QUEUE,
        )

        # Cache in Redis for history
        try:
            redis = aioredis.from_url(REDIS_URL, decode_responses=True)
            try:
                history_key = f"briefing:history:{req.user_id}"
                await redis.lpush(history_key, json.dumps({"date": str(__import__('datetime').date.today()), "text": result}))
                await redis.ltrim(history_key, 0, 6)   # keep last 7
            finally:
                await redis.aclose()
        except Exception:
            pass

        return {"user_id": req.user_id, "briefing": result}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Briefing failed: {e}")


@app.get("/briefing/history/{user_id}")
async def briefing_history(user_id: str):
    """Return last 7 morning briefings for a user."""
    try:
        redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        try:
            items = await redis.lrange(f"briefing:history:{user_id}", 0, 6)
        finally:
            await redis.aclose()
        return {"user_id": user_id, "history": [json.loads(i) for i in items]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Agent cards (A2A foundation) ──────────────────────────────────────────────

@app.get("/agents")
async def agents_list():
    """Return metadata cards for all 7 agents."""
    return {"agents": list_all_agents()}


@app.get("/agents/{agent_name}")
async def agent_detail(agent_name: str):
    """Return the metadata card for a specific agent."""
    card = get_agent_card(agent_name)
    if not card:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")
    return card


# ── Run directly ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)