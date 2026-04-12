# rag/query_engine.py
"""
Custom Agentic RAG — no LlamaIndex dependency.
Uses a ReAct (Reason + Act) loop driven by qwen2.5:7b via Ollama.

Tools available to the agent:
  1. financial_facts_sql  — exact numbers from financial_facts table
  2. vector_search        — semantic search over document_embeddings
  3. section_search       — vector search filtered by section name
"""

import os
import json
import asyncio
import time
import asyncpg
import httpx
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("WEALTHOS_DB_URL", "postgresql://postgres:postgres@localhost:5432/wealthos")
OLLAMA_URL   = os.getenv("OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL  = "mxbai-embed-large"
GEN_MODEL    = "qwen2.5:7b"


def clean_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


# ── Embedding ─────────────────────────────────────────────────────────────────

async def embed(text: str, client: httpx.AsyncClient) -> list[float]:
    resp = await client.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60.0,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


# ── LLM call ──────────────────────────────────────────────────────────────────

async def llm(prompt: str, client: httpx.AsyncClient, system: str = "") -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    resp = await client.post(
        f"{OLLAMA_URL}/api/chat",
        json={"model": GEN_MODEL, "messages": messages, "stream": False},
        timeout=120.0,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"].strip()


# ── Tool implementations ───────────────────────────────────────────────────────

async def tool_sql(ticker: str, metric: str, conn: asyncpg.Connection) -> str:
    """Query financial_facts for an exact number."""
    # normalize metric name
    metric_map = {
        "revenue": "total_revenue", "total revenue": "total_revenue",
        "net income": "net_income", "earnings": "net_income",
        "gross profit": "gross_profit", "operating income": "operating_income",
        "ebitda": "ebitda", "free cash flow": "free_cash_flow",
        "total assets": "total_assets", "total debt": "total_debt",
        "cash": "cash_and_equivalents", "eps": "eps_diluted",
        "eps basic": "eps_basic", "eps diluted": "eps_diluted",
    }
    metric_key = metric_map.get(metric.lower().strip(), metric.lower().strip().replace(" ", "_"))

    rows = await conn.fetch(
        """
        SELECT metric, value, fiscal_year, unit
        FROM financial_facts
        WHERE ticker = $1 AND metric = $2
        ORDER BY fiscal_year DESC
        LIMIT 5
        """,
        ticker.upper(), metric_key
    )

    if not rows:
        # try partial match
        rows = await conn.fetch(
            """
            SELECT metric, value, fiscal_year, unit
            FROM financial_facts
            WHERE ticker = $1 AND metric ILIKE $2
            ORDER BY fiscal_year DESC
            LIMIT 5
            """,
            ticker.upper(), f"%{metric_key}%"
        )

    if not rows:
        return f"No data found for {ticker} metric '{metric}' in financial_facts table."

    lines = [f"{ticker} {r['metric']} FY{r['fiscal_year']}: ${r['value']:,.0f}M" for r in rows]
    return "\n".join(lines)


async def tool_vector_search(
    question: str,
    ticker: str,
    conn: asyncpg.Connection,
    client: httpx.AsyncClient,
    section: str | None = None,
    top_k: int = 6,
) -> str:
    """Semantic search over document_embeddings, optionally filtered by section."""
    vector = await embed(question, client)
    vector_str = "[" + ",".join(str(v) for v in vector) + "]"

    if section:
        rows = await conn.fetch(
            """
            SELECT chunk_text, section,
                   1 - (embedding <=> $1::text::vector) AS score
            FROM document_embeddings
            WHERE ticker = $2 AND section = $3
            ORDER BY embedding <=> $1::text::vector
            LIMIT $4
            """,
            vector_str, ticker.upper(), section, top_k
        )
        # fallback to full vector if section has no chunks
        if not rows:
            section = None

    if not section:
        rows = await conn.fetch(
            """
            SELECT chunk_text, section,
                   1 - (embedding <=> $1::text::vector) AS score
            FROM document_embeddings
            WHERE ticker = $2
            ORDER BY embedding <=> $1::text::vector
            LIMIT $3
            """,
            vector_str, ticker.upper(), top_k
        )

    if not rows:
        return f"No filing chunks found for {ticker}. Has this ticker been indexed?"

    chunks = "\n\n---\n\n".join(
        f"[Section: {r['section']} | Score: {float(r['score']):.3f}]\n{r['chunk_text']}"
        for r in rows
    )
    return chunks


# ── ReAct Agent Loop ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a financial research assistant with access to tools.
Use the ReAct format — reason step by step, then call ONE tool at a time.

Available tools:
1. financial_facts_sql(metric) — get exact financial numbers (revenue, net income, EPS, debt, cash flow, assets)
2. vector_search(query) — semantic search over SEC filing text (strategy, products, general info)
3. section_search(query, section) — search a specific filing section. Sections: md_and_a, risk_factors, income_statement, balance_sheet, cash_flow

Format your response EXACTLY like this:
Thought: [your reasoning about what to do next]
Action: tool_name
Input: {"key": "value"}

When you have enough information to answer, respond EXACTLY like this:
Thought: I now have enough information to answer.
Final Answer: [your complete answer]

Rules:
- ALWAYS use financial_facts_sql for specific numbers (revenue, income, EPS, debt)
- Use section_search with section=md_and_a for management commentary and outlook
- Use section_search with section=risk_factors for risks and threats
- Use vector_search for general qualitative questions
- You may call multiple tools before giving Final Answer
- Never hallucinate numbers — only state figures returned by financial_facts_sql
"""


async def react_agent(
    question: str,
    ticker: str,
    conn: asyncpg.Connection,
    client: httpx.AsyncClient,
    max_iterations: int = 6,
) -> str:
    """Run the ReAct loop until Final Answer or max iterations."""

    conversation = f"Company: {ticker}\nQuestion: {question}"
    tool_results = []
    seen_calls = set()          

    for i in range(max_iterations):
        # Build prompt with all tool results so far
        full_prompt = conversation
        if tool_results:
            full_prompt += "\n\n" + "\n\n".join(tool_results)
        full_prompt += "\n\nWhat do you do next?"

        response = await llm(full_prompt, client, system=SYSTEM_PROMPT)

        print(f"\n[agent] Iteration {i+1}:")
        print(response[:300])

        # Check for Final Answer
        if "Final Answer:" in response:
            final = response.split("Final Answer:")[-1].strip()
            return final

        # Parse tool call
        try:
            action_line = [l for l in response.splitlines() if l.startswith("Action:")][0]
            input_line  = [l for l in response.splitlines() if l.startswith("Input:")][0]

            tool_name = action_line.replace("Action:", "").strip()
            tool_input = json.loads(input_line.replace("Input:", "").strip())
        except (IndexError, json.JSONDecodeError):
            # LLM didn't follow format — ask it to try again
            tool_results.append("System: Your last response didn't follow the required format. Please use Thought/Action/Input format or give a Final Answer.")
            continue

        # Dedup — skip if same tool+input already called
        call_sig = f"{tool_name}:{json.dumps(tool_input, sort_keys=True)}"
        if call_sig in seen_calls:
            tool_results.append(
                f"System: You already called {tool_name} with that exact input. "
                f"Use the results you already have, or give a Final Answer."
            )
            continue
        seen_calls.add(call_sig)

        # Execute tool
        print(f"[agent] Calling tool: {tool_name} with {tool_input}")

        if tool_name == "financial_facts_sql":
            metric = tool_input.get("metric", "total_revenue")
            result = await tool_sql(ticker, metric, conn)

        elif tool_name == "vector_search":
            query = tool_input.get("query", question)
            result = await tool_vector_search(question=query, ticker=ticker, conn=conn, client=client)

        elif tool_name == "section_search":
            query   = tool_input.get("query", question)
            section = tool_input.get("section", "md_and_a")
            result  = await tool_vector_search(question=query, ticker=ticker, conn=conn, client=client, section=section)

        else:
            result = f"Unknown tool: {tool_name}. Available tools: financial_facts_sql, vector_search, section_search"

        tool_results.append(f"Tool: {tool_name}\nResult:\n{result}")

    # Max iterations hit — ask LLM to summarize what it has
    summary_prompt = (
        f"Question: {question}\n\n"
        f"Here is the information collected:\n\n"
        + "\n\n".join(tool_results)
        + "\n\nPlease give your best answer based on the above."
    )
    return await llm(summary_prompt, client)


# ── Main engine ───────────────────────────────────────────────────────────────

class FilingQueryEngine:

    async def query(self, question: str, ticker: str | None = None) -> dict:
        start = time.time()
        db_url = clean_url(DATABASE_URL)

        conn = await asyncpg.connect(db_url)
        try:
            async with httpx.AsyncClient() as client:
                if ticker:
                    answer = await react_agent(question, ticker, conn, client)
                else:
                    # No ticker — pure vector search
                    answer = await tool_vector_search(question, "", conn, client)
        finally:
            await conn.close()

        latency_ms = int((time.time() - start) * 1000)

        # Log to query_logs
        try:
            conn2 = await asyncpg.connect(db_url)
            await conn2.execute(
                "INSERT INTO query_logs (question, ticker, route_used, answer, latency_ms) VALUES ($1,$2,$3,$4,$5)",
                question, ticker, "agentic", answer[:500], latency_ms
            )
            await conn2.close()
        except Exception:
            pass

        return {
            "question":   question,
            "ticker":     ticker,
            "answer":     answer,
            "route":      "agentic",
            "latency_ms": latency_ms,
        }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    question = sys.argv[1] if len(sys.argv) > 1 else "What is the company's total revenue?"
    ticker   = sys.argv[2] if len(sys.argv) > 2 else None

    async def main():
        engine = FilingQueryEngine()
        result = await engine.query(question, ticker=ticker)

        print(f"\n{'═' * 60}")
        print(f"  Q        : {result['question']}")
        print(f"  Ticker   : {result['ticker']}")
        print(f"  Latency  : {result['latency_ms']}ms")
        print(f"{'─' * 60}")
        print(f"  A : {result['answer']}")
        print(f"{'═' * 60}\n")

    asyncio.run(main())