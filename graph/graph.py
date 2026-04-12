# graph/graph.py
"""
WealthOS LangGraph orchestrator.

## Phase 5 change
`validation_node` added between risk_and_code and rebalancing.

Execution order:
  finance_node
      │
      ├── data_node ──────┐   (parallel)
      └── research_node ──┤
                          │
              ┌───────────┴──────────┐
              ▼                      ▼
          risk_node             code_node   (parallel)
              │                      │
              └───────────┬──────────┘
                          ▼
                  validation_node     ← Phase 5: guardrails checks
                          │
                          ▼
                  rebalancing_node
                          │
                          ▼
                     writer_node
                          │
                         END
"""

import asyncio
from langgraph.graph import StateGraph, END
from graph.state import WealthOSState
from graph.nodes import (
    finance_node,
    data_node,
    research_node,
    risk_node,
    code_node,
    validation_node,
    rebalancing_node,
    writer_node,
    error_node,
)


# ── Parallel wrappers ──────────────────────────────────────────────────────────

async def data_and_research_node(state: WealthOSState) -> dict:
    """Runs data_node and research_node simultaneously."""
    print("\n[Graph] Parallel: Data + Research")
    data_result, research_result = await asyncio.gather(
        data_node(state),
        research_node(state),
    )
    return {
        **state,
        "financial_snapshot": data_result.get("financial_snapshot"),
        "research_output":    research_result.get("research_output"),
        "messages": (
            state.get("messages", [])
            + [m for m in data_result.get("messages", [])     if m not in state.get("messages", [])]
            + [m for m in research_result.get("messages", []) if m not in state.get("messages", [])]
        ),
        "error": data_result.get("error") or research_result.get("error"),
    }


async def risk_and_code_node(state: WealthOSState) -> dict:
    """Runs risk_node and code_node simultaneously."""
    print("\n[Graph] Parallel: Risk + Code")
    risk_result, code_result = await asyncio.gather(
        risk_node(state),
        code_node(state),
    )
    return {
        **state,
        "risk_report": risk_result.get("risk_report"),
        "code_output": code_result.get("code_output"),
        "messages": (
            state.get("messages", [])
            + [m for m in risk_result.get("messages", []) if m not in state.get("messages", [])]
            + [m for m in code_result.get("messages", []) if m not in state.get("messages", [])]
        ),
        "error": risk_result.get("error") or code_result.get("error"),
    }


# ── Error routing ──────────────────────────────────────────────────────────────

def route_on_error(state: WealthOSState) -> str:
    """Hard-stop only if the data fetch completely failed."""
    if state.get("error") and not state.get("financial_snapshot"):
        return "error"
    return "continue"


# ── Build the graph ────────────────────────────────────────────────────────────

def build_graph():
    graph = StateGraph(WealthOSState)

    graph.add_node("finance",           finance_node)
    graph.add_node("data_and_research", data_and_research_node)
    graph.add_node("risk_and_code",     risk_and_code_node)
    graph.add_node("validation",        validation_node)       # Phase 5
    graph.add_node("rebalancing",       rebalancing_node)
    graph.add_node("writer",            writer_node)
    graph.add_node("error",             error_node)

    graph.set_entry_point("finance")

    graph.add_edge("finance", "data_and_research")

    graph.add_conditional_edges(
        "data_and_research",
        route_on_error,
        {
            "continue": "risk_and_code",
            "error":    "error",
        }
    )

    graph.add_edge("risk_and_code", "validation")     # Phase 5
    graph.add_edge("validation",    "rebalancing")    # Phase 5
    graph.add_edge("rebalancing",   "writer")
    graph.add_edge("writer",        END)
    graph.add_edge("error",         END)

    return graph.compile()


wealthos_graph = build_graph()


# ── CLI test ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio, sys

    ticker  = sys.argv[1] if len(sys.argv) > 1 else "TSLA"
    user_id = sys.argv[2] if len(sys.argv) > 2 else "test-user"

    async def main():
        print(f"\n{'='*60}")
        print(f"  WealthOS Graph — {ticker}")
        print(f"{'='*60}\n")

        initial_state: WealthOSState = {
            "query":               f"Should I invest in {ticker}?",
            "tickers":             [ticker],
            "user_id":             user_id,
            "personal_finance":    None,
            "financial_snapshot":  None,
            "research_output":     None,
            "risk_report":         None,
            "code_output":         None,
            "rebalance_suggestion": None,
            "final_memo":          None,
            "error":               None,
            "messages":            [],
        }

        result = await wealthos_graph.ainvoke(initial_state)

        print(f"\n{'='*60}")
        print("  EXECUTION LOG")
        print(f"{'='*60}")
        for msg in result.get("messages", []):
            print(f"  {msg}")

        print(f"\n{'='*60}")
        print("  FINAL MEMO")
        print(f"{'='*60}")
        print(result.get("final_memo", "No memo generated"))

    asyncio.run(main())