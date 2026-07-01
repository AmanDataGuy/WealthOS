# tests/test_e2e.py
"""
End-to-end pipeline test — runs AAPL through the full LangGraph graph.

Requires:
  - GROQ_API_KEY set (uses real LLM calls)
  - Qdrant running at QDRANT_URL (or localhost:6333)

Run:
    pytest tests/test_e2e.py -v -s

Skip in CI if no GROQ_API_KEY:
    SKIP_E2E=1 pytest  OR  omit GROQ_API_KEY from the environment
"""

import os
import asyncio
import pytest

if not os.getenv("GROQ_API_KEY") or os.getenv("SKIP_E2E"):
    pytest.skip("GROQ_API_KEY not set or SKIP_E2E=1", allow_module_level=True)

DEMO_USER   = "00000000-0000-0000-0000-000000000001"
TEST_TICKER = "AAPL"


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
async def aapl_result(event_loop):
    from graph.graph import build_graph
    from graph.state import WealthOSState

    graph = build_graph()
    initial: WealthOSState = {
        "user_id":            DEMO_USER,
        "tickers":            [TEST_TICKER],
        "query":              f"Should I invest in {TEST_TICKER} long term?",
        "input_source":       "text",
        "investment_horizon": "long",
        "messages":           [],
    }
    return await graph.ainvoke(initial)


@pytest.mark.asyncio
async def test_pipeline_completes(aapl_result):
    assert aapl_result is not None
    assert "final_memo" in aapl_result
    assert aapl_result["final_memo"]


@pytest.mark.asyncio
async def test_memo_minimum_length(aapl_result):
    memo = aapl_result.get("final_memo", "")
    assert len(memo) >= 1000, (
        f"Memo too short ({len(memo)} chars). First 300: {memo[:300]}"
    )


@pytest.mark.asyncio
async def test_memo_has_required_sections(aapl_result):
    memo    = aapl_result.get("final_memo", "").lower()
    missing = [s for s in ["financial snapshot", "risk assessment", "final verdict"]
               if s not in memo]
    assert not missing, f"Missing sections: {missing}"


@pytest.mark.asyncio
async def test_memo_contains_ticker(aapl_result):
    memo = aapl_result.get("final_memo", "")
    assert TEST_TICKER in memo or TEST_TICKER.lower() in memo.lower()


@pytest.mark.asyncio
async def test_no_pipeline_error(aapl_result):
    assert not aapl_result.get("error"), f"Pipeline error: {aapl_result.get('error')}"


@pytest.mark.asyncio
async def test_risk_report_valid(aapl_result):
    report = aapl_result.get("risk_report")
    assert report is not None, "risk_report missing"
    score = report.get("risk_score")
    assert score is not None and 1 <= int(score) <= 10, f"Invalid risk_score: {score}"


@pytest.mark.asyncio
async def test_verdict_in_memo(aapl_result):
    import re
    memo  = aapl_result.get("final_memo", "")
    match = re.search(r"\b(buy|hold|avoid)\b", memo, re.IGNORECASE)
    assert match, "No Buy/Hold/Avoid verdict found in memo"
