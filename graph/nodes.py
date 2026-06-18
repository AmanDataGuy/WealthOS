# graph/nodes.py
"""
One async function per agent.
Each node pulls what it needs from state, calls the agent, writes result back.
Errors are caught per-node so one failure doesn't kill the whole pipeline.

## Phase 5 addition
`validation_node` added between risk_and_code and rebalancing.

## Phase 6 addition
`finance_node` now reads Mem0 memory at the start.
`writer_node` now writes results to Mem0 at the end.
"""

import time
from agents.data_agent        import run_data_agent
from agents.risk_agent        import run_risk_agent
from agents.code_agent        import run_code_agent
from agents.rebalancing_agent import run_rebalancing_agent, NewInvestment
from agents.writer_agent      import run_writer_agent
from graph.state              import WealthOSState
from guardrails.validators    import validate_all, validate_memo
from observability.langsmith_config import trace_node


def log(state: WealthOSState, msg: str) -> list[str]:
    messages = state.get("messages", [])
    messages.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    return messages


# ── Finance Node ───────────────────────────────────────────────────────────────
# Phase 6: reads Mem0 memory before doing anything else.
# The user_memory string flows into every downstream agent via state.

@trace_node("finance_node")
async def finance_node(state: WealthOSState) -> dict:
    print("\n[Graph] Finance Node running...")
    try:
        # Phase 6 — pull long-term memory for this user
        user_id = state.get("user_id", "test-user")
        user_memory = ""
        try:
            from memory.mem0_client import read_memory
            user_memory = read_memory(user_id)
            if user_memory:
                print(f"  [mem0] Loaded memory for {user_id}")
        except Exception as e:
            print(f"  [mem0] ⚠️  Could not load memory: {e}")

        # Actually run the Finance Agent instead of returning hardcoded data
        try:
            from agents.finance_agent import run_finance_agent
            snapshot = await run_finance_agent(user_id)
            personal_finance = snapshot.model_dump()
            print(f"  [finance] Agent returned: status={snapshot.status}, confidence={snapshot.data_confidence}")
        except Exception as e:
            print(f"  [finance] ⚠️  Agent failed ({e}), using minimal defaults")
            personal_finance = {
                "monthly_income":    0,
                "monthly_surplus":   0,
                "debt_burden_ratio": 0,
                "health_score":      {"overall": 50, "grade": "C"},
                "risk_capacity":     "unknown",
                "investable_monthly": 0,
                "goals": [],
                "anomalies": [],
                "data_confidence":   "none",
                "status":            "error",
                "message":           f"Finance Agent failed: {e}",
            }

        return {
            "user_memory":      user_memory,
            "personal_finance": personal_finance,
            "messages": log(state, f"Finance Node ✅ (confidence={personal_finance.get('data_confidence', 'unknown')}, memory={'yes' if user_memory else 'none'})"),
        }
    except Exception as e:
        return {
            "error": f"Finance Node failed: {e}",
            "messages": log(state, f"Finance Node ❌ {e}"),
        }


# ── Data Node ──────────────────────────────────────────────────────────────────

@trace_node("data_node")
async def data_node(state: WealthOSState) -> dict:
    print("\n[Graph] Data Node running...")
    ticker = state["tickers"][0] if state.get("tickers") else None
    if not ticker:
        return {"error": "No ticker provided", "messages": log(state, "Data Node ❌ no ticker")}
    try:
        snapshot = await run_data_agent(ticker, use_rag=True)
        return {
            "financial_snapshot": snapshot.model_dump(),
            "messages": log(state, f"Data Node ✅ {ticker} — confidence {snapshot.confidence}"),
        }
    except Exception as e:
        return {
            "error": f"Data Node failed: {e}",
            "messages": log(state, f"Data Node ❌ {e}"),
        }


# ── Research Node ──────────────────────────────────────────────────────────────

@trace_node("research_node")
async def research_node(state: WealthOSState) -> dict:
    print("\n[Graph] Research Node running...")
    ticker = state["tickers"][0] if state.get("tickers") else "Unknown"
    try:
        from agents.research_agent import run_research_agent
        user_id = state.get("user_id", "test-user")
        snapshot = await run_research_agent(user_id, [ticker])
        return {
            "research_output": snapshot.model_dump() if hasattr(snapshot, "model_dump") else {"summary": str(snapshot)},
            "messages": log(state, f"Research Node ✅ {ticker}"),
        }
    except Exception as e:
        return {
            "research_output": {"summary": f"Research unavailable: {e}"},
            "messages": log(state, f"Research Node ⚠️ {e} (continuing)"),
        }


# ── Risk Node ──────────────────────────────────────────────────────────────────

@trace_node("risk_node")
async def risk_node(state: WealthOSState) -> dict:
    print("\n[Graph] Risk Node running...")
    ticker   = state["tickers"][0] if state.get("tickers") else None
    snapshot = state.get("financial_snapshot")
    personal = state.get("personal_finance")
    if not ticker:
        return {"messages": log(state, "Risk Node ❌ no ticker")}
    try:
        report = await run_risk_agent(
            ticker=ticker,
            financial_snapshot=snapshot,
            personal_finance=personal,
        )
        return {
            "risk_report": report.model_dump(),
            "messages": log(state, f"Risk Node ✅ score={report.risk_score}/10 {report.recommendation}"),
        }
    except Exception as e:
        return {
            "error": f"Risk Node failed: {e}",
            "messages": log(state, f"Risk Node ❌ {e}"),
        }


# ── Code Node ──────────────────────────────────────────────────────────────────

@trace_node("code_node")
async def code_node(state: WealthOSState) -> dict:
    print("\n[Graph] Code Node running...")
    ticker   = state["tickers"][0] if state.get("tickers") else None
    snapshot = state.get("financial_snapshot")
    if not ticker:
        return {"messages": log(state, "Code Node ❌ no ticker")}
    try:
        result = await run_code_agent(ticker=ticker, financial_snapshot=snapshot)
        return {
            "code_output": result.model_dump(),
            "messages": log(state, f"Code Node ✅ DCF=${result.dcf.intrinsic_value:.2f}" if result.dcf else "Code Node ✅"),
        }
    except Exception as e:
        return {
            "error": f"Code Node failed: {e}",
            "messages": log(state, f"Code Node ❌ {e}"),
        }


# ── Validation Node ────────────────────────────────────────────────────────────

@trace_node("validation_node")
async def validation_node(state: WealthOSState) -> dict:
    print("\n[Graph] Validation Node running...")
    valid, error = validate_all(state)
    if valid:
        return {
            "messages": log(state, "Validation Node ✅ all agent outputs passed checks"),
        }
    else:
        print(f"  [validation] ⚠️  {error}")
        return {
            "messages": log(state, f"Validation Node ⚠️ {error} (continuing)"),
        }


# ── Rebalancing Node ───────────────────────────────────────────────────────────

@trace_node("rebalancing_node")
async def rebalancing_node(state: WealthOSState) -> dict:
    print("\n[Graph] Rebalancing Node running...")
    user_id = state.get("user_id", "test-user")
    ticker  = state["tickers"][0] if state.get("tickers") else None

    new_inv  = None
    snapshot = state.get("financial_snapshot")
    if ticker and snapshot:
        sector  = snapshot.get("sector") or "Technology"
        new_inv = NewInvestment(ticker=ticker, amount=20000.0, sector=sector)

    try:
        suggestion = await run_rebalancing_agent(
            user_id=user_id,
            new_investment=new_inv,
        )
        return {
            "rebalance_suggestion": suggestion.model_dump(),
            "messages": log(state, f"Rebalancing Node ✅ {len(suggestion.actions)} actions"),
        }
    except Exception as e:
        return {
            "rebalance_suggestion": None,
            "messages": log(state, f"Rebalancing Node ⚠️ {e} (continuing)"),
        }


# ── Writer Node ────────────────────────────────────────────────────────────────
# Phase 6: writes results to Mem0 after memo is complete.

@trace_node("writer_node")
async def writer_node(state: WealthOSState) -> dict:
    print("\n[Graph] Writer Node running...")
    ticker = state["tickers"][0] if state.get("tickers") else "Unknown"
    try:
        memo = await run_writer_agent(
            ticker=ticker,
            financial_snapshot=state.get("financial_snapshot"),
            risk_report=state.get("risk_report"),
            code_output=state.get("code_output"),
            rebalance_suggestion=state.get("rebalance_suggestion"),
            personal_finance=state.get("personal_finance"),
            research_snapshot=state.get("research_output"),
        )

        valid, error = validate_memo(memo.full_memo)
        if not valid:
            print(f"  [validation] ⚠️  Memo validation: {error}")

        # Phase 6 — save this analysis to Mem0
        try:
            from memory.mem0_client import write_memory
            write_memory(state.get("user_id", "test-user"), {
                **state,
                "final_memo": memo.full_memo,
            })
        except Exception as e:
            print(f"  [mem0] ⚠️  write failed: {e}")

        return {
            "final_memo": memo.full_memo,
            "messages": log(state, f"Writer Node ✅ {len(memo.full_memo)} chars — verdict: {memo.verdict}"),
        }
    except Exception as e:
        return {
            "error": f"Writer Node failed: {e}",
            "messages": log(state, f"Writer Node ❌ {e}"),
        }


# ── Error Node ─────────────────────────────────────────────────────────────────

@trace_node("error_node")
async def error_node(state: WealthOSState) -> dict:
    print(f"\n[Graph] Error Node — {state.get('error')}")
    return {
        "final_memo": f"Analysis failed: {state.get('error', 'Unknown error')}. Please try again.",
        "messages": log(state, "Error Node — pipeline terminated"),
    }