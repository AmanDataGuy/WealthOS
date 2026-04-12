# graph/state.py
"""
WealthOS shared state — flows through every LangGraph node.
Every agent reads from this and writes back to it.
"""

from typing import Optional, TypedDict


class WealthOSState(TypedDict):
    # ── Input ──────────────────────────────────────────────
    query:          str
    tickers:        list[str]
    user_id:        str

    # ── Agent outputs ───────────────────────────────────────
    personal_finance:       Optional[dict]   # Finance Agent
    financial_snapshot:     Optional[dict]   # Data Agent
    research_output:        Optional[dict]   # Research Agent (stub for now)
    risk_report:            Optional[dict]   # Risk Agent
    code_output:            Optional[dict]   # Code Agent
    rebalance_suggestion:   Optional[dict]   # Rebalancing Agent
    final_memo:             Optional[str]    # Writer Agent

    # ── Control ─────────────────────────────────────────────
    error:          Optional[str]
    messages:       list[str]     # execution log — what ran and when