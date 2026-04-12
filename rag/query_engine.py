# rag/query_engine.py
"""
Query Engine
============
The main entry point for all RAG queries in WealthOS.

How it works
------------
1. Router reads the question → picks a route
2. Route 2 (SQL)         → hits financial_facts table directly, exact number
3. Route 3 (section_rag) → filters chunks by section, then vector search
4. Route 1 (vector)      → searches all chunks, default behaviour

No hardcoded numbers anywhere. Each route is built for what it's good at.
"""

import os
import asyncio
from dotenv import load_dotenv

import asyncpg
import httpx

load_dotenv()

# ── Settings ──────────────────────────────────────────────────────────────────

DB_URL      = os.getenv("WEALTHOS_DB_URL", "postgresql://postgres:wealth123@localhost:5432/wealthos")
OLLAMA_URL  = os.getenv("OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = "mxbai-embed-large"
GEN_MODEL   = "qwen2.5:7b"


# ── Helpers ───────────────────────────────────────────────────────────────────

def clean_db_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def embed(text: str, client: httpx.AsyncClient) -> list[float]:
    """Turn a text string into a vector using Ollama."""
    resp = await client.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60.0,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


async def generate(question: str, chunks: list[dict], client: httpx.AsyncClient) -> str:
    """Send question + context chunks to qwen2.5:7b and get a grounded answer."""

    # build the context block the LLM will read
    context = "\n\n---\n\n".join(
        f"[Source {i+1} | {c['ticker']} {c['doc_type']}]\n{c['chunk_text']}"
        for i, c in enumerate(chunks)
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are a financial analyst. Answer using ONLY the provided context.\n\n"
                "RULES:\n"
                "1. Never use outside knowledge — only what is in the sources.\n"
                "2. When a table shows two years side by side, LEFT = earlier year, RIGHT = later year.\n"
                "3. Always cite which Source number you got each figure from.\n"
                "4. Never use a number from one company to answer about a different company.\n"
                "5. If the answer is not in any source, say so clearly.\n"
            ),
        },
        {
            "role": "user",
            "content": (
                f"CONTEXT:\n{context}\n\n"
                f"QUESTION: {question}\n\n"
                "Answer directly with exact figures and source citations."
            ),
        },
    ]

    resp = await client.post(
        f"{OLLAMA_URL}/api/chat",
        json={"model": GEN_MODEL, "messages": messages, "stream": False},
        timeout=120.0,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"].strip()


# ── Route handlers ────────────────────────────────────────────────────────────

async def route_sql(question: str, ticker: str, conn: asyncpg.Connection) -> dict:
    """
    Route 2 — SQL on financial_facts table.

    Maps common question words to metric names in the DB,
    then does a direct SELECT. No LLM involved for the number itself.

    Example: "What is Tesla's revenue?" → SELECT where metric = 'total_revenue'
    """

    # map question keywords → DB metric names
    metric_map = {
        "revenue":          "total_revenue",
        "net sales":        "total_revenue",
        "net income":       "net_income",
        "profit":           "net_income",
        "operating income": "operating_income",
        "gross profit":     "gross_profit",
        "assets":           "total_assets",
        "liabilities":      "total_liabilities",
        "debt":             "long_term_debt",
        "cash":             "cash_and_equivalents",
        "cash flow":        "operating_cash_flow",
        "capital expenditure": "capital_expenditure",
        "r&d":              "research_and_development",
    }

    q       = question.lower()
    metric  = next((v for k, v in metric_map.items() if k in q), None)

    # try to detect fiscal year from question (e.g. "FY2024", "2024", "fiscal 2024")
    import re
    year_match = re.search(r"20\d{2}", question)
    fiscal_year = int(year_match.group()) if year_match else None

    if not metric:
        # can't map to a metric — fall back to vector search
        return None

    # build query — with or without year filter
    if fiscal_year:
        rows = await conn.fetch(
            """
            SELECT metric, value, fiscal_year, unit
            FROM financial_facts
            WHERE ticker = $1 AND metric = $2 AND fiscal_year = $3
            ORDER BY fiscal_year DESC
            LIMIT 1
            """,
            ticker, metric, fiscal_year,
        )
    else:
        # no year mentioned → return most recent
        rows = await conn.fetch(
            """
            SELECT metric, value, fiscal_year, unit
            FROM financial_facts
            WHERE ticker = $1 AND metric = $2
            ORDER BY fiscal_year DESC
            LIMIT 1
            """,
            ticker, metric,
        )

    if not rows:
        return {
            "answer":  f"No data found for {ticker} {metric} in financial_facts. Try re-running edgar_ingestor.py.",
            "sources": [],
            "route":   "sql",
        }

    row    = rows[0]
    answer = (
        f"{ticker} {row['metric'].replace('_', ' ').title()} "
        f"FY{row['fiscal_year']}: "
        f"${row['value']:,.0f}M "
        f"(Source: SEC EDGAR financial_facts table)"
    )

    return {
        "answer":  answer,
        "sources": [dict(row)],
        "route":   "sql",
    }


async def route_vector(
    question: str,
    ticker: str | None,
    conn: asyncpg.Connection,
    client: httpx.AsyncClient,
    section: str | None = None,
    top_k: int = 10,
) -> dict:
    """
    Route 1 (vector) and Route 3 (section_rag) share this function.

    Pass section='risk_factors' etc. for section-aware filtering.
    Pass section=None for full document search.
    """

    # embed the question
    vector      = await embed(question, client)
    vector_str  = "[" + ",".join(str(v) for v in vector) + "]"

    # build query — filter by ticker and optionally by section
    if ticker and section:
        rows = await conn.fetch(
            """
            SELECT chunk_text, ticker, doc_type, metadata,
                   1 - (embedding <=> $1::vector) AS score
            FROM document_embeddings
            WHERE ticker = $2 AND section = $3
            ORDER BY embedding <=> $1::vector
            LIMIT $4
            """,
            vector_str, ticker, section, top_k,
        )
    elif ticker:
        rows = await conn.fetch(
            """
            SELECT chunk_text, ticker, doc_type, metadata,
                   1 - (embedding <=> $1::vector) AS score
            FROM document_embeddings
            WHERE ticker = $2
            ORDER BY embedding <=> $1::vector
            LIMIT $3
            """,
            vector_str, ticker, top_k,
        )
    else:
        rows = await conn.fetch(
            """
            SELECT chunk_text, ticker, doc_type, metadata,
                   1 - (embedding <=> $1::vector) AS score
            FROM document_embeddings
            ORDER BY embedding <=> $1::vector
            LIMIT $2
            """,
            vector_str, top_k,
        )

    if not rows:
        return {
            "answer":  "No relevant chunks found. Have you indexed filings for this ticker?",
            "sources": [],
            "route":   "section_rag" if section else "vector",
        }

    chunks = [
        {
            "chunk_text": r["chunk_text"],
            "ticker":     r["ticker"],
            "doc_type":   r["doc_type"],
            "score":      round(float(r["score"]), 3),
        }
        for r in rows
    ]

    answer = await generate(question, chunks, client)

    return {
        "answer":  answer,
        "sources": chunks,
        "route":   "section_rag" if section else "vector",
    }


def detect_section(question: str) -> str | None:
    """
    Maps question intent to a section tag in document_embeddings.
    Returns None if no specific section is relevant.
    """
    q = question.lower()

    if any(w in q for w in ["risk", "risks", "uncertainty"]):
        return "risk_factors"
    if any(w in q for w in ["management", "md&a", "discuss", "ceo", "outlook"]):
        return "md_and_a"
    if any(w in q for w in ["revenue", "income", "profit", "sales"]):
        return "income_statement"

    return None


# ── Main engine class ─────────────────────────────────────────────────────────

class FilingQueryEngine:

    def __init__(self):
        self.db_url = clean_db_url(DB_URL)

    async def query(
        self,
        question: str,
        ticker: str | None = None,
        top_k: int = 10,
    ) -> dict:
        """
        Main query method. Called by agents and the CLI.

        Steps
        -----
        1. Router decides the route
        2. SQL route  → direct DB lookup, return exact number
        3. Section RAG → vector search within one section
        4. Vector     → vector search across all chunks
        5. Log the query for debugging
        """

        # import here to avoid circular imports
        from rag.router import route

        conn = await asyncpg.connect(self.db_url)

        try:
            async with httpx.AsyncClient() as client:

                # ── Step 1: decide route ───────────────────────────────────
                chosen_route = route(question)
                print(f"[query_engine] Route: {chosen_route} | Q: {question[:60]}")

                # ── Step 2: SQL route ──────────────────────────────────────
                if chosen_route == "sql" and ticker:
                    result = await route_sql(question, ticker, conn)

                    # if SQL couldn't map to a metric, fall back to vector
                    if result is not None:
                        result["question"] = question
                        result["ticker"]   = ticker
                        await log_query(conn, question, ticker, result["route"], result["answer"])
                        return result

                # ── Step 3: Section RAG ────────────────────────────────────
                if chosen_route == "section_rag":
                    section = detect_section(question)
                    result  = await route_vector(question, ticker, conn, client, section=section, top_k=top_k)
                    result["question"] = question
                    result["ticker"]   = ticker
                    await log_query(conn, question, ticker, result["route"], result["answer"])
                    return result

                # ── Step 4: Default vector search ──────────────────────────
                result = await route_vector(question, ticker, conn, client, top_k=top_k)
                result["question"] = question
                result["ticker"]   = ticker
                await log_query(conn, question, ticker, result["route"], result["answer"])
                return result

        finally:
            await conn.close()


async def log_query(conn, question, ticker, route_used, answer):
    """Save every query to query_logs for debugging and demo purposes."""
    try:
        await conn.execute(
            """
            INSERT INTO query_logs (question, ticker, route_used, answer)
            VALUES ($1, $2, $3, $4)
            """,
            question, ticker, route_used, answer[:500],
        )
    except Exception:
        pass  # logging should never break the main flow


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    question = sys.argv[1] if len(sys.argv) > 1 else "What is the company's total revenue?"
    ticker   = sys.argv[2] if len(sys.argv) > 2 else None

    async def main():
        engine = FilingQueryEngine()
        result = await engine.query(question, ticker=ticker)

        print(f"\n{'═' * 60}")
        print(f"  Q : {result['question']}")
        print(f"  🗺  Route  : {result.get('route', 'unknown')}")
        print(f"  🏷  Ticker : {result['ticker']}")
        print(f"{'─' * 60}")
        print(f"  A : {result['answer']}")
        print(f"{'─' * 60}")

        sources = result.get("sources", [])
        if sources and isinstance(sources[0], dict) and "chunk_text" in sources[0]:
            print(f"\n  Sources ({len(sources)}):")
            for i, s in enumerate(sources):
                print(f"    [{i+1}] {s['ticker']} {s['doc_type']} | score: {s.get('score', 'N/A')}")
                print(f"         {s['chunk_text'][:100]}...")
        print()

    asyncio.run(main())