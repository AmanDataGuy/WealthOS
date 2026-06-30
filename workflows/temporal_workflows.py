# workflows/temporal_workflows.py
"""
WealthOS Temporal durable workflow.

Each agent call is wrapped as a Temporal activity with retry policy.
If the server crashes mid-pipeline, Temporal replays from the last
completed activity — not from scratch.

Run the worker separately:
  python -m workflows.temporal_worker

Trigger a workflow from api/main.py or CLI:
  python -m workflows.temporal_workflows TSLA 00000000-0000-0000-0000-000000000001
"""

import asyncio
import sys
from datetime import timedelta
from typing import Optional

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.client import Client
from temporalio.worker import Worker


TASK_QUEUE = "wealthos-queue"

RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=30),
    backoff_coefficient=2.0,
)


# ── Activities (the actual agent calls) ───────────────────────────────────────
# Each activity is isolated — Temporal retries individual activities, not the
# whole workflow. A failed Code Agent call retries without re-running the
# Data Agent or Risk Agent.

@activity.defn
async def finance_activity(user_id: str) -> dict:
    """Returns the personal finance context for this user."""
    # Stub — same test context as graph/nodes.py finance_node
    # Swap in run_finance_agent(user_id) in Phase 8
    return {
        "monthly_income":     150000,
        "monthly_surplus":    30000,
        "debt_burden_ratio":  0.25,
        "health_score":       {"total": 72, "grade": "Good"},
        "risk_capacity":      "medium",
        "investable_monthly": 20000,
        "goals":              [],
        "anomalies":          [],
    }


@activity.defn
async def data_activity(ticker: str) -> dict:
    from agents.data_agent import run_data_agent
    snapshot = await run_data_agent(ticker, use_rag=True)
    return snapshot.model_dump()


@activity.defn
async def research_activity(ticker: str) -> dict:
    try:
        from agents.research_agent import run_research_agent
        snapshot = await run_research_agent("temporal-worker", [ticker])
        return snapshot.model_dump() if hasattr(snapshot, "model_dump") else {"summary": str(snapshot)}
    except Exception as e:
        return {"summary": f"Research unavailable: {e}"}


@activity.defn
async def risk_activity(ticker: str, snapshot: dict, personal: dict) -> dict:
    from agents.risk_agent import run_risk_agent
    report = await run_risk_agent(
        ticker=ticker,
        financial_snapshot=snapshot,
        personal_finance=personal,
    )
    return report.model_dump()


@activity.defn
async def code_activity(ticker: str, snapshot: dict) -> dict:
    from agents.code_agent import run_code_agent
    result = await run_code_agent(ticker=ticker, financial_snapshot=snapshot)
    return result.model_dump()


@activity.defn
async def rebalancing_activity(user_id: str, ticker: str, snapshot: dict) -> Optional[dict]:
    from agents.rebalancing_agent import run_rebalancing_agent, NewInvestment
    sector  = (snapshot or {}).get("sector") or "Technology"
    new_inv = NewInvestment(ticker=ticker, amount=20000.0, sector=sector)
    try:
        suggestion = await run_rebalancing_agent(user_id=user_id, new_investment=new_inv)
        return suggestion.model_dump()
    except Exception as e:
        return None


@activity.defn
async def writer_activity(
    ticker: str,
    snapshot: dict,
    risk: dict,
    code: dict,
    rebalance: Optional[dict],
    personal: dict,
    research: dict,
) -> dict:
    from agents.writer_agent import run_writer_agent
    memo = await run_writer_agent(
        ticker=ticker,
        financial_snapshot=snapshot,
        risk_report=risk,
        code_output=code,
        rebalance_suggestion=rebalance,
        personal_finance=personal,
        research_snapshot=research,
    )
    return {"full_memo": memo.full_memo, "verdict": memo.verdict}


@activity.defn
async def mem0_write_activity(user_id: str, state: dict) -> None:
    from memory.mem0_client import write_memory
    write_memory(user_id, state)


# ── Workflow ──────────────────────────────────────────────────────────────────

@workflow.defn
class WealthOSWorkflow:
    """
    Durable WealthOS pipeline.

    Execution order mirrors the LangGraph graph:
      finance → [data + research parallel] → [risk + code parallel]
              → rebalancing → writer → mem0_write
    """

    @workflow.run
    async def run(self, ticker: str, user_id: str, query: str) -> dict:
        workflow.logger.info(f"WealthOSWorkflow starting — {ticker} for {user_id}")

        # Step 1 — Finance (personal context)
        personal = await workflow.execute_activity(
            finance_activity,
            user_id,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RETRY,
        )

        # Step 2 — Data + Research in parallel
        data_fut     = workflow.execute_activity(
            data_activity, ticker,
            start_to_close_timeout=timedelta(seconds=120),
            retry_policy=RETRY,
        )
        research_fut = workflow.execute_activity(
            research_activity, ticker,
            start_to_close_timeout=timedelta(seconds=120),
            retry_policy=RETRY,
        )
        snapshot, research = await asyncio.gather(data_fut, research_fut)

        # Step 3 — Risk + Code in parallel
        risk_fut = workflow.execute_activity(
            risk_activity, ticker, snapshot, personal,
            start_to_close_timeout=timedelta(seconds=180),
            retry_policy=RETRY,
        )
        code_fut = workflow.execute_activity(
            code_activity, ticker, snapshot,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RETRY,
        )
        risk, code = await asyncio.gather(risk_fut, code_fut)

        # Step 4 — Rebalancing
        rebalance = await workflow.execute_activity(
            rebalancing_activity, user_id, ticker, snapshot,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RETRY,
        )

        # Step 5 — Writer
        memo_result = await workflow.execute_activity(
            writer_activity, ticker, snapshot, risk, code, rebalance, personal, research,
            start_to_close_timeout=timedelta(seconds=180),
            retry_policy=RETRY,
        )

        # Step 6 — Save to Mem0
        final_state = {
            "tickers":            [ticker],
            "risk_report":        risk,
            "personal_finance":   personal,
            "final_memo":         memo_result.get("full_memo", ""),
        }
        await workflow.execute_activity(
            mem0_write_activity, user_id, final_state,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )

        workflow.logger.info(f"WealthOSWorkflow complete — {ticker} verdict: {memo_result.get('verdict')}")

        return {
            "ticker":     ticker,
            "verdict":    memo_result.get("verdict"),
            "memo":       memo_result.get("full_memo"),
            "risk_score": risk.get("risk_score"),
        }


# ── CLI test ──────────────────────────────────────────────────────────────────

async def _cli():
    ticker  = sys.argv[1] if len(sys.argv) > 1 else "TSLA"
    user_id = sys.argv[2] if len(sys.argv) > 2 else "00000000-0000-0000-0000-000000000001"

    client = await Client.connect("localhost:7233")

    result = await client.execute_workflow(
        WealthOSWorkflow.run,
        args=[ticker, user_id, f"Should I invest in {ticker}?"],
        id=f"wealthos-{ticker}-{user_id}",
        task_queue=TASK_QUEUE,
    )

    print(f"\nVerdict : {result['verdict']}")
    print(f"Risk    : {result['risk_score']}/10")
    print(f"\nMemo preview:\n{result['memo'][:500]}...")


if __name__ == "__main__":
    asyncio.run(_cli())