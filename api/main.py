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
import shutil
import collections
from pathlib import Path
import re
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Header, Depends
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import asyncpg
import redis.asyncio as aioredis
from dotenv import load_dotenv
import bcrypt as _bcrypt

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
DB_URL    = os.getenv("WEALTHOS_DB_URL", "").replace("postgresql+asyncpg://", "postgresql://")

def _cap_password(password: str) -> bytes:
    # bcrypt truncates at 72 bytes — do it explicitly so hash and verify always agree
    return password.encode("utf-8")[:72]

def _hash_password(plain: str) -> str:
    return _bcrypt.hashpw(_cap_password(plain), _bcrypt.gensalt()).decode("utf-8")

def _verify_password(plain: str, hashed: str) -> bool:
    return _bcrypt.checkpw(_cap_password(plain), hashed.encode("utf-8"))


async def _ensure_users_table():
    if not DB_URL:
        return
    try:
        conn = await asyncpg.connect(DB_URL)
        try:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    username      TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at    TIMESTAMP DEFAULT NOW()
                );
            """)
            # Seed default accounts if they don't exist
            for uname, passwd in [("admin", "wealthos123"), ("demo", "demo123")]:
                await conn.execute(
                    "INSERT INTO users (username, password_hash) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    uname, _hash_password(passwd),
                )
        finally:
            await conn.close()
    except Exception as e:
        logger.warning("[startup] Could not ensure users table: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    verify_langsmith()
    init_weave()
    await _ensure_users_table()
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
    query:              str
    ticker:             str
    user_id:            str = "00000000-0000-0000-0000-000000000001"
    invest_amount:      Optional[float] = 20000.0
    investment_horizon: Optional[str] = None   # "short" | "mid" | "long" | None

class AnalyzeResponse(BaseModel):
    ticker:     str
    verdict:    Optional[str]
    memo:       Optional[str]
    risk_score: Optional[int]
    dcf_value:  Optional[float]
    messages:   list[str]
    error:      Optional[str]

class BriefingRequest(BaseModel):
    user_id: str = "00000000-0000-0000-0000-000000000001"

class SignupRequest(BaseModel):
    username: str
    password: str

class LoginRequest(BaseModel):
    username: str
    password: str


# ── Rate limiting (simple in-memory, per user_id, 10 req/min) ────────────────

_rate_buckets: dict = collections.defaultdict(list)
_RATE_LIMIT    = int(os.getenv("ANALYZE_RATE_LIMIT", "10"))
_RATE_WINDOW   = 60  # seconds

def _check_rate_limit(user_id: str) -> None:
    now = time.time()
    bucket = _rate_buckets[user_id]
    _rate_buckets[user_id] = [t for t in bucket if now - t < _RATE_WINDOW]
    if len(_rate_buckets[user_id]) >= _RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {_RATE_LIMIT} analyses per minute.",
        )
    _rate_buckets[user_id].append(now)


# ── API key auth dependency ───────────────────────────────────────────────────

async def verify_api_key(x_api_key: Optional[str] = Header(None)):
    api_key = os.getenv("WEALTHOS_API_KEY", "")
    if api_key and x_api_key != api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ── Input sanitization ────────────────────────────────────────────────────────

_INJECTION_PATTERNS = [
    r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?",
    r"you\s+are\s+now\s+(?:a|an)\s+",
    r"disregard\s+(?:all\s+)?(?:previous|prior)\s+",
    r"forget\s+(?:all\s+)?(?:previous|prior)\s+",
]

def _sanitize_query(q: str) -> str:
    q = q.strip()[:500]
    for pat in _INJECTION_PATTERNS:
        q = re.sub(pat, "[filtered]", q, flags=re.IGNORECASE)
    return q


# ── Query logging ─────────────────────────────────────────────────────────────

def _write_query_log(entry: dict):
    """Append one JSON line to logs/query_log.jsonl. Swallows errors so a log
    failure never breaks a real request."""
    try:
        with open(QUERY_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning("[query_log] Failed to write log entry: %s", e)


async def _save_analysis_history(user_id, ticker, query, result, latency_ms, cost_snap):
    """Persist a completed analysis run to analysis_history. Fire-and-forget."""
    if not DB_URL:
        return
    try:
        risk   = result.get("risk_report") or {}
        code   = result.get("code_output") or {}
        dcf_v  = (code.get("dcf") or {}).get("intrinsic_value")
        agents = [
            m.split("] ", 1)[1].split(" ✅")[0].split(" ❌")[0].strip()
            for m in result.get("messages", [])
            if "] " in m and ("✅" in m or "❌" in m)
        ]
        conn = await asyncpg.connect(DB_URL)
        try:
            await conn.execute(
                """
                INSERT INTO analysis_history
                    (user_id, ticker, query, verdict, risk_score, memo,
                     dcf_value, latency_ms, cost_usd, total_tokens, agents_invoked)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                """,
                user_id,
                ticker,
                query[:200],
                risk.get("recommendation"),
                risk.get("risk_score"),
                result.get("final_memo"),
                float(dcf_v) if dcf_v is not None else None,
                latency_ms,
                round(cost_snap["estimated_cost_usd"], 6),
                cost_snap["total_tokens"],
                agents,
            )
        finally:
            await conn.close()
    except Exception as e:
        logger.warning("[analyze] Failed to save analysis history: %s", e)


# ── Auth endpoints ────────────────────────────────────────────────────────────

@app.post("/auth/signup")
async def signup(req: SignupRequest):
    username = req.username.strip()
    if len(username) < 3:
        raise HTTPException(status_code=400, detail="Username must be at least 3 characters")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    if not DB_URL:
        raise HTTPException(status_code=503, detail="Database not configured")
    try:
        conn = await asyncpg.connect(DB_URL)
        try:
            row = await conn.fetchrow(
                "INSERT INTO users (username, password_hash) VALUES ($1, $2) RETURNING id::text, username",
                username, _hash_password(req.password),
            )
        finally:
            await conn.close()
        return {"user_id": row["id"], "username": row["username"], "message": "Account created successfully"}
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="Username already taken")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/auth/login")
async def login(req: LoginRequest):
    if not DB_URL:
        raise HTTPException(status_code=503, detail="Database not configured")
    try:
        conn = await asyncpg.connect(DB_URL)
        try:
            row = await conn.fetchrow(
                "SELECT id::text, username, password_hash FROM users WHERE username = $1",
                req.username.strip(),
            )
        finally:
            await conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not row or not _verify_password(req.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return {"user_id": row["id"], "username": row["username"]}


# ── Health check ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0", "agents": 7}


# ── Main analysis endpoint ─────────────────────────────────────────────────────

@app.post("/analyze", response_model=AnalyzeResponse, dependencies=[Depends(verify_api_key)])
async def analyze(req: AnalyzeRequest):
    _check_rate_limit(req.user_id)
    ticker = req.ticker.upper().strip()
    query  = _sanitize_query(req.query)

    initial_state: WealthOSState = {
        "query":              query,
        "tickers":            [ticker],
        "user_id":            req.user_id,
        "investment_horizon": req.investment_horizon,
        "fetch_plan":         None,
        "user_memory":        None,
        "past_decisions_ctx": None,
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
            "query":              query[:100],
            "tickers":            [ticker],
            "user_id":            req.user_id,
            "latency_ms":         latency_ms,
            "total_tokens":       cost_snap["total_tokens"],
            "estimated_cost_usd": round(cost_snap["estimated_cost_usd"], 6),
            "agents_invoked": [
                m.split("] ", 1)[1].split(" ✅")[0].split(" ❌")[0].strip()
                for m in result.get("messages", [])
                if "] " in m and ("✅" in m or "❌" in m)
            ],
            "success":            error_detail is None,
            "error":              error_detail,
        }
        logger.info("[analyze] %s", json.dumps(log_entry))
        _write_query_log(log_entry)
        if error_detail is None and result:
            asyncio.create_task(_save_analysis_history(
                user_id=req.user_id,
                ticker=ticker,
                query=query,
                result=result,
                latency_ms=latency_ms,
                cost_snap=cost_snap,
            ))

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

@app.post("/analyze/stream", dependencies=[Depends(verify_api_key)])
async def analyze_stream(req: AnalyzeRequest):
    ticker = req.ticker.upper().strip()
    query  = _sanitize_query(req.query)

    initial_state: WealthOSState = {
        "query":              query,
        "tickers":            [ticker],
        "user_id":            req.user_id,
        "investment_horizon": req.investment_horizon,
        "fetch_plan":         None,
        "user_memory":        None,
        "past_decisions_ctx": None,
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


# ── Analysis history ──────────────────────────────────────────────────────────

@app.get("/history/{user_id}")
async def analysis_history(user_id: str, limit: int = 20):
    """Return the last N analysis runs for a user."""
    if not DB_URL:
        return {"user_id": user_id, "history": []}
    try:
        conn = await asyncpg.connect(DB_URL)
        try:
            rows = await conn.fetch(
                """
                SELECT id::text, ticker, query, verdict, risk_score,
                       dcf_value, latency_ms, cost_usd, total_tokens,
                       agents_invoked, created_at, memo
                FROM analysis_history
                WHERE user_id = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                user_id, limit,
            )
        finally:
            await conn.close()
        return {
            "user_id": user_id,
            "history": [dict(r) for r in rows],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Portfolio ──────────────────────────────────────────────────────────────────

@app.get("/portfolio/{user_id}")
async def get_portfolio(user_id: str):
    """Return the current portfolio holdings for a user."""
    if not DB_URL:
        return {"user_id": user_id, "holdings": []}
    try:
        conn = await asyncpg.connect(DB_URL)
        try:
            rows = await conn.fetch(
                """
                SELECT ticker, quantity, avg_buy_price, target_weight, sector
                FROM portfolio_holdings
                WHERE user_id = $1
                ORDER BY ticker
                """,
                user_id,
            )
        finally:
            await conn.close()
        return {
            "user_id":  user_id,
            "holdings": [dict(r) for r in rows],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Personal document upload ───────────────────────────────────────────────────

@app.post("/upload-personal-doc")
async def upload_personal_doc(
    user_id: str = Form(...),
    file: UploadFile = File(...),
):
    """
    Index a personal finance document (PDF, HTM) into Qdrant under
    ticker=PERSONAL_{user_id} so the research agent can retrieve it
    during personalised analysis.
    """
    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".pdf", ".htm", ".html"}:
        raise HTTPException(status_code=400, detail="Only PDF and HTML files are supported.")

    # Save permanently to data/personal_docs/{user_id}/
    docs_dir = Path("data") / "personal_docs" / user_id
    docs_dir.mkdir(parents=True, exist_ok=True)
    perm_path = docs_dir / file.filename
    with perm_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        from rag.indexer import FilingIndexer
        indexer = FilingIndexer()
        result = await indexer.index_personal_doc(
            str(perm_path),
            user_id,
            filename=file.filename,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])

    return result


