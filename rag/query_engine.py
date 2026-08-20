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
COHERE_API_KEY  = os.getenv("COHERE_API_KEY",  "")
WEALTHOS_DB_URL = os.getenv("WEALTHOS_DB_URL", "")

COLLECTION_NAME  = "wealthos_docs"
SENTENCE_MODEL   = "sentence-transformers/all-MiniLM-L6-v2"
DENSE_DIMS       = 384

# Module-level model cache — shared with indexer.py when running in-process
_dense_model = None
_sparse_model = None


def _get_dense_model():
    global _dense_model
    if _dense_model is None:
        from sentence_transformers import SentenceTransformer
        _dense_model = SentenceTransformer(SENTENCE_MODEL)
    return _dense_model


# ── Embedding ─────────────────────────────────────────────────────────────────

def embed_query_dense(text: str) -> list[float]:
    model = _get_dense_model()
    return model.encode([text], normalize_embeddings=True)[0].tolist()


def embed_query_sparse(text: str):
    global _sparse_model
    if _sparse_model is None:
        from fastembed import SparseTextEmbedding
        _sparse_model = SparseTextEmbedding(model_name="Qdrant/bm25")
    return list(_sparse_model.embed([text]))[0]


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


# ── Staleness scoring ─────────────────────────────────────────────────────────

def staleness_score(filing_date_str: str, half_life_days: int) -> float:
    try:
        from datetime import datetime, timezone
        filed    = datetime.fromisoformat(filing_date_str).replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - filed).days
        return round(max(0.1, 0.5 ** (age_days / half_life_days)), 3)
    except Exception:
        return 0.5


def _annotate_staleness(hit: dict) -> str:
    """Return chunk content, appending a stale-data warning when score < 0.5."""
    content       = hit.get("content", "")
    filing_date   = hit.get("filing_date", "")
    half_life     = hit.get("half_life_days", 180)
    if not filing_date:
        return content
    try:
        from datetime import datetime, timezone
        filed    = datetime.fromisoformat(filing_date).replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - filed).days
    except Exception:
        return content
    score = staleness_score(filing_date, half_life)
    if score < 0.5:
        content += f" [⚠️ data may be stale — {age_days}d old, half-life {half_life}d]"
    return content


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
        parts.append(f"[{sec}] {_annotate_staleness(h)}{parent_content}")

    return "\n\n---\n\n".join(parts)


# ── LLM call ──────────────────────────────────────────────────────────────────

async def _call_llm(messages: list[dict]) -> str:
    from services.llm_client import call_llm
    system = next(
        (m["content"] for m in messages if m.get("role") == "system"),
        "You are a financial research assistant.",
    )
    user = next(
        (m["content"] for m in messages if m.get("role") == "user"),
        "",
    )
    return await call_llm(system=system, user=user, max_tokens=800)


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
            context_parts.append(f"[{sec}] {_annotate_staleness(h)}{parent_content}")

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
        Structured function-calling agentic loop. Runs up to 4 tool-call
        rounds using Groq's native `tools` param (was a hand-parsed
        `ACTION:`/`INPUT:` text protocol — silently broke whenever the model
        phrased its action line even slightly differently). Returns final
        answer string.
        """
        from services.llm_client import call_llm, GROQ_MODEL

        MAX_STEPS = 4
        TOOLS = [
            {
                "type": "function",
                "function": {
                    "name": "financial_facts_sql",
                    "description": "Query structured financial metrics (revenue, earnings, ratios) from the financial_facts table.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query_type": {"type": "string", "description": "e.g. 'revenue growth', 'profit margins'"},
                        },
                        "required": ["query_type"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "hybrid_search",
                    "description": "Semantic + keyword hybrid search over SEC filing chunks. Returns relevant prose and table excerpts.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "search_query": {"type": "string"},
                            "section": {"type": "string", "description": "optional, e.g. risk_factors, md_and_a, income_statement"},
                        },
                        "required": ["search_query"],
                    },
                },
            },
        ]

        messages = [
            {"role": "system", "content": f"You are a financial analyst with access to SEC filing data for {ticker}. Use tools to gather evidence before concluding."},
            {"role": "user",   "content": question},
        ]

        for _ in range(MAX_STEPS):
            message = await call_llm(system="", user="", messages=messages, tools=TOOLS, model=GROQ_MODEL, max_tokens=800)
            tool_calls = message.get("tool_calls") if message else None

            if not tool_calls:
                return (message or {}).get("content", "") or ""

            messages.append({
                "role": "assistant",
                "content": message.get("content"),
                "tool_calls": tool_calls,
            })
            for call in tool_calls:
                result = await self._dispatch_tool(call["function"]["name"], call["function"]["arguments"], ticker)
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})

        # Fallback — model didn't converge in MAX_STEPS rounds. Keep the same
        # `tools` schema on this call too: gpt-oss models on Groq have a
        # built-in "browser.search" tool that can fire even with no `tools`
        # param sent, and dropping to a bare system/user call here (as the
        # old ReAct fallback did) triggered a 400 "Tool choice is none, but
        # model called a tool" — verified live. If it still won't give a
        # plain answer, synthesize one from the tool results directly rather
        # than making another round-trip.
        final_messages = messages + [{"role": "user", "content": "Based on all information gathered, provide your final answer now. Do not call any more tools."}]
        message = await call_llm(system="", user="", messages=final_messages, tools=TOOLS, model=GROQ_MODEL, max_tokens=800)
        content = (message or {}).get("content")
        if content:
            return content
        tool_results = [m["content"] for m in messages if m.get("role") == "tool"]
        return "\n\n".join(tool_results) if tool_results else "Unable to reach a conclusion."

    async def _dispatch_tool(self, tool_name: str, arguments_json: str, ticker: str) -> str:
        try:
            tool_input = json.loads(arguments_json)
        except Exception:
            return f"Could not parse arguments for {tool_name}."

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
