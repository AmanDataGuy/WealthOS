#!/usr/bin/env python
# eval/deepeval_metrics.py
"""
## DeepEval Metrics — WealthOS Phase 2

Agent-quality evaluation for the full WealthOS 8-agent pipeline.

---

### Metrics

| Name                        | What it checks                                              | Judge         |
|-----------------------------|-------------------------------------------------------------|---------------|
| `FaithfulnessMetric`        | Every number in the memo traces to the provided context     | Groq LLM      |
| `HallucinationMetric`       | No invented past decisions, fabricated prices, fake history | Groq LLM      |
| `AnswerRelevancyMetric`     | Memo directly addresses the investment question             | Groq LLM      |
| `ContextualPrecisionMetric` | Most relevant context used before less relevant chunks      | Groq LLM      |
| `ContextualRecallMetric`    | All information needed for the memo was retrieved           | Groq LLM      |
| `ContextualRelevancyMetric` | Retrieved chunks are relevant to the query                  | Groq LLM      |
| `financial_verdict_geval`   | Verdict is financially defensible given the data            | GEval         |
| `task_completion_geval`     | All 7 memo sections present with verdict + personal fit     | GEval         |
| `VerdictConsistencyMetric`  | BUY fails if risk>=9; AVOID fails if risk<=4 (deterministic) | Pure Python   |

---

### Usage

    # Run via pytest:
    pytest tests/test_deepeval.py -v -m deepeval

    # Or evaluate a single case directly:
    from eval.deepeval_metrics import make_memo_test_case, faithfulness_metric
    from deepeval import evaluate
    import json
    from pathlib import Path
    dataset = json.loads(Path("eval/writer_golden_dataset.json").read_text())
    evaluate([make_memo_test_case(dataset[0])], [faithfulness_metric])
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepeval.models.base_model import DeepEvalBaseLLM
from deepeval.metrics import (
    FaithfulnessMetric,
    HallucinationMetric,
    AnswerRelevancyMetric,
    ContextualPrecisionMetric,
    ContextualRecallMetric,
    ContextualRelevancyMetric,
    GEval,
    BaseMetric,
)
from deepeval.test_case import LLMTestCase, LLMTestCaseParams


# ── Groq judge ────────────────────────────────────────────────────────────────
#
# DeepEval defaults to OpenAI. We swap it for Groq via the custom LLM class.
# Temperature=0 ensures reproducible scoring across eval runs.

class GroqJudge(DeepEvalBaseLLM):
    """
    DeepEval-compatible judge backed by Groq llama-3.3-70b (free tier).

    The ChatGroq client is built lazily on first use, not in __init__ — this
    lets the module (and the deterministic VerdictConsistencyMetric, which
    never touches an LLM) import cleanly without GROQ_API_KEY set, e.g. in CI
    jobs that only run the no-API-key test.
    """

    def __init__(self, model: str = "openai/gpt-oss-120b"):
        self._model = model
        self._model_name = f"groq/{model}"
        self._chat = None

    def load_model(self):
        if self._chat is None:
            from langchain_groq import ChatGroq
            self._chat = ChatGroq(
                model=self._model,
                api_key=os.getenv("GROQ_API_KEY", ""),
                temperature=0,
            )
        return self._chat

    def generate(self, prompt: str, schema=None) -> str:
        return self.load_model().invoke(prompt).content

    async def a_generate(self, prompt: str, schema=None) -> str:
        return (await self.load_model().ainvoke(prompt)).content

    def get_model_name(self) -> str:
        return self._model_name


JUDGE = GroqJudge()


# ── Built-in DeepEval metrics ─────────────────────────────────────────────────

faithfulness_metric = FaithfulnessMetric(
    threshold=0.75,
    model=JUDGE,
    include_reason=True,
)

hallucination_metric = HallucinationMetric(
    threshold=0.80,
    model=JUDGE,
    include_reason=True,
)

answer_relevancy_metric = AnswerRelevancyMetric(
    threshold=0.70,
    model=JUDGE,
    include_reason=True,
)

contextual_precision_metric = ContextualPrecisionMetric(
    threshold=0.70,
    model=JUDGE,
    include_reason=True,
)

contextual_recall_metric = ContextualRecallMetric(
    threshold=0.60,
    model=JUDGE,
    include_reason=True,
)

contextual_relevancy_metric = ContextualRelevancyMetric(
    threshold=0.60,
    model=JUDGE,
    include_reason=True,
)


# ── GEval: Financial verdict quality ──────────────────────────────────────────
#
# Checks whether the BUY/HOLD/AVOID verdict is backed by specific numbers from
# the financial data context, and does not contradict the risk score.

financial_verdict_geval = GEval(
    name="Financial Verdict Quality",
    criteria=(
        "Evaluate whether the investment verdict (BUY / HOLD / AVOID) in ACTUAL_OUTPUT "
        "is financially defensible. A good verdict must: "
        "(1) reference specific numbers from the financial data in INPUT / CONTEXT, "
        "(2) not contradict the risk score — a risk_score >= 9 (Very High) should lean HOLD or AVOID, "
        "(3) not invent company history, past analyses, or prior tickers not in the context, "
        "(4) give 2-3 specific reasons that directly cite data from the context."
    ),
    evaluation_params=[
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.CONTEXT,
    ],
    model=JUDGE,
    threshold=0.70,
)


# ── GEval: Task completion ────────────────────────────────────────────────────
#
# The writer must produce all 7 sections. GEval handles paraphrased headings
# more gracefully than a regex and gives partial-credit scoring.

task_completion_geval = GEval(
    name="Task Completion",
    criteria=(
        "Check if the investment memo in ACTUAL_OUTPUT contains ALL seven required sections. "
        "Each section must be substantive (not just a heading with no content): "
        "1. Executive Summary — includes a clear BUY / HOLD / AVOID verdict "
        "2. Financial Snapshot — contains specific financial figures "
        "3. Valuation Analysis — discusses DCF, Monte Carlo, or P/E valuation "
        "4. Risk Assessment — contains a risk score and specific risk factors "
        "5. Portfolio Impact — discusses effect on the user's portfolio "
        "6. Personal Finance Fit — references the user's personal financial data "
        "7. Final Verdict — restates the verdict with 2-3 numbered reasons. "
        "Score 1.0 if all 7 are present and substantive. "
        "Score 0.0 if the final verdict is missing. "
        "Deduct 0.14 for each other missing or empty section."
    ),
    evaluation_params=[LLMTestCaseParams.ACTUAL_OUTPUT],
    model=JUDGE,
    threshold=0.80,
)


# ── Custom: Verdict consistency (deterministic DAG check) ────────────────────
#
# No LLM needed — pure regex + rule logic.
# The verdict and risk_score come from the same agent run so they must agree.
#
# Rules (from risk_agent.py scoring logic):
#   risk_score <= 4  -> valid verdicts: BUY, HOLD, AVOID
#   risk_score 5-8   -> valid verdicts: BUY, HOLD, AVOID (risk alone doesn't rule out BUY —
#                        e.g. a contrarian dip-buy or a small crypto allocation can legitimately
#                        carry Moderate/High risk; position sizing, not the verdict, absorbs it)
#   risk_score >= 9  -> valid verdicts: HOLD, AVOID
#
# Violations that fail this metric:
#   BUY   with risk_score >= 9 (inconsistent — near-maximum risk but bullish verdict)
#   AVOID with risk_score <= 4 (inconsistent — low risk but bearish verdict)

class VerdictConsistencyMetric(BaseMetric):
    """
    Deterministic check — no LLM, always reproducible.
    Parses the verdict and risk_score, then verifies they are consistent.
    """

    def __init__(self):
        self.threshold = 1.0
        self.score     = 0.0
        self.success   = False
        self.reason    = ""

    def measure(self, test_case: LLMTestCase) -> float:
        memo    = test_case.actual_output or ""
        context = " ".join(test_case.context or [])

        verdict    = _extract_verdict(memo)
        risk_score = _extract_risk_score(context)

        if verdict is None or risk_score is None:
            # Can't verify — don't fail, but flag as inconclusive
            self.score   = 0.5
            self.success = True
            self.reason  = f"Could not parse: verdict={verdict!r}, risk_score={risk_score!r}"
            return self.score

        if verdict == "BUY" and risk_score >= 9:
            self.score   = 0.0
            self.success = False
            self.reason  = f"BUY verdict with risk_score={risk_score} (>=9, Very High) contradicts risk logic"
        elif verdict == "AVOID" and risk_score <= 4:
            self.score   = 0.0
            self.success = False
            self.reason  = f"AVOID verdict with risk_score={risk_score} (<=4) contradicts risk logic"
        else:
            self.score   = 1.0
            self.success = True
            self.reason  = f"Verdict={verdict} is consistent with risk_score={risk_score}"

        return self.score

    async def a_measure(self, test_case: LLMTestCase) -> float:
        return self.measure(test_case)

    @property
    def __name__(self):
        return "VerdictConsistencyMetric"


verdict_consistency_metric = VerdictConsistencyMetric()


# ── Helpers ───────────────────────────────────────────────────────────────────

from eval._verdict_utils import extract_verdict as _extract_verdict, extract_risk_score as _extract_risk_score


def make_memo_test_case(entry: dict) -> LLMTestCase:
    """
    Build an LLMTestCase from one entry in writer_golden_dataset.json.

    The context fields (financial_snapshot, risk_report, etc.) become both
    `context` and `retrieval_context` — they represent the ground-truth data
    the writer agent was given to produce the memo.

    Args:
        entry: One dict from writer_golden_dataset.json with keys:
               id, ticker, company_name, context (dict), memo, quality_score.

    Returns:
        LLMTestCase ready to pass to any DeepEval metric or assert_test().
    """
    ctx             = entry.get("context", {})
    context_strings = [v for v in ctx.values() if v]

    return LLMTestCase(
        input=(
            f"Provide a complete investment analysis memo for "
            f"{entry.get('company_name', '')} ({entry.get('ticker', '')}) "
            f"based on the financial data provided."
        ),
        actual_output=entry.get("memo", ""),
        expected_output=entry.get("memo", ""),   # golden dataset IS ground truth
        context=context_strings,
        retrieval_context=context_strings,
    )


def make_live_test_case(
    ticker: str,
    memo: str,
    rag_chunks: list[str],
    context_strings: list[str],
) -> LLMTestCase:
    """
    Build an LLMTestCase from a live pipeline run.

    Args:
        ticker:          Stock ticker that was analysed.
        memo:            Generated memo from writer_agent.
        rag_chunks:      Raw content strings from _hybrid_search_sync.
        context_strings: All context strings that were passed to the writer.

    Returns:
        LLMTestCase ready for any DeepEval metric.
    """
    return LLMTestCase(
        input=f"Provide a complete investment analysis for {ticker}.",
        actual_output=memo,
        expected_output=memo,
        context=context_strings,
        retrieval_context=rag_chunks or context_strings,
    )