# ── Memory endpoints ──────────────────────────────────────────────────────────

@app.get("/memory/{user_id}")
async def get_memory(user_id: str):
    """Return Mem0 memories for a user as a plain string."""
    try:
        from memory.mem0_client import read_memory
        mem = read_memory(user_id)
        return {"memory": mem or "", "has_memory": bool(mem)}
    except Exception as e:
        return {"memory": "", "has_memory": False, "error": str(e)}


@app.delete("/memory/{user_id}")
async def clear_memory(user_id: str):
    """Delete all Mem0 memories for a user."""
    try:
        from memory.mem0_client import get_client
        get_client().delete_all(user_id=user_id)
        return {"status": "cleared"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/user-profile/{user_id}")
async def get_user_profile(user_id: str):
    """Return the user_risk_profiles row for a user."""
    if not DB_URL:
        return {"profile": None}
    try:
        conn = await asyncpg.connect(DB_URL)
        try:
            row = await conn.fetchrow(
                "SELECT * FROM user_risk_profiles WHERE user_id = $1", user_id
            )
        finally:
            await conn.close()
        return {"profile": dict(row) if row else None}
    except Exception as e:
        return {"profile": None, "error": str(e)}


@app.get("/user-analyses/{user_id}")
async def get_user_analyses(user_id: str, limit: int = 8):
    """Return the last N verdict entries from the Qdrant user_analyses collection."""
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        qc = QdrantClient(url=os.getenv("QDRANT_URL", "http://localhost:6333"))
        results, _ = qc.scroll(
            collection_name="user_analyses",
            scroll_filter=Filter(must=[
                FieldCondition("user_id", match=MatchValue(value=user_id))
            ]),
            limit=limit,
            with_payload=True,
        )
        analyses = sorted(
            [r.payload for r in results],
            key=lambda x: x.get("analysis_date", ""),
            reverse=True,
        )
        return {"analyses": analyses}
    except Exception as e:
        return {"analyses": [], "error": str(e)}


# ── Run directly ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)