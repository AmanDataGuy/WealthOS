# agents/router_agent.py
"""
Router Agent — classifies every incoming query before the pipeline starts.

Sets three fields on WealthOSState:
  investment_horizon : "short" | "mid" | "long" | "unknown"
  company_tier       : "well_indexed" | "thin_indexed" | "not_indexed"
  user_tier          : "new" | "returning" | "power"
  fetch_plan         : dict controlling what each downstream node fetches

Called as the first node in the LangGraph pipeline.
Cost: ~200 tokens per call (tiny LLM call).
"""

import os
import json
import asyncio
import logging

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = "llama-3.1-8b-instant"  # small model — just classification

HORIZON_PROMPT = """Classify this investment query into one of three horizons.

Horizons:
- "short": trading, quick profit, days to weeks, earnings play, technical levels, momentum
- "mid": earnings cycle, 1-3 months, near-term catalyst, upcoming results
- "long": fundamentals, long-term hold, valuation, moat, DCF, 1+ year

Query: {query}
Ticker: {ticker}

Respond with ONLY a JSON object, no explanation:
{{"investment_horizon": "short|mid|long", "confidence": 0.0-1.0, "key_intent": "one sentence"}}"""


async def _classify_horizon(query: str, ticker: str) -> tuple[str, float]:
    """Returns (horizon, confidence). Defaults to 'long' on any failure."""
    if not GROQ_API_KEY:
        return "long", 0.5

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": GROQ_MODEL,
                    "messages": [{"role": "user", "content": HORIZON_PROMPT.format(query=query, ticker=ticker)}],
                    "max_tokens": 80,
                    "temperature": 0.1,
                },
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
            # Strip markdown code fences if present
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text)
            horizon    = data.get("investment_horizon", "long")
            confidence = float(data.get("confidence", 0.7))
            if horizon not in ("short", "mid", "long"):
                horizon = "long"
            # Low confidence → default to long (safer)
            if confidence < 0.65:
                horizon = "long"
            return horizon, confidence
    except Exception as e:
        logger.warning("[router] horizon classification failed: %s — defaulting to long", e)
        return "long", 0.5


def _get_company_tier(ticker: str) -> str:
    """
    Check Qdrant chunk count to classify company coverage.
    Returns 'well_indexed' (>=100), 'thin_indexed' (>=10), or 'not_indexed' (<10).
    """
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        qc  = QdrantClient(url=os.getenv("QDRANT_URL", "http://localhost:6333"))
        res = qc.count(
            "wealthos_docs",
            count_filter=Filter(must=[FieldCondition("ticker", match=MatchValue(value=ticker))]),
        )
        count = res.count
        if count >= 100:
            return "well_indexed"
        elif count >= 10:
            return "thin_indexed"
        else:
            return "not_indexed"
    except Exception as e:
        logger.warning("[router] Qdrant tier check failed: %s", e)
        return "thin_indexed"  # safe default — don't block pipeline


async def _get_user_tier(user_id: str) -> str:
    """
    Check analysis_history count to classify user experience.
    Returns 'new' (0), 'returning' (1-5), or 'power' (6+).
    """
    try:
        import asyncpg
        db_url = os.getenv("WEALTHOS_DB_URL", "").replace("postgresql+asyncpg://", "postgresql://")
        if not db_url:
            return "new"
        conn  = await asyncpg.connect(db_url)
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM analysis_history WHERE user_id = $1", user_id
        )
        await conn.close()
        if count >= 6:
            return "power"
        elif count >= 1:
            return "returning"
        return "new"
    except Exception as e:
        logger.warning("[router] user tier check failed: %s", e)
        return "new"


def _build_fetch_plan(horizon: str, company_tier: str) -> dict:
    """Build the fetch_plan dict that downstream nodes check."""
    base = {
        "use_rag":               True,
        "use_dcf":               horizon in ("mid", "long"),
        "use_technicals":        horizon in ("short", "mid"),
        "use_options":           horizon == "short",
        "use_news_full":         horizon in ("short", "mid"),
        "use_earnings_transcript": horizon in ("short", "mid"),
        "horizon":               horizon,
        "company_tier":          company_tier,
    }
    # Thin / unindexed companies: skip heavy RAG sections
    if company_tier == "not_indexed":
        base["use_rag"] = False
    return base


async def _on_demand_index(ticker: str) -> None:
    """
    Background task: download latest 10-K from SEC EDGAR and index it into Qdrant.
    Upserts indexed_tickers on success; marks status='failed' on error.
    Fired via asyncio.create_task — never blocks the pipeline.
    """
    import httpx
    logger.info("[router] on-demand indexing triggered for %s", ticker)
    try:
        from mcp_servers.sec_edgar_server import get_10k
        meta = await asyncio.to_thread(get_10k, ticker)
        if "error" in meta:
            logger.warning("[router] SEC EDGAR lookup: %s", meta["error"])
            return

        document_url = meta["document_url"]
        filing_date  = meta.get("filed_date", "")
        company_name = meta.get("company_name", ticker)

        # Persist to data/filings/ so the file survives between runs
        import pathlib
        filings_dir = pathlib.Path("data/filings")
        filings_dir.mkdir(parents=True, exist_ok=True)
        dest = filings_dir / f"{ticker}_10-K.htm"

        async with httpx.AsyncClient(
            timeout=120, follow_redirects=True,
            headers={"User-Agent": "WealthOS/1.0 ankit.work2026@gmail.com"},
        ) as client:
            resp = await client.get(document_url)
            resp.raise_for_status()
            dest.write_bytes(resp.content)

        logger.info("[router] downloaded 10-K for %s (%d bytes)", ticker, len(resp.content))

        from rag.indexer import FilingIndexer
        result      = await FilingIndexer().index_filing(
            file_path=str(dest), ticker=ticker,
            filing_type="10-K", filing_date=filing_date,
        )
        chunk_count = result.get("chunks_indexed", 0)
        logger.info("[router] indexed %d chunks for %s", chunk_count, ticker)

        db_url = os.getenv("WEALTHOS_DB_URL", "").replace("postgresql+asyncpg://", "postgresql://")
        if db_url:
            import asyncpg
            conn = await asyncpg.connect(db_url)
            await conn.execute(
                """
                INSERT INTO indexed_tickers
                    (ticker, company_name, chunk_count, filing_year, data_source, status)
                VALUES ($1, $2, $3, $4, 'sec_edgar', 'active')
                ON CONFLICT (ticker) DO UPDATE SET
                    company_name    = EXCLUDED.company_name,
                    chunk_count     = EXCLUDED.chunk_count,
                    filing_year     = EXCLUDED.filing_year,
                    data_source     = 'sec_edgar',
                    status          = 'active',
                    last_indexed_at = NOW()
                """,
                ticker, company_name, chunk_count,
                filing_date[:4] if filing_date else None,
            )
            await conn.close()
            logger.info("[router] indexed_tickers upserted for %s", ticker)

    except Exception as e:
        logger.error("[router] on-demand indexing failed for %s: %s", ticker, e)
        try:
            db_url = os.getenv("WEALTHOS_DB_URL", "").replace("postgresql+asyncpg://", "postgresql://")
            if db_url:
                import asyncpg
                conn = await asyncpg.connect(db_url)
                await conn.execute(
                    """
                    INSERT INTO indexed_tickers (ticker, status) VALUES ($1, 'failed')
                    ON CONFLICT (ticker) DO UPDATE SET status = 'failed', last_indexed_at = NOW()
                    """,
                    ticker,
                )
                await conn.close()
        except Exception:
            pass


async def run_router_agent(
    query: str,
    ticker: str,
    user_id: str,
    investment_horizon: str | None = None,
) -> dict:
    """
    Main entry point. If investment_horizon is provided (from UI), skip LLM classification.
    Returns partial WealthOSState dict.
    """
    if investment_horizon and investment_horizon in ("short", "mid", "long"):
        horizon, confidence = investment_horizon, 1.0
        logger.info("[router] horizon from UI: %s", horizon)
    else:
        horizon, confidence = await _classify_horizon(query, ticker)
        logger.info("[router] horizon classified: %s (confidence=%.2f)", horizon, confidence)

    company_tier, user_tier = await asyncio.gather(
        asyncio.to_thread(_get_company_tier, ticker),
        _get_user_tier(user_id),
    )

    # Fire-and-forget indexing for unknown companies — pipeline continues immediately
    if company_tier == "not_indexed":
        asyncio.create_task(_on_demand_index(ticker))
        logger.info("[router] background indexing scheduled for %s", ticker)

    fetch_plan = _build_fetch_plan(horizon, company_tier)

    logger.info(
        "[router] %s | horizon=%s | tier=%s | user=%s",
        ticker, horizon, company_tier, user_tier,
    )

    return {
        "investment_horizon": horizon,
        "company_tier":       company_tier,
        "user_tier":          user_tier,
        "fetch_plan":         fetch_plan,
    }
