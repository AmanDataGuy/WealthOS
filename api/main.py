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

## Phase 7 addition
Three observability tools are initialized at startup:
  - LangSmith  → traces every LangGraph node automatically
  - AgentOps   → tracks LLM calls and tool actions
  - W&B Weave  → eval comparison logging for Writer Agent

All three are no-ops if their API keys are missing in .env.
"""

import os
import json
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import redis.asyncio as aioredis
from dotenv import load_dotenv

load_dotenv()

from graph.graph  import wealthos_graph
from graph.state  import WealthOSState

# Phase 7 — observability startup
from observability import init_agentops, init_weave, verify_langsmith, start_session, end_session

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

app = FastAPI(
    title="WealthOS API",
    description="7-agent personal financial intelligence platform",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8501"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Phase 7: Initialize all three observability tools at startup ───────────────
# These run once when the server starts. Each prints a clear status line
# so you can see in the logs whether they connected successfully.
verify_langsmith()   # LangSmith — confirms API key and project name
init_agentops()      # AgentOps  — monkey-patches LLM clients for auto-tracking
init_weave()         # W&B Weave — connects to the WealthOS project


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


# ── Health check ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0", "agents": 7}


# ── Main analysis endpoint ─────────────────────────────────────────────────────

@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest):
    ticker = req.ticker.upper().strip()

    # Phase 7 — start an AgentOps session so all tool calls in this run are grouped
    start_session(user_id=req.user_id, ticker=ticker)

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

    try:
        result = await wealthos_graph.ainvoke(initial_state)
        end_session(success=True)
    except Exception as e:
        end_session(success=False)
        raise HTTPException(status_code=500, detail=str(e))

    risk    = result.get("risk_report") or {}
    code    = result.get("code_output") or {}
    dcf_val = None
    if code.get("dcf"):
        dcf_val = code["dcf"].get("intrinsic_value")

    try:
        redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        await redis.setex(
            f"analysis:{ticker}",
            3600,
            json.dumps({
                "memo":       result.get("final_memo"),
                "risk_score": risk.get("risk_score"),
                "verdict":    risk.get("recommendation"),
                "messages":   result.get("messages", []),
            })
        )
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

    # Phase 7 — start session for streaming runs too
    start_session(user_id=req.user_id, ticker=ticker)

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
            end_session(success=True)
            memo   = result.get("final_memo", "")
            words  = memo.split(" ")
            for i, word in enumerate(words):
                chunk = word + (" " if i < len(words) - 1 else "")
                yield f"data: {json.dumps({'event': 'chunk', 'text': chunk})}\n\n"
                await asyncio.sleep(0.02)
            yield f"data: {json.dumps({'event': 'done', 'messages': result.get('messages', [])})}\n\n"
        except Exception as e:
            end_session(success=False)
            yield f"data: {json.dumps({'event': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Last analysis state ────────────────────────────────────────────────────────

@app.get("/state/{ticker}")
async def get_state(ticker: str):
    try:
        redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        raw   = await redis.get(f"analysis:{ticker.upper()}")
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
            history_key = f"briefing:history:{req.user_id}"
            await redis.lpush(history_key, json.dumps({"date": str(__import__('datetime').date.today()), "text": result}))
            await redis.ltrim(history_key, 0, 6)   # keep last 7
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
        items = await redis.lrange(f"briefing:history:{user_id}", 0, 6)
        await redis.aclose()
        return {"user_id": user_id, "history": [json.loads(i) for i in items]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Run directly ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)