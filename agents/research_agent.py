# research_agent.py
#
# WealthOS — Research Agent
# Answers the question: "What's happening in the world that affects your money?"
#
# What it does:
#   1. Pulls the user's tracked symbols from Postgres
#   2. Fetches live market data via yfinance
#   3. Fetches recent news via NewsAPI
#   4. Fetches SEC filings for US tickers via EDGAR
#   5. Searches your RAG pipeline for relevant stored context
#   6. Returns a clean ResearchSnapshot
#
# No frameworks. No hidden deps. Pure Python — same philosophy as finance_agent.py.
#
# Dependencies:
#   pip install yfinance asyncpg httpx pydantic python-dotenv


# ─────────────────────────────────────────────────────────────────────────────
#  Imports
# ─────────────────────────────────────────────────────────────────────────────

import os
import json
import asyncio
import logging
from datetime import datetime, timezone, timedelta

import httpx
import asyncpg

from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("research_agent")


# ─────────────────────────────────────────────────────────────────────────────
#  Config  —  all env vars in one place, easy to change
# ─────────────────────────────────────────────────────────────────────────────

DB_URL       = os.getenv("WEALTHOS_DB_URL", "").replace("postgresql+asyncpg://", "postgresql://")
NEWSAPI_KEY  = os.getenv("NEWSAPI_KEY", "")
RAG_TOP_K    = 5                   # how many RAG chunks to return


# ═════════════════════════════════════════════════════════════════════════════
#
#  SECTION 1 — Pydantic Models
#  These are the shapes data takes as it flows through the agent.
#
# ═════════════════════════════════════════════════════════════════════════════

class NewsItem(BaseModel):
    """One news article, cleaned and ready to use."""
    headline    : str
    source      : str
    published   : str
    url         : str
    summary     : str = ""          # filled in by LLM after fetch


class MarketSignal(BaseModel):
    """Price trend for one symbol over the last 30 days."""
    symbol      : str
    current_price : float
    change_30d_pct : float          # % change over last 30 days
    trend       : str               # "up" | "down" | "sideways"
    high_30d    : float
    low_30d     : float


class SECInsight(BaseModel):
    """Key facts pulled from a company's latest SEC filing."""
    ticker      : str
    form        : str               # "10-K" or "10-Q"
    filed_date  : str
    revenue_trend : str = ""        # LLM-generated one-liner
    risk_flags  : list[str] = []    # notable risks mentioned
    document_url : str = ""


class ResearchSnapshot(BaseModel):
    """
    Final output of the Research Agent.
    LangGraph reads this in Phase 4.
    """
    user_id          : str
    symbols_tracked  : list[str]
    market_signals   : list[MarketSignal]
    news_items       : list[NewsItem]
    sec_insights     : list[SECInsight]
    rag_context      : str = ""     # relevant chunks from pgvector
    macro_summary    : str = ""     # overall market mood, LLM-generated
    data_confidence  : str = "high" # "high" | "medium" | "low" | "none"
    generated_at     : str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status           : str = "ok"
    message          : str = ""


# ═════════════════════════════════════════════════════════════════════════════
#
#  SECTION 2 — Tool Functions
#  One job each. No side effects. Returns typed data or a safe fallback.
#
# ═════════════════════════════════════════════════════════════════════════════


# ── Tool 1 ────────────────────────────────────────────────────────────────────

async def get_tracked_symbols(user_id: str) -> list[str]:
    """
    Pull the list of symbols this user is tracking from Postgres.
    Returns an empty list if the table doesn't exist yet or user is new.

    Example output: ["AAPL", "TSLA", "RELIANCE.NS"]
    """
    if not DB_URL:
        logger.warning("DB_URL not set — skipping symbol fetch")
        return []

    try:
        conn = await asyncpg.connect(DB_URL, timeout=10)
        try:
            rows = await conn.fetch(
                "SELECT symbol FROM tracked_symbols WHERE user_id = $1",
                user_id
            )
            return [r["symbol"] for r in rows]
        finally:
            await conn.close()

    except asyncpg.exceptions.UndefinedTableError:
        # Table doesn't exist yet — totally fine for a new project
        logger.info("tracked_symbols table not found — returning empty list")
        return []
    except Exception as e:
        logger.error("get_tracked_symbols failed: %s", e)
        return []


# ── Tool 2 ────────────────────────────────────────────────────────────────────

def fetch_market_data(symbols: list[str]) -> list[MarketSignal]:
    """
    Fetch 30-day price history for each symbol using market_server MCP tools.
    Computes trend (up / down / sideways) and % change.
    """
    from mcp_servers.market_server import get_history
    signals = []

    for symbol in symbols:
        try:
            # We want 30 days of daily data
            data = get_history(symbol, period="1mo", interval="1d")
            
            if "error" in data or not data.get("bars"):
                logger.warning("No price data for %s — skipping", symbol)
                continue
                
            bars = data["bars"]
            if len(bars) < 2:
                continue

            open_price    = bars[0]["close"]
            current_price = bars[-1]["close"]
            high_30d      = max((b.get("high") or b["close"]) for b in bars)
            low_30d       = min((b.get("low") or b["close"]) for b in bars)
            change_pct    = round(((current_price - open_price) / open_price) * 100, 2)

            # Simple trend classification
            if change_pct > 3:
                trend = "up"
            elif change_pct < -3:
                trend = "down"
            else:
                trend = "sideways"

            signals.append(MarketSignal(
                symbol        = symbol,
                current_price = current_price,
                change_30d_pct = change_pct,
                trend         = trend,
                high_30d      = high_30d,
                low_30d       = low_30d,
            ))

            logger.info("%s — ₹/$ %.2f  |  %+.1f%%  |  %s", symbol, current_price, change_pct, trend)

        except Exception as e:
            logger.error("fetch_market_data failed for %s: %s", symbol, e)
            continue

    return signals



# ── Tool 3 ────────────────────────────────────────────────────────────────────

async def fetch_news(symbols: list[str], days: int = 7) -> list[NewsItem]:
    """
    Fetch recent news articles for all tracked symbols using news_server MCP tools.
    """
    from mcp_servers.news_server import search_news
    
    # Build a combined query — e.g. "AAPL OR TSLA OR Reliance"
    query = " OR ".join(symbols[:5])  # NewsAPI handles up to 5 well

    try:
        data = search_news(query, days=days, count=10)
        articles = []
        for a in data.get("articles", []):
            articles.append(NewsItem(
                headline  = a.get("title", ""),
                source    = a.get("source", "Unknown"),
                published = a.get("published", ""),
                url       = a.get("url", ""),
            ))

        logger.info("Fetched %d news articles for: %s", len(articles), query)
        return articles
    except Exception as e:
        logger.error("fetch_news failed: %s", e)
        return []


# ── Tool 4 ────────────────────────────────────────────────────────────────────

async def fetch_sec_insights(symbols: list[str]) -> list[SECInsight]:
    """
    Fetch the latest 10-K or 10-Q for each symbol from SEC EDGAR.
    Only works for US-listed tickers — Indian tickers are silently skipped.
    No API key needed — SEC EDGAR is a free public API.
    """
    insights = []
    headers  = {"User-Agent": "WealthOS research@wealthos.app"}

    # First, get the SEC CIK number for each ticker
    async with httpx.AsyncClient(timeout=20, headers=headers) as client:

        # SEC publishes a single JSON file mapping all tickers → CIK
        try:
            resp = await client.get("https://www.sec.gov/files/company_tickers.json")
            resp.raise_for_status()
            ticker_map = {
                v["ticker"].upper(): str(v["cik_str"]).zfill(10)
                for v in resp.json().values()
            }
        except Exception as e:
            logger.error("Could not fetch SEC ticker map: %s", e)
            return []

        for symbol in symbols:
            # Strip exchange suffix — "RELIANCE.NS" → skip, "AAPL" → look up
            clean = symbol.split(".")[0].upper()
            cik   = ticker_map.get(clean)

            if not cik:
                logger.info("%s not found in SEC database — likely non-US ticker, skipping", symbol)
                continue

            try:
                # Fetch all filings for this company
                sub_resp = await client.get(f"https://data.sec.gov/submissions/CIK{cik}.json")
                sub_resp.raise_for_status()
                subs = sub_resp.json()

                recent    = subs.get("filings", {}).get("recent", {})
                forms     = recent.get("form", [])
                dates     = recent.get("filingDate", [])
                accessions = recent.get("accessionNumber", [])
                docs      = recent.get("primaryDocument", [])

                # Find the most recent 10-K or 10-Q
                filing = None
                for i, form in enumerate(forms):
                    if form in ("10-K", "10-Q"):
                        acc_clean = accessions[i].replace("-", "")
                        doc_url   = (
                            f"https://www.sec.gov/Archives/edgar/data/"
                            f"{int(subs.get('cik', 0))}/{acc_clean}/{docs[i]}"
                            if i < len(docs) else ""
                        )
                        filing = SECInsight(
                            ticker       = symbol,
                            form         = form,
                            filed_date   = dates[i],
                            document_url = doc_url,
                        )
                        break

                if filing:
                    insights.append(filing)
                    logger.info("%s — latest filing: %s on %s", symbol, filing.form, filing.filed_date)

            except Exception as e:
                logger.error("SEC fetch failed for %s: %s", symbol, e)
                continue

    return insights


# ── Tool 5 ────────────────────────────────────────────────────────────────────

async def summarize_with_llm(text: str, instruction: str) -> str:
    return ""


# ── Tool 6 ────────────────────────────────────────────────────────────────────

async def query_rag(question: str, user_id: str, symbols: list[str] | None = None) -> str:
    try:
        from rag.query_engine import FilingQueryEngine
        engine = FilingQueryEngine()
        parts = []

        for ticker in (symbols or [])[:3]:
            result = await engine.search(question=question, ticker=ticker)
            if result:
                parts.append(f"[{ticker} filing] {result}")

        # Always fetch this user's personal finance documents regardless of query similarity
        personal_ticker = f"PERSONAL_{user_id}"
        try:
            from rag.query_engine import get_qdrant_client
            from qdrant_client.models import Filter, FieldCondition, MatchValue
            qc = get_qdrant_client()
            hits, _ = await asyncio.to_thread(
                qc.scroll,
                collection_name="wealthos_docs",
                scroll_filter=Filter(must=[FieldCondition(key="ticker", match=MatchValue(value=personal_ticker))]),
                limit=5,
                with_payload=True,
            )
            personal_chunks = [h.payload.get("content", "") for h in hits if h.payload.get("content")]
            if personal_chunks:
                parts.append("[Personal documents]\n" + "\n\n".join(personal_chunks))
        except Exception as e:
            logger.warning("Personal doc fetch failed: %s", e)

        return "\n\n".join(parts)
    except Exception as e:
        logger.warning("RAG query failed: %s", e)
        return ""


# ═════════════════════════════════════════════════════════════════════════════
#
#  SECTION 3 — Orchestrator
#  Runs all tools in the right order. Handles every cold start case.
#  This is what LangGraph calls in Phase 4.
#
# ═════════════════════════════════════════════════════════════════════════════

async def run_research_agent(
    user_id        : str,
    custom_symbols : list[str] | None = None,   # override tracked symbols
    custom_query   : str | None       = None,   # research a specific topic
) -> ResearchSnapshot:
    """
    Main entry point for the Research Agent.

    Args:
        user_id:        The user's UUID — used for DB lookups and RAG
        custom_symbols: Override the DB symbols (useful for testing)
        custom_query:   Research a specific question instead of portfolio news

    Returns:
        ResearchSnapshot — always returns something, never crashes
    """
    logger.info("=" * 60)
    logger.info("  WealthOS — Research Agent  |  user: %s", user_id)
    logger.info("=" * 60)

    # ── Step 1 — Figure out what to research ──────────────────────────────────

    symbols = custom_symbols or await get_tracked_symbols(user_id)

    # Cold start path 1: no symbols, no query
    if not symbols and not custom_query:
        logger.info("No symbols and no query — returning macro-only snapshot")
        macro = await summarize_with_llm(
            "Global markets, interest rates, inflation, USD/INR, Nifty 50",
            "Write a 3-sentence macro market summary for an Indian retail investor."
        )
        return ResearchSnapshot(
            user_id         = user_id,
            symbols_tracked = [],
            market_signals  = [],
            news_items      = [],
            sec_insights    = [],
            macro_summary   = macro or "No market data available at this time.",
            data_confidence = "low",
            message         = "No symbols tracked. Add stocks to your watchlist for personalised research.",
        )

    # Cold start path 2: custom query, no symbols
    if custom_query and not symbols:
        logger.info("Custom query mode: %s", custom_query)
        rag_context = await query_rag(custom_query, user_id)
        summary     = await summarize_with_llm(
            rag_context or custom_query,
            f"Answer this research question for a retail investor: {custom_query}"
        )
        return ResearchSnapshot(
            user_id         = user_id,
            symbols_tracked = [],
            market_signals  = [],
            news_items      = [],
            sec_insights    = [],
            rag_context     = rag_context,
            macro_summary   = summary,
            data_confidence = "medium",
            message         = f"Custom query research: {custom_query}",
        )

    logger.info("Symbols to research: %s", symbols)

    # ── Step 2 — Fetch all data in parallel ───────────────────────────────────
    #
    # market data (yfinance) is synchronous — runs in a thread
    # news and SEC are async — run concurrently
    #
    logger.info("Fetching market data, news, and SEC filings...")

    _results = await asyncio.gather(
        asyncio.wait_for(asyncio.to_thread(fetch_market_data, symbols), timeout=30),
        asyncio.wait_for(fetch_news(symbols),         timeout=20),
        asyncio.wait_for(fetch_sec_insights(symbols), timeout=25),
        return_exceptions=True,
    )

    market_signals = _results[0] if not isinstance(_results[0], BaseException) else []
    news_items     = _results[1] if not isinstance(_results[1], BaseException) else []
    sec_insights   = _results[2] if not isinstance(_results[2], BaseException) else []

    _failed = [("market", _results[0]), ("news", _results[1]), ("sec", _results[2])]
    for _name, _r in _failed:
        if isinstance(_r, BaseException):
            logger.warning("Parallel fetch %s failed: %s: %s", _name, type(_r).__name__, _r)

    logger.info(
        "Fetched — signals: %d | news: %d | SEC: %d",
        len(market_signals), len(news_items), len(sec_insights)
    )

    # ── Step 3 — LLM enrichment ───────────────────────────────────────────────
    #
    # Summarize news headlines into readable one-liners.
    # Enrich SEC insights with a revenue trend and risk flags.
    # Generate an overall macro summary.
    #

    # Summarize top 5 news items (don't burn tokens on all of them)
    for item in news_items[:5]:
        if item.headline:
            item.summary = await summarize_with_llm(
                item.headline,
                "Explain this financial news headline in one simple sentence for a retail investor."
            )

    # Pull revenue trend + risk flags from SEC filing metadata
    for insight in sec_insights:
        if insight.document_url:
            insight.revenue_trend = await summarize_with_llm(
                f"{insight.ticker} filed a {insight.form} on {insight.filed_date}",
                "In one sentence, what should an investor check in this type of SEC filing?"
            )

    # Build overall macro summary from market signals
    if market_signals:
        signals_text = "\n".join([
            f"{s.symbol}: {s.trend} ({s.change_30d_pct:+.1f}% in 30 days, current: {s.current_price})"
            for s in market_signals
        ])
        macro_summary = await summarize_with_llm(
            signals_text,
            "Write a 2-3 sentence market overview for an Indian retail investor based on these signals."
        )
    else:
        macro_summary = ""

    # ── Step 4 — RAG context ──────────────────────────────────────────────────

    rag_query   = custom_query or f"financial news and analysis for {', '.join(symbols)}"
    rag_context = await query_rag(rag_query, user_id, symbols=symbols)

    # ── Step 5 — Assess confidence and return ─────────────────────────────────

    if len(market_signals) >= 3 and len(news_items) >= 3:
        confidence = "high"
    elif len(market_signals) >= 1 or len(news_items) >= 1:
        confidence = "medium"
    else:
        confidence = "low"

    logger.info("Research complete — confidence: %s", confidence)

    return ResearchSnapshot(
        user_id         = user_id,
        symbols_tracked = symbols,
        market_signals  = market_signals,
        news_items      = news_items,
        sec_insights    = sec_insights,
        rag_context     = rag_context,
        macro_summary   = macro_summary,
        data_confidence = confidence,
        status          = "ok",
        message         = f"Research complete for {len(symbols)} symbol(s).",
    )


# ═════════════════════════════════════════════════════════════════════════════
#
#  SECTION 4 — Startup Check
#  Run this directly to verify everything is wired up before using the agent.
#  python -m agents.research_agent
#
# ═════════════════════════════════════════════════════════════════════════════

async def _startup_check():
    """Quick sanity check — no real API calls, just verifies the wiring."""

    print("\n" + "=" * 60)
    print("  WealthOS — Research Agent  |  Startup Check")
    print("=" * 60)

    # Run with a test user and two hardcoded symbols
    snapshot = await run_research_agent(
        user_id        = "test_user_001",
        custom_symbols = ["AAPL", "TSLA"],
    )

    print(f"\n  User            : {snapshot.user_id}")
    print(f"  Symbols         : {', '.join(snapshot.symbols_tracked)}")
    print(f"  Market Signals  : {len(snapshot.market_signals)}")

    for s in snapshot.market_signals:
        arrow = "↑" if s.trend == "up" else "↓" if s.trend == "down" else "→"
        print(f"    {arrow} {s.symbol:<12} ${s.current_price:>9.2f}   {s.change_30d_pct:+.1f}%")

    print(f"\n  News Items      : {len(snapshot.news_items)}")
    for n in snapshot.news_items[:3]:
        print(f"    • [{n.source}] {n.headline[:70]}...")

    print(f"\n  SEC Filings     : {len(snapshot.sec_insights)}")
    for s in snapshot.sec_insights:
        print(f"    • {s.ticker} — {s.form} filed {s.filed_date}")

    print(f"\n  RAG Context     : {'found' if snapshot.rag_context else 'none (table may not exist yet)'}")
    print(f"  Macro Summary   : {snapshot.macro_summary[:120]}..." if snapshot.macro_summary else "  Macro Summary   : (LLM not called in test mode)")
    print(f"  Confidence      : {snapshot.data_confidence}")
    print(f"  Status          : {snapshot.status}")
    print(f"  Message         : {snapshot.message}")
    print()


if __name__ == "__main__":
    asyncio.run(_startup_check())