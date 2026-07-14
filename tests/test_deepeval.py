# tests/test_deepeval.py
"""
## DeepEval Tests — WealthOS Phase 2

Runs 9 metrics against the writer golden dataset to verify memo quality,
faithfulness, and verdict consistency.

---

### Metric groups

**LLM-judge metrics (require GROQ_API_KEY, marked `deepeval`)**
- Faithfulness — every number traces to context
- Hallucination — no invented facts
- Answer relevancy — memo addresses the question
- Contextual precision / recall / relevancy — context quality
- Financial verdict quality (GEval) — verdict is defensible
- Task completion (GEval) — all 7 sections present

**Deterministic metric (always runs, no API key needed)**
- Verdict consistency — BUY/HOLD/AVOID matches the risk_score

---

### Run

    # Deterministic only (fast, CI-safe):
    pytest tests/test_deepeval.py::test_verdict_consistency -v

    # Full suite with LLM judge (~3 min):
    pytest tests/test_deepeval.py -v -m deepeval

    # Via DeepEval CLI (rich dashboard + Confident AI):
    deepeval test run tests/test_deepeval.py

---

### Setup

    export GROQ_API_KEY=gsk_...
    pytest tests/test_deepeval.py -v -m deepeval
"""

import json
import pytest
from pathlib import Path

# ── Skip guard ────────────────────────────────────────────────────────────────

_HAS_KEY = bool(__import__("os").getenv("GROQ_API_KEY"))

# All LLM-judge tests carry this marker — CI skips them automatically
_skip = pytest.mark.skipif(not _HAS_KEY, reason="GROQ_API_KEY not set")

# ── Golden dataset ────────────────────────────────────────────────────────────

GOLDEN_PATH = Path(__file__).parent.parent / "eval" / "writer_golden_dataset.json"


@pytest.fixture(scope="module")
def golden_dataset():
    if not GOLDEN_PATH.exists():
        pytest.skip(f"{GOLDEN_PATH} not found")
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def all_cases(golden_dataset):
    from eval.deepeval_metrics import make_memo_test_case
    return [make_memo_test_case(entry) for entry in golden_dataset]


# First 3 entries by default — enough to catch regressions without burning API quota.
# Swap to `all_cases` for a full 15-entry sweep.
@pytest.fixture(scope="module")
def sample_cases(all_cases):
    return all_cases[:3]


# ══════════════════════════════════════════════════════════════════════════════
# LLM-judge metrics (require GROQ_API_KEY)
# Run with: pytest tests/test_deepeval.py -m deepeval
# ══════════════════════════════════════════════════════════════════════════════

@_skip
@pytest.mark.deepeval
def test_faithfulness(sample_cases):
    """Every number cited in the memo must appear in the provided context (>= 0.75)."""
    from deepeval import assert_test
    from eval.deepeval_metrics import faithfulness_metric
    for case in sample_cases:
        assert_test(case, [faithfulness_metric])


@_skip
@pytest.mark.deepeval
def test_hallucination(sample_cases):
    """Memo must not invent facts not present in the context (>= 0.80)."""
    from deepeval import assert_test
    from eval.deepeval_metrics import hallucination_metric
    for case in sample_cases:
        assert_test(case, [hallucination_metric])


@_skip
@pytest.mark.deepeval
def test_answer_relevancy(sample_cases):
    """Memo must directly address the investment question asked (>= 0.70)."""
    from deepeval import assert_test
    from eval.deepeval_metrics import answer_relevancy_metric
    for case in sample_cases:
        assert_test(case, [answer_relevancy_metric])


@_skip
@pytest.mark.deepeval
def test_contextual_precision(sample_cases):
    """Most relevant context fields must be used before less relevant ones (>= 0.70)."""
    from deepeval import assert_test
    from eval.deepeval_metrics import contextual_precision_metric
    for case in sample_cases:
        assert_test(case, [contextual_precision_metric])


@_skip
@pytest.mark.deepeval
def test_contextual_recall(sample_cases):
    """All context information needed for the memo must have been retrieved (>= 0.60)."""
    from deepeval import assert_test
    from eval.deepeval_metrics import contextual_recall_metric
    for case in sample_cases:
        assert_test(case, [contextual_recall_metric])


@_skip
@pytest.mark.deepeval
def test_contextual_relevancy(sample_cases):
    """Context fields must be relevant to the investment question (>= 0.60)."""
    from deepeval import assert_test
    from eval.deepeval_metrics import contextual_relevancy_metric
    for case in sample_cases:
        assert_test(case, [contextual_relevancy_metric])


@_skip
@pytest.mark.deepeval
def test_financial_verdict_quality(sample_cases):
    """Verdict must be defensible — backed by specific data from context (>= 0.70)."""
    from deepeval import assert_test
    from eval.deepeval_metrics import financial_verdict_geval
    for case in sample_cases:
        assert_test(case, [financial_verdict_geval])


@_skip
@pytest.mark.deepeval
def test_task_completion(sample_cases):
    """Memo must contain all 7 required sections (>= 0.80)."""
    from deepeval import assert_test
    from eval.deepeval_metrics import task_completion_geval
    for case in sample_cases:
        assert_test(case, [task_completion_geval])


# ══════════════════════════════════════════════════════════════════════════════
# Deterministic check — always runs in CI, no API key needed
# ══════════════════════════════════════════════════════════════════════════════

def test_verdict_consistency(golden_dataset):
    """
    Deterministic DAG check — no LLM, always reproducible.

    Rules:
      BUY  with risk_score >= 6  -> fail (high risk but bullish verdict)
      AVOID with risk_score <= 4 -> fail (low risk but bearish verdict)

    All 15 golden dataset entries must pass.
    """
    from eval.deepeval_metrics import make_memo_test_case, verdict_consistency_metric

    failures = []
    for entry in golden_dataset:
        case = make_memo_test_case(entry)
        verdict_consistency_metric.measure(case)
        if not verdict_consistency_metric.success:
            failures.append(
                f"[{entry['ticker']}] {verdict_consistency_metric.reason}"
            )

    assert not failures, (
        "Verdict/risk_score contradictions in golden dataset:\n"
        + "\n".join(failures)
    )
