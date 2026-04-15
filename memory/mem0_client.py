# memory/mem0_client.py
"""
Mem0 long-term memory for WealthOS.

Two functions used by the graph:
  read_memory(user_id)          → called at start of finance_node
  write_memory(user_id, result) → called at end of writer_node

Mem0 handles vector storage, deduplication, and retrieval automatically.
All we do is add and search.

## Phase 7 addition
Both functions are wrapped with @track_memory_op so AgentOps
captures how many memories were found and how long each op took.
"""

import os
from mem0 import MemoryClient
from dotenv import load_dotenv

# Phase 7 — track memory ops in AgentOps
from observability import track_memory_op

load_dotenv()

_client = None

def get_client() -> MemoryClient:
    global _client
    if _client is None:
        _client = MemoryClient(api_key=os.getenv("MEM0_API_KEY"))
    return _client


@track_memory_op("read")
def read_memory(user_id: str) -> str:
    """
    Pull everything Mem0 knows about this user.
    Returns a plain string injected into state as user_memory.
    Returns empty string if no memories exist yet (new user).
    """
    try:
        client = get_client()
        memories = client.search(
            query="financial analysis investment risk portfolio",
            filters={"user_id": user_id},
            limit=10,
        )
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


@track_memory_op("write")
def write_memory(user_id: str, state: dict) -> None:
    """
    Store the key outcomes of this analysis in Mem0.
    Called after writer_node completes.
    """
    try:
        client = get_client()

        ticker   = state.get("tickers", ["Unknown"])[0]
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

        messages = [
            {
                "role": "user",
                "content": f"Analyzed {ticker}. My monthly surplus was ₹{surplus}, health score {health}/100."
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