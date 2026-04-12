# api/main.py
"""
WealthOS FastAPI backend.

Endpoints:
  POST /analyze          → run full pipeline, return memo
  POST /analyze/stream   → run pipeline, stream memo chunks
  GET  /health           → service health check
  GET  /state/{ticker}   → last known state for a ticker (from Redis)
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

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

app = FastAPI(
    title="WealthOS API",
    description="7-agent personal financial intelligence platform",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response schemas ─────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    query:      str
    ticker:     str
    user_id:    str = "test-user"
    invest_amount: Optional[float] = 20000.0   # amount being considered

class AnalyzeResponse(BaseModel):
    ticker:     str
    verdict:    Optional[str]
    memo:       Optional[str]
    risk_score: Optional[int]
    dcf_value:  Optional[float]
    messages:   list[str]
    error:      Optional[str]


# ── Health check ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0", "agents": 7}


# ── Main analysis endpoint ─────────────────────────────────────────────────────

@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest):
    """
    Run the full 7-agent pipeline and return the complete memo.
    Typical latency: 60-120 seconds depending on Ollama load.
    """
    ticker = req.ticker.upper().strip()

    initial_state: WealthOSState = {
        "query":              req.query,
        "tickers":            [ticker],
        "user_id":            req.user_id,
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Extract key fields for the response
    risk    = result.get("risk_report") or {}
    code    = result.get("code_output") or {}
    dcf_val = None
    if code.get("dcf"):
        dcf_val = code["dcf"].get("intrinsic_value")

    # Cache result in Redis for /state endpoint
    try:
        redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        await redis.setex(
            f"analysis:{ticker}",
            3600,   # 1 hour
            json.dumps({
                "memo":       result.get("final_memo"),
                "risk_score": risk.get("risk_score"),
                "verdict":    risk.get("recommendation"),
                "messages":   result.get("messages", []),
            })
        )
        await redis.aclose()
    except Exception:
        pass   # Redis failure should not break the response

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
    """
    Same pipeline as /analyze but streams the memo word by word.
    Connect with EventSource or fetch with ReadableStream on the frontend.
    """
    ticker = req.ticker.upper().strip()

    initial_state: WealthOSState = {
        "query":              req.query,
        "tickers":            [ticker],
        "user_id":            req.user_id,
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
        # Stream node progress events first
        yield f"data: {json.dumps({'event': 'start', 'ticker': ticker})}\n\n"

        try:
            result = await wealthos_graph.ainvoke(initial_state)
            memo   = result.get("final_memo", "")

            # Stream the memo word by word
            words = memo.split(" ")
            for i, word in enumerate(words):
                chunk = word + (" " if i < len(words) - 1 else "")
                yield f"data: {json.dumps({'event': 'chunk', 'text': chunk})}\n\n"
                await asyncio.sleep(0.02)   # ~50 words/sec

            yield f"data: {json.dumps({'event': 'done', 'messages': result.get('messages', [])})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'event': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Last analysis state ────────────────────────────────────────────────────────

@app.get("/state/{ticker}")
async def get_state(ticker: str):
    """Return the last cached analysis for a ticker (up to 1 hour old)."""
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


# ── Run directly ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)