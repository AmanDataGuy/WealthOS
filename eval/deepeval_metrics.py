# eval/deepeval_metrics.py
"""
DeepEval metric definitions for WealthOS.

Three metrics that matter for an investment memo:
- Faithfulness: claims should be grounded in the financial data we passed in
- Answer Relevancy: the memo should actually answer the investment question
- Hallucination: no invented numbers or unsupported assertions

Thresholds are set conservatively — a financial memo has to be accurate,
not just plausible.
"""

FAITHFULNESS_THRESHOLD   = 0.75   # 75% of claims traceable to input context
ANSWER_RELEVANCY_THRESHOLD = 0.80  # memo must directly address the query
HALLUCINATION_THRESHOLD  = 0.20   # at most 20% hallucinated statements


def get_metrics():
    """
    Returns configured DeepEval metric instances.
    Import is deferred so the rest of the eval module loads even if
    deepeval isn't installed.
    """
    try:
        from deepeval.metrics import (
            FaithfulnessMetric,
            AnswerRelevancyMetric,
            HallucinationMetric,
        )
    except ImportError:
        raise ImportError(
            "deepeval not installed — run: pip install deepeval"
        )

    faithfulness = FaithfulnessMetric(
        threshold=FAITHFULNESS_THRESHOLD,
        model="gpt-4o-mini",   # cheaper judge for batch eval
        include_reason=True,
    )

    answer_relevancy = AnswerRelevancyMetric(
        threshold=ANSWER_RELEVANCY_THRESHOLD,
        model="gpt-4o-mini",
        include_reason=True,
    )

    hallucination = HallucinationMetric(
        threshold=HALLUCINATION_THRESHOLD,
        model="gpt-4o-mini",
        include_reason=True,
    )

    return faithfulness, answer_relevancy, hallucination


def build_test_case(query: str, memo: str, context_chunks: list[str]):
    """
    Build a DeepEval LLMTestCase from WealthOS pipeline outputs.

    query          — the user's original investment question
    memo           — the Writer Agent's output
    context_chunks — list of strings that were used as retrieval context
                     (financial_snapshot, risk_report, etc. as text)
    """
    try:
        from deepeval.test_case import LLMTestCase
    except ImportError:
        raise ImportError("deepeval not installed — run: pip install deepeval")

    return LLMTestCase(
        input=query,
        actual_output=memo,
        retrieval_context=context_chunks,
    )
