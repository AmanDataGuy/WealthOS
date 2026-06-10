# rag/query_engine.py
# Agentic retrieval engine — hybrid Qdrant search + Cohere rerank + parent context
#
# Two public methods:
#   search(question, ticker, section_filter)  — lightweight, used by data_agent
#   query(question, ticker)                   — full ReAct agentic loop (up to 4 steps)

import os
import json
import asyncio
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

QDRANT_URL      = os.getenv("QDRANT_URL",      "http://localhost:6333")
QDRANT_API_KEY  = os.getenv("QDRANT_API_KEY",  "")
VOYAGE_API_KEY  = os.getenv("VOYAGE_API_KEY",  "")
COHERE_API_KEY  = os.getenv("COHERE_API_KEY",  "")
WEALTHOS_DB_URL = os.getenv("WEALTHOS_DB_URL", "")

COLLECTION_NAME = "wealthos_docs"
VOYAGE_MODEL    = "voyage-finance-2"


# ── Embedding ─────────────────────────────────────────────────────────────────

def embed_query_dense(text: str) -> list[float]:
    if not VOYAGE_API_KEY:
        return [0.0] * 1024
    import voyageai
    vc = voyageai.Client(api_key=VOYAGE_API_KEY)
    result = vc.embed([text], model=VOYAGE_MODEL, input_type="query")
    return result.embeddings[0]


def embed_query_sparse(text: str):
    from fastembed import SparseTextEmbedding
    model = SparseTextEmbedding(model_name="Qdrant/bm25")
    return list(model.embed([text]))[0]


# ── Qdrant client ─────────────────────────────────────────────────────────────

def get_qdrant_client():
    from qdrant_client import QdrantClient
    if QDRANT_API_KEY:
        return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    return QdrantClient(url=QDRANT_URL)


# ── Hybrid search (dense + sparse + RRF + Cohere rerank) ─────────────────────

def _hybrid_search_sync(
    question: str,
    ticker: str,
    section_filter: Optional[str] = None,
    top_candidates: int = 20,
    top_k: int = 5,
) -> list[dict]:
    try:
        from qdrant_client.models import (
            Filter, FieldCondition, MatchValue,
            Prefetch, FusionQuery, Fusion, SparseVector,
        )
        client = get_qdrant_client()

        dense_vec  = embed_query_dense(question)
        sparse_vec = embed_query_sparse(question)

        must_filters = [
            FieldCondition(key="ticker",      match=MatchValue(value=ticker)),
            FieldCondition(key="chunk_level", match=MatchValue(value=2)),
        ]
        if section_filter:
            must_filters.append(FieldCondition(key="section", match=MatchValue(value=section_filter)))

        q_filter = Filter(must=must_filters)

        results = client.query_points(
            collection_name=COLLECTION_NAME,
            prefetch=[
                Prefetch(query=dense_vec, using="dense", limit=top_candidates * 2),
                Prefetch(
                    query=SparseVector(
                        indices=sparse_vec.indices.tolist(),
                        values=sparse_vec.values.tolist(),
                    ),
                    using="sparse",
                    limit=top_candidates * 2,
                ),
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            query_filter=q_filter,
            limit=top_candidates,
            with_payload=True,
        )
        hits = [{"id": p.id, **p.payload} for p in results.points]
    except Exception as e:
        print(f"[query_engine] Qdrant hybrid search error: {e}")
        return []

    if not hits:
        return []

    # Cohere rerank
    if COHERE_API_KEY and len(hits) > 1:
        try:
            import cohere
            co = cohere.Client(api_key=COHERE_API_KEY)
            docs = [h["content"] for h in hits]
            reranked = co.rerank(
                query=question,
                documents=docs,
                model="rerank-english-v3.0",
                top_n=top_k,
            )
            hits = [hits[r.index] for r in reranked.results]
        except Exception as e:
            print(f"[query_engine] Cohere rerank error (using raw order): {e}")
            hits = hits[:top_k]
    else:
        hits = hits[:top_k]

    return hits


def _fetch_parents_sync(parent_ids: list[str]) -> list[dict]:
    """Fetch level-1 section parents to give LLM richer context."""
    if not parent_ids:
        return []
    try:
        from qdrant_client.models import Filter, HasIdCondition
        client = get_qdrant_client()
        results = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(must=[HasIdCondition(has_id=parent_ids)]),
            limit=len(parent_ids),
            with_payload=True,
            with_vectors=False,
        )
        return [{"id": p.id, **p.payload} for p in results[0]]
    except Exception as e:
        print(f"[query_engine] Parent fetch error: {e}")
        return []


# ── SQL tool (unchanged — still hits Postgres financial_facts) ────────────────

async def _tool_sql(ticker: str, question: str) -> str:
    if not WEALTHOS_DB_URL:
        return "Database not configured."
    try:
        import asyncpg
        conn = await asyncpg.connect(WEALTHOS_DB_URL)
        try:
            rows = await conn.fetch(
                """
                SELECT metric, value, period, unit
                FROM   financial_facts
                WHERE  ticker = $1
                ORDER BY period DESC
                LIMIT  30
                """,
                ticker,
            )
            if not rows:
                return f"No financial facts found for {ticker}."
            return "\n".join(f"{r['metric']}: {r['value']} {r['unit']} ({r['period']})" for r in rows)
        finally:
            await conn.close()
    except Exception as e:
        return f"SQL error: {e}"


# ── Vector search tool (hybrid, wraps _hybrid_search_sync) ───────────────────

async def _tool_hybrid_search(
    question: str,
    ticker: str,
    section: Optional[str] = None,
) -> str:
    hits = await asyncio.to_thread(_hybrid_search_sync, question, ticker, section)
    if not hits:
        return "No relevant filing chunks found."

    parent_ids = list({h.get("parent_id") for h in hits if h.get("parent_id")})
    parents    = await asyncio.to_thread(_fetch_parents_sync, parent_ids)
    parents_by_id = {p["id"]: p for p in parents}

    parts = []
    for h in hits:
        sec = h.get("section", "unknown")
        parent_content = ""
        if h.get("parent_id") and h["parent_id"] in parents_by_id:
            parent_content = f"\n[Section context]: {parents_by_id[h['parent_id']]['content'][:500]}"
        parts.append(f"[{sec}] {h['content']}{parent_content}")

    return "\n\n---\n\n".join(parts)


# ── LLM call ──────────────────────────────────────────────────────────────────

async def _call_llm(messages: list[dict]) -> str:
    from services.llm_client import call_llm
    return await call_llm(messages)


# ── FilingQueryEngine ─────────────────────────────────────────────────────────

class FilingQueryEngine:

    async def search(
        self,
        question: str,
        ticker: str,
        section_filter: Optional[str] = None,
    ) -> Optional[str]:
        """
        Lightweight single-shot retrieval for data_agent.
        Returns synthesized answer string, or None on empty results.
        """
        hits = await asyncio.to_thread(_hybrid_search_sync, question, ticker, section_filter)
        if not hits:
            return None

        parent_ids = list({h.get("parent_id") for h in hits if h.get("parent_id")})
        parents    = await asyncio.to_thread(_fetch_parents_sync, parent_ids)
        parents_by_id = {p["id"]: p for p in parents}

        context_parts = []
        for h in hits:
            sec = h.get("section", "unknown")
            parent_content = ""
            if h.get("parent_id") and h["parent_id"] in parents_by_id:
                parent_content = f"\n{parents_by_id[h['parent_id']]['content'][:400]}"
            context_parts.append(f"[{sec}] {h['content']}{parent_content}")

        context = "\n\n".join(context_parts)
        prompt = [
            {"role": "system", "content": "You are a financial analyst. Answer strictly from the provided context. Be factual and concise."},
            {"role": "user",   "content": f"Context from {ticker} SEC filings:\n\n{context}\n\nQuestion: {question}"},
        ]
        try:
            return await _call_llm(prompt)
        except Exception as e:
            return f"Synthesis error: {e}"

    async def query(self, question: str, ticker: str) -> str:
        """
        Full ReAct agentic loop. Runs up to 4 reasoning steps.
        Returns final answer string.
        """
        MAX_STEPS = 4
        TOOL_DEFINITIONS = [
            {
                "name": "financial_facts_sql",
                "description": "Query structured financial metrics (revenue, earnings, ratios) from the financial_facts table.",
                "parameters": {"query_type": "string (e.g. 'revenue growth', 'profit margins')"},
            },
            {
                "name": "hybrid_search",
                "description": "Semantic + keyword hybrid search over SEC filing chunks. Returns relevant prose and table excerpts.",
                "parameters": {"search_query": "string", "section": "optional string (e.g. risk_factors, md_and_a, income_statement)"},
            },
        ]

        system_prompt = f"""You are a financial analyst with access to SEC filing data for {ticker}.

Available tools:
{json.dumps(TOOL_DEFINITIONS, indent=2)}

To use a tool respond with:
ACTION: <tool_name>
INPUT: <json input>

When you have enough information respond with:
FINAL ANSWER: <your answer>

Be methodical. Use tools to gather evidence before concluding."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": question},
        ]

        for _ in range(MAX_STEPS):
            response = await _call_llm(messages)
            messages.append({"role": "assistant", "content": response})

            if "FINAL ANSWER:" in response:
                return response.split("FINAL ANSWER:", 1)[1].strip()

            if "ACTION:" not in response:
                return response

            tool_result = await self._dispatch_tool(response, ticker)
            messages.append({"role": "user", "content": f"Tool result:\n{tool_result}"})

        # Fallback — ask for synthesis from accumulated context
        messages.append({"role": "user", "content": "Based on all information gathered, provide your final answer now."})
        return await _call_llm(messages)

    async def _dispatch_tool(self, llm_response: str, ticker: str) -> str:
        try:
            action_line = [l for l in llm_response.splitlines() if l.strip().startswith("ACTION:")][0]
            tool_name   = action_line.split("ACTION:", 1)[1].strip()
            input_line  = [l for l in llm_response.splitlines() if l.strip().startswith("INPUT:")][0]
            tool_input  = json.loads(input_line.split("INPUT:", 1)[1].strip())
        except Exception:
            return "Could not parse tool call. Respond with FINAL ANSWER or try again."

        if tool_name == "financial_facts_sql":
            return await _tool_sql(ticker, tool_input.get("query_type", ""))
        elif tool_name == "hybrid_search":
            return await _tool_hybrid_search(
                tool_input.get("search_query", ""),
                ticker,
                tool_input.get("section"),
            )
        else:
            return f"Unknown tool: {tool_name}"


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python rag/query_engine.py <ticker> <question>")
        sys.exit(1)
    engine = FilingQueryEngine()
    answer = asyncio.run(engine.query(sys.argv[2], sys.argv[1]))
    print(answer)
