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

    # ── Router classifications ─────────────────────────────
    investment_horizon: Optional[str]   # "short" | "mid" | "long" — set by router
    fetch_plan:         Optional[dict]  # {"use_technicals": bool, ...} — set by router

    # ── Phase 6: Mem0 long-term memory ─────────────────────
    user_memory:    Optional[str]   # injected at start of finance_node

    # ── Past decisions context (Qdrant user_analyses) ──────
    past_decisions_ctx: Optional[str]  # 3 most recent analyses for this user

    # ── Agent outputs ───────────────────────────────────────
    personal_finance:       Optional[dict]   # Finance Agent
    financial_snapshot:     Optional[dict]   # Data Agent
    research_output:        Optional[dict]   # Research Agent
    risk_report:            Optional[dict]   # Risk Agent
    code_output:            Optional[dict]   # Code Agent
    rebalance_suggestion:   Optional[dict]   # Rebalancing Agent
    final_memo:             Optional[str]    # Writer Agent

    # ── Control ─────────────────────────────────────────────
    error:          Optional[str]
    messages:       list[str]     # execution log — what ran and when