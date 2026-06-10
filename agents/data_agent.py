# agents/data_agent.py
"""
Data Agent — PydanticAI
=======================
Fetches hard financial numbers with zero hallucination.
Every number comes from a trusted source (DB or yfinance).
LLM is only used to extract data from RAG chunks — never to invent numbers.

Flow:
  1. Check Redis cache (15-min TTL)
  2. Fetch from financial_facts table (SQL — exact numbers)
  3. Fetch live market data from market_server MCP
  4. Query RAG pipeline for qualitative context
  5. Validate everything against FinancialSnapshot schema
  6. Cache result and return
"""

import os
import json
import asyncio
import time
import asyncpg
import httpx
import redis.asyncio as aioredis
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field, field_validator
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("WEALTHOS_DB_URL", "postgresql://postgres:postgres@localhost:5432/wealthos")
OLLAMA_URL   = os.getenv("OLLAMA_URL", "http://localhost:11434")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
REDIS_URL    = os.getenv("REDIS_URL", "redis://localhost:6379")
CACHE_TTL    = 60 * 15   # 15 minutes

GEN_MODEL    = "llama-3.3-70b-versatile"   # Groq model
OLLAMA_MODEL = "qwen2.5:7b"                # fallback local


def clean_db_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


# ── Pydantic Schemas ───────────────────────────────────────────────────────────

class IncomeStatement(BaseModel):
    total_revenue:      Optional[float] = None   # millions USD
    gross_profit:       Optional[float] = None
    operating_income:   Optional[float] = None
    net_income:         Optional[float] = None
    ebitda:             Optional[float] = None
    fiscal_year:        Optional[int]   = None

class BalanceSheet(BaseModel):
    total_assets:       Optional[float] = None
    total_debt:         Optional[float] = None
    cash_equivalents:   Optional[float] = None
    fiscal_year:        Optional[int]   = None

class CashFlow(BaseModel):
    free_cash_flow:     Optional[float] = None
    operating_cash_flow: Optional[float] = None
    fiscal_year:        Optional[int]   = None

class ValuationMetrics(BaseModel):
    current_price:      Optional[float] = None
    pe_ratio:           Optional[float] = None
    eps_diluted:        Optional[float] = None
    market_cap:         Optional[float] = None
    week_52_high:       Optional[float] = None
    week_52_low:        Optional[float] = None
    price_to_book:      Optional[float] = None
    dividend_yield:     Optional[float] = None

class GrowthMetrics(BaseModel):
    revenue_cagr_3y:    Optional[float] = None   # percentage
    eps_growth_yoy:     Optional[float] = None
    net_income_growth:  Optional[float] = None

class FinancialSnapshot(BaseModel):
    """
    The core output of the Data Agent.
    Every field is either populated from a trusted source or None.
    No field is ever hallucinated.
    """
    ticker:             str
    company_name:       Optional[str]   = None
    sector:             Optional[str]   = None
    analysis_date:      str             = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    income_statement:   IncomeStatement = Field(default_factory=IncomeStatement)
    balance_sheet:      BalanceSheet    = Field(default_factory=BalanceSheet)
    cash_flow:          CashFlow        = Field(default_factory=CashFlow)
    valuation:          ValuationMetrics = Field(default_factory=ValuationMetrics)
    growth:             GrowthMetrics   = Field(default_factory=GrowthMetrics)

    # Qualitative context from RAG
    business_summary:   Optional[str]   = None
    key_risks:          Optional[str]   = None
    management_outlook: Optional[str]   = None

    # Data quality
    data_sources:       list[str]       = Field(default_factory=list)
    missing_fields:     list[str]       = Field(default_factory=list)
    confidence:         str             = "high"   # high / medium / low

    @field_validator("ticker")
    @classmethod
    def uppercase_ticker(cls, v):
        return v.upper()


# ── Redis Cache ────────────────────────────────────────────────────────────────

async def cache_get(redis: aioredis.Redis, ticker: str) -> Optional[FinancialSnapshot]:
    try:
        raw = await redis.get(f"snapshot:{ticker}")
        if raw:
            return FinancialSnapshot(**json.loads(raw))
    except Exception:
        pass
    return None


async def cache_set(redis: aioredis.Redis, ticker: str, snapshot: FinancialSnapshot):
    try:
        await redis.setex(
            f"snapshot:{ticker}",
            CACHE_TTL,
            snapshot.model_dump_json()
        )
    except Exception:
        pass


# ── SQL Fetcher ────────────────────────────────────────────────────────────────

async def fetch_from_db(ticker: str, conn: asyncpg.Connection) -> dict:
    """Pull all metrics from financial_facts for the most recent fiscal year."""
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (metric)
            metric, value, fiscal_year
        FROM financial_facts
        WHERE ticker = $1
        ORDER BY metric, fiscal_year DESC
        """,
        ticker.upper()
    )
    return {r["metric"]: {"value": float(r["value"]), "year": r["fiscal_year"]} for r in rows}


# ── Market Data Fetcher ────────────────────────────────────────────────────────

async def fetch_market_data(ticker: str, client: httpx.AsyncClient) -> dict:
    """Fetch live market data from market_server MCP tools."""
    try:
        from mcp_servers.market_server import get_price, get_financials, get_info
        
        # We can call these directly since they are in the same repo
        price_data = get_price(ticker)
        fin_data   = get_financials(ticker)
        info_data  = get_info(ticker)
        
        return {
            "current_price":  price_data.get("current_price"),
            "pe_ratio":       fin_data.get("pe_ratio"),
            "eps_diluted":    fin_data.get("eps_trailing"),
            "market_cap":     price_data.get("market_cap"),
            "week_52_high":   price_data.get("week_52_high"),
            "week_52_low":    price_data.get("week_52_low"),
            "price_to_book":  None, # not currently in market_server
            "dividend_yield": fin_data.get("dividend_yield"),
            "company_name":   info_data.get("name"),
            "sector":         info_data.get("sector"),
        }
    except Exception as e:
        print(f"[data_agent] Market data fetch failed: {e}")
        return {}


# ── RAG Fetcher ───────────────────────────────────────────────────────────────

async def fetch_from_rag(ticker: str, conn: asyncpg.Connection, client: httpx.AsyncClient) -> dict:
    """Query the RAG pipeline for qualitative context."""
    results = {}

    async def query_rag(question: str, section: str) -> Optional[str]:
        try:
            vector_resp = await client.post(
                f"{OLLAMA_URL}/api/embeddings",
                json={"model": "mxbai-embed-large", "prompt": question},
                timeout=30.0,
            )
            vector = vector_resp.json()["embedding"]
            vector_str = "[" + ",".join(str(v) for v in vector) + "]"

            rows = await conn.fetch(
                """
                SELECT chunk_text
                FROM document_embeddings
                WHERE ticker = $1 AND section = $2
                ORDER BY embedding <=> $3::text::vector
                LIMIT 4
                """,
                ticker.upper(), section, vector_str
            )

            if not rows:
                # fallback — no section filter
                rows = await conn.fetch(
                    """
                    SELECT chunk_text
                    FROM document_embeddings
                    WHERE ticker = $1
                    ORDER BY embedding <=> $2::text::vector
                    LIMIT 3
                    """,
                    ticker.upper(), vector_str
                )

            if not rows:
                return None

            context = "\n\n".join(r["chunk_text"] for r in rows)

            from services.llm_client import call_llm
            answer = await call_llm(
                system="You are a financial analyst. Answer concisely based only on the provided context. Max 150 words.",
                user=f"Context:\n{context}\n\nQuestion: {question}",
                max_tokens=150
            )
            return answer
        except Exception as e:
            print(f"[data_agent] RAG query failed: {e}")
            return None

    results["business_summary"]   = await query_rag(f"What does {ticker} do? Describe the business.", "business")
    results["key_risks"]          = await query_rag(f"What are {ticker}'s main risk factors?", "risk_factors")
    results["management_outlook"] = await query_rag(f"What did management say about future outlook and growth?", "md_and_a")

    return results


# ── Growth Calculator ──────────────────────────────────────────────────────────

async def compute_growth_metrics(ticker: str, conn: asyncpg.Connection) -> GrowthMetrics:
    """Compute growth metrics from historical financial_facts data."""
    try:
        rows = await conn.fetch(
            """
            SELECT value, fiscal_year
            FROM financial_facts
            WHERE ticker = $1 AND metric = 'total_revenue'
            ORDER BY fiscal_year DESC
            LIMIT 4
            """,
            ticker.upper()
        )

        if len(rows) < 2:
            return GrowthMetrics()

        values = [(float(r["value"]), r["fiscal_year"]) for r in rows]

        # YoY growth
        yoy = ((values[0][0] - values[1][0]) / values[1][0] * 100) if values[1][0] else None

        # 3Y CAGR
        cagr = None
        if len(values) >= 4:
            start, end = values[3][0], values[0][0]
            if start and start > 0:
                cagr = ((end / start) ** (1/3) - 1) * 100

        # EPS growth
        eps_rows = await conn.fetch(
            """
            SELECT value, fiscal_year
            FROM financial_facts
            WHERE ticker = $1 AND metric = 'eps_diluted'
            ORDER BY fiscal_year DESC
            LIMIT 2
            """,
            ticker.upper()
        )
        eps_growth = None
        if len(eps_rows) == 2:
            prev = float(eps_rows[1]["value"])
            curr = float(eps_rows[0]["value"])
            if prev and prev != 0:
                eps_growth = ((curr - prev) / abs(prev)) * 100

        return GrowthMetrics(
            revenue_cagr_3y=round(cagr, 2) if cagr else None,
            eps_growth_yoy=round(eps_growth, 2) if eps_growth else None,
            net_income_growth=round(yoy, 2) if yoy else None,
        )
    except Exception as e:
        print(f"[data_agent] Growth calc failed: {e}")
        return GrowthMetrics()


# ── Main Orchestrator ──────────────────────────────────────────────────────────

async def run_data_agent(ticker: str, use_rag: bool = True) -> FinancialSnapshot:
    """
    Main entry point. Called by LangGraph in Phase 4.
    Returns a fully validated FinancialSnapshot.
    """
    start = time.time()
    ticker = ticker.upper()
    print(f"\n{'='*50}")
    print(f"  Data Agent — {ticker}")
    print(f"{'='*50}")

    # ── Step 1: Check Redis cache ─────────────────────────────────────────────
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        cached = await cache_get(redis, ticker)
        if cached:
            print(f"  ✅ Cache hit — returning cached snapshot ({CACHE_TTL//60}min TTL)")
            return cached

        print(f"  Cache miss — fetching fresh data...")
    
        # ── Step 2: Connect to DB and fetch ───────────────────────────────────────
        conn = await asyncpg.connect(clean_db_url(DATABASE_URL))
        data_sources = []
        missing_fields = []
    
        try:
            async with httpx.AsyncClient() as client:
    
                # Fetch from DB
                db_data = await fetch_from_db(ticker, conn)
                if db_data:
                    data_sources.append("financial_facts_db")
                    print(f"  ✅ DB: {len(db_data)} metrics found")
                else:
                    print(f"  ⚠️  DB: No data — run populate_facts.py first")
    
                # Fetch live market data
                market_data = await fetch_market_data(ticker, client)
                if market_data.get("current_price"):
                    data_sources.append("yfinance_live")
                    print(f"  ✅ Market: ${market_data.get('current_price'):.2f}")
                else:
                    print(f"  ⚠️  Market: No live price")
    
                # Fetch RAG context
                rag_data = {}
                if use_rag:
                    rag_data = await fetch_from_rag(ticker, conn, client)
                    if any(rag_data.values()):
                        data_sources.append("rag_pipeline")
                        print(f"  ✅ RAG: qualitative context retrieved")
                    else:
                        print(f"  ⚠️  RAG: No chunks found (run pipeline.py first)")
    
                # Compute growth metrics
                growth = await compute_growth_metrics(ticker, conn)
                if growth.revenue_cagr_3y:
                    print(f"  ✅ Growth: Revenue CAGR 3Y = {growth.revenue_cagr_3y:.1f}%")
    
        finally:
            await conn.close()
    
        # ── Step 3: Build FinancialSnapshot ───────────────────────────────────────
    
        def get_metric(metric: str) -> Optional[float]:
            return db_data.get(metric, {}).get("value")
    
        def get_year(metric: str) -> Optional[int]:
            return db_data.get(metric, {}).get("year")
    
        income = IncomeStatement(
            total_revenue=get_metric("total_revenue"),
            gross_profit=get_metric("gross_profit"),
            operating_income=get_metric("operating_income"),
            net_income=get_metric("net_income"),
            ebitda=get_metric("ebitda"),
            fiscal_year=get_year("total_revenue"),
        )
    
        balance = BalanceSheet(
            total_assets=get_metric("total_assets"),
            total_debt=get_metric("total_debt"),
            cash_equivalents=get_metric("cash_and_equivalents"),
            fiscal_year=get_year("total_assets"),
        )
    
        cashflow = CashFlow(
            free_cash_flow=get_metric("free_cash_flow"),
            operating_cash_flow=get_metric("operating_cash_flow"),
            fiscal_year=get_year("free_cash_flow"),
        )
    
        valuation = ValuationMetrics(
            current_price=market_data.get("current_price"),
            pe_ratio=market_data.get("pe_ratio"),
            eps_diluted=market_data.get("eps_diluted") or get_metric("eps_diluted"),
            market_cap=market_data.get("market_cap"),
            week_52_high=market_data.get("week_52_high"),
            week_52_low=market_data.get("week_52_low"),
            price_to_book=market_data.get("price_to_book"),
            dividend_yield=market_data.get("dividend_yield"),
        )
    
        # Track missing fields
        all_fields = {
            "total_revenue": income.total_revenue,
            "net_income": income.net_income,
            "current_price": valuation.current_price,
            "total_debt": balance.total_debt,
            "free_cash_flow": cashflow.free_cash_flow,
            "pe_ratio": valuation.pe_ratio,
        }
        missing_fields = [k for k, v in all_fields.items() if v is None]
    
        confidence = "high" if len(missing_fields) == 0 else \
                     "medium" if len(missing_fields) <= 2 else "low"
    
        snapshot = FinancialSnapshot(
            ticker=ticker,
            company_name=market_data.get("company_name"),
            sector=market_data.get("sector"),
            income_statement=income,
            balance_sheet=balance,
            cash_flow=cashflow,
            valuation=valuation,
            growth=growth,
            business_summary=rag_data.get("business_summary"),
            key_risks=rag_data.get("key_risks"),
            management_outlook=rag_data.get("management_outlook"),
            data_sources=data_sources,
            missing_fields=missing_fields,
            confidence=confidence,
        )
    
        # ── Step 4: Cache and return ──────────────────────────────────────────────
        await cache_set(redis, ticker, snapshot)
    
        elapsed = round(time.time() - start, 1)
        print(f"\n  Confidence  : {confidence.upper()}")
        print(f"  Missing     : {missing_fields or 'none'}")
        print(f"  Sources     : {data_sources}")
        print(f"  Time        : {elapsed}s")
        print(f"{'='*50}\n")
    
        return snapshot
    finally:
        await redis.aclose()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "TSLA"

    async def main():
        snapshot = await run_data_agent(ticker)
        print("\n── FinancialSnapshot ──────────────────────────────")
        print(f"  Company   : {snapshot.company_name}")
        print(f"  Sector    : {snapshot.sector}")
        print(f"\n  Income Statement (FY{snapshot.income_statement.fiscal_year}):")
        print(f"    Revenue  : ${snapshot.income_statement.total_revenue:,.0f}M" if snapshot.income_statement.total_revenue else "    Revenue  : N/A")
        print(f"    Net Inc  : ${snapshot.income_statement.net_income:,.0f}M" if snapshot.income_statement.net_income else "    Net Inc  : N/A")
        print(f"\n  Valuation:")
        print(f"    Price    : ${snapshot.valuation.current_price:.2f}" if snapshot.valuation.current_price else "    Price    : N/A")
        print(f"    P/E      : {snapshot.valuation.pe_ratio:.1f}x" if snapshot.valuation.pe_ratio else "    P/E      : N/A")
        print(f"\n  Growth:")
        print(f"    Rev CAGR : {snapshot.growth.revenue_cagr_3y:.1f}%" if snapshot.growth.revenue_cagr_3y else "    Rev CAGR : N/A")
        print(f"\n  Business : {(snapshot.business_summary or 'N/A')[:150]}...")
        print(f"  Risks    : {(snapshot.key_risks or 'N/A')[:150]}...")
        print("──────────────────────────────────────────────────\n")

    asyncio.run(main())