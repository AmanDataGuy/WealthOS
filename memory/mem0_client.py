# memory/mem0_client.py
"""
Mem0 long-term memory for WealthOS.

Two functions used by the graph:
  read_memory(user_id)  → called at start of finance_node
  write_memory(user_id, result) → called at end of writer_node

Mem0 handles vector storage, deduplication, and retrieval automatically.
All we do is add and search.
"""

import os
from mem0 import MemoryClient
from dotenv import load_dotenv

load_dotenv()

_client = None

def get_client() -> MemoryClient:
    global _client
    if _client is None:
        _client = MemoryClient(api_key=os.getenv("MEM0_API_KEY"))
    return _client


def read_memory(user_id: str, query: str = "") -> str:
    """
    Pull what Mem0 knows about this user that's relevant to their current
    question. Returns a plain string injected into state as user_memory.
    Returns empty string if no memories exist yet (new user).

    Args:
        user_id: whose memory to search
        query:   the user's actual current question, if available — searches
                 for "your history with semiconductor stocks" instead of a
                 generic finance query when the user is asking about one.
                 Falls back to a generic query if not provided (e.g. a
                 cold-start call with no question yet).
    """
    try:
        client = get_client()
        response = client.search(
            query=query or "financial analysis investment risk portfolio",
            filters={"user_id": user_id},
            limit=10,
        )
        # client.search() returns {"results": [...]}, not a bare list — this
        # was previously unwrapped incorrectly (`for m in memories` iterated
        # over the dict's keys, i.e. the single string "results", so every
        # real memory call before this fix silently returned garbage instead
        # of actual content). Handle both shapes defensively in case the SDK
        # ever reverts to returning a bare list.
        memories = response.get("results", []) if isinstance(response, dict) else response
        if not memories:
            return ""

        lines = []
        for m in memories:
            text = m.get("memory", "") if isinstance(m, dict) else str(m)
            if text:
                lines.append(f"- {text}")

        if not lines:
            return ""

        result = "\n".join(lines)
        print(f"  [mem0] Retrieved {len(lines)} memories for {user_id}")
        return result

    except Exception as e:
        # Memory failure should never block the pipeline
        print(f"  [mem0] ⚠️  read failed: {e}")
        return ""


def write_memory(user_id: str, state: dict) -> None:
    """
    Store the key outcomes of this analysis in Mem0.
    Called after writer_node completes.
    """
    try:
        client = get_client()

        ticker   = state.get("tickers", ["Unknown"])[0]
        query    = state.get("query", "")
        risk     = state.get("risk_report") or {}
        personal = state.get("personal_finance") or {}
        memo     = state.get("final_memo", "")

        verdict    = risk.get("recommendation", "Unknown")
        risk_score = risk.get("risk_score", "N/A")
        surplus    = personal.get("monthly_surplus", "N/A")
        health     = (personal.get("health_score") or {}).get("total", "N/A")

        # Pull the first 300 chars of the final verdict section from memo
        verdict_excerpt = ""
        if "Final Verdict" in memo:
            verdict_excerpt = memo.split("Final Verdict")[-1][:200].strip()

        # Include the actual question asked — real qualitative signal about
        # stated intent (e.g. "quick trade" vs "10-year hold"), not just the
        # decision outcome that's already logged in Qdrant's user_analyses.
        user_content = f"Analyzed {ticker}. My monthly surplus was ₹{surplus}, health score {health}/100."
        if query:
            user_content = f'{user_content} I asked: "{query}"'

        messages = [
            {
                "role": "user",
                "content": user_content
            },
            {
                "role": "assistant",
                "content": (
                    f"WealthOS verdict for {ticker}: {verdict} "
                    f"(risk score {risk_score}/10). {verdict_excerpt}"
                )
            }
        ]

        client.add(messages, user_id=user_id)
        print(f"  [mem0] ✅ Saved analysis memory for {user_id} — {ticker} → {verdict}")

    except Exception as e:
        print(f"  [mem0] ⚠️  write failed: {e}")