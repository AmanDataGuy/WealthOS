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
    invest_amount:  Optional[float]  # amount the user wants to invest, INR

    # ── Router classifications ─────────────────────────────
    investment_horizon: Optional[str]   # "short" | "mid" | "long" — set by router
    fetch_plan:         Optional[dict]  # {"use_technicals": bool, ...} — set by router

    # ── Past decisions context (Qdrant user_analyses) ──────
    # Mem0 was removed (deep-dive audit — confirmed redundant with this
    # exact collection, see api/main.py's get_memory() docstring).
    past_decisions_ctx: Optional[str]  # recent analyses for this user, deterministic on current ticker + semantic fallback

    # ── Agent outputs ───────────────────────────────────────
    personal_finance:       Optional[dict]   # Finance Agent
    financial_snapshot:     Optional[dict]   # Data Agent
    research_output:        Optional[dict]   # Research Agent
    risk_report:            Optional[dict]   # Risk Agent
    code_output:            Optional[dict]   # Code Agent
    rebalance_suggestion:   Optional[dict]   # Rebalancing Agent
    tax_context:            Optional[dict]   # Tax Agent — only set when involves_taxable_decision()
    final_memo:             Optional[str]    # Writer Agent

    # ── Control ─────────────────────────────────────────────
    error:          Optional[str]
    messages:       list[str]     # execution log — what ran and when

    # ── Phase 5: validation gate ─────────────────────────────
    validation_passed: Optional[bool]   # set by validation_node
    validation_issues: Optional[list]   # validate_all failures, if any

    # ── Policy gate (harness/risk_policy.py) ─────────────────
    memo_verdict:    Optional[str]  # Buy/Hold/Avoid — set by writer_node, read by policy_node
    memo_risk_score: Optional[int]  # set by writer_node, read by policy_node
    policy_status:   Optional[str]  # "allow" | "deny" | "escalate" — set by policy_node
    policy_reason:   Optional[str]  # human-readable reason, if not "allow"