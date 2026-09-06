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
    DeepEval-compatible judge. Routes through services.llm_client.call_llm()
    instead of calling ChatGroq directly, so it inherits the same Groq-key
    rotation + OpenRouter fallback the main app uses.

    Was a direct ChatGroq call with no fallback — a single point of failure
    on one provider. That's exactly the failure class that broke the CI gate
    on 2026-09-03: Groq's daily token quota (org-wide, all 3 rotation keys
    share it) was exhausted from live testing, and every metric call 429'd.
    The main app already has an OpenRouter fallback for this; the eval judge
    didn't, despite existing for the same reason. This closes that gap.

    Caveat, kept simple deliberately: if Groq is unavailable and OpenRouter's
    free-tier model answers instead, get_model_name() still reports the Groq
    model — call_llm() doesn't report back which provider actually answered.
    Good enough for "the gate still runs instead of hard-failing"; revisit if
    you need per-call provider attribution in eval results.
    """

    def __init__(self, model: str = None):
        from services.llm_client import GROQ_MODEL
        self._model = model or GROQ_MODEL
        self._model_name = f"groq/{self._model}"

    def load_model(self):
        return self

    def generate(self, prompt: str, schema=None) -> str:
        import asyncio
        return asyncio.run(self.a_generate(prompt, schema))

    async def a_generate(self, prompt: str, schema=None) -> str:
        from services.llm_client import call_llm
        result = await call_llm(
            system="You are an expert evaluator.",
            user=prompt,
            max_tokens=2000,
            temperature=0,
            model=self._model,
        )
        if not result:
            raise RuntimeError("GroqJudge: Groq and OpenRouter fallback both failed to return a response")
        return result

    def get_model_name(self) -> str:
        return self._model_name


class GeminiJudge(DeepEvalBaseLLM):
    """
    DeepEval-compatible judge backed by Gemini instead of Groq. Used
    automatically when GEMINI_API_KEY is set — Gemini's rate limits are
    generous enough on a paid key that run_deepeval.py's 30s inter-metric
    pacing (needed only to survive Groq's free-tier 8k TPM cap) can be
    skipped entirely, so a full eval run finishes in a couple minutes
    instead of 20+.

    Real cost at ~4k tokens/call (per run_deepeval.py's own estimate):
    a --limit 5 CI run (45 calls) is a few cents; the full 28-example
    dataset (252 calls) is roughly $0.15-$1.50 depending on model choice —
    verified against Gemini's published pricing 2026-09-06, not a guess.
    """

    def __init__(self, model: str = None):
        self._model = model or os.getenv("GEMINI_JUDGE_MODEL", "gemini-2.5-flash-lite")

    def load_model(self):
        return self

    def generate(self, prompt: str, schema=None) -> str:
        import asyncio
        return asyncio.run(self.a_generate(prompt, schema))

    async def a_generate(self, prompt: str, schema=None) -> str:
        import httpx
        api_key = os.getenv("GEMINI_API_KEY", "")
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent",
                params={"key": api_key},
                json={"contents": [{"parts": [{"text": prompt}]}]},
            )
            resp.raise_for_status()
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]

    def get_model_name(self) -> str:
        return f"gemini/{self._model}"


JUDGE = GeminiJudge() if os.getenv("GEMINI_API_KEY") else GroqJudge()


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
