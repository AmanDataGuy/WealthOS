# eval/deepeval_metrics.py
"""
DeepEval metric definitions for WealthOS.

Judge: Gemini 2.5 Flash (primary) — high daily quota (1500 req/day free),
reliable JSON output, no OPENAI_API_KEY required.
Requires GEMINI_API_KEY in env.

Thresholds are conservative — a financial memo must be accurate, not just plausible.
"""

import os
import re
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY             = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
FAITHFULNESS_THRESHOLD     = 0.75
ANSWER_RELEVANCY_THRESHOLD = 0.80
HALLUCINATION_THRESHOLD    = 0.20


# ── Custom Gemini judge ────────────────────────────────────────────────────────

def _make_judge():
    """Return a DeepEvalBaseLLM-compatible Gemini Flash judge."""
    try:
        from deepeval.models.base_model import DeepEvalBaseLLM

        class _GeminiDeepEvalLLM(DeepEvalBaseLLM):
            def __init__(self):
                self._model_name = "gemini-1.5-flash"
                self._llm = None

            def _get_llm(self):
                if self._llm is None:
                    from langchain_google_genai import ChatGoogleGenerativeAI
                    self._llm = ChatGoogleGenerativeAI(
                        model=self._model_name,
                        google_api_key=GEMINI_API_KEY,
                        temperature=0,
                    )
                return self._llm

            def load_model(self):
                return self._get_llm()

            def _clean(self, raw: str) -> str:
                """Extract JSON from response — Gemini occasionally adds prose."""
                m = re.search(r"\{.*\}", raw, re.DOTALL)
                if m:
                    return m.group()
                m = re.search(r"\[.*\]", raw, re.DOTALL)
                return m.group() if m else raw

            def generate(self, prompt: str, **kwargs) -> str:
                return self._clean(self._get_llm().invoke(prompt).content)

            async def a_generate(self, prompt: str, **kwargs) -> str:
                res = await self._get_llm().ainvoke(prompt)
                return self._clean(res.content)

            def get_model_name(self) -> str:
                return f"gemini/{self._model_name}"

        return _GeminiDeepEvalLLM()

    except ImportError:
        raise ImportError("deepeval not installed — run: pip install deepeval")


# ── Metrics ────────────────────────────────────────────────────────────────────

def get_metrics():
    """Return configured DeepEval metric instances using Gemini Flash as judge."""
    try:
        from deepeval.metrics import (
            FaithfulnessMetric,
            AnswerRelevancyMetric,
            HallucinationMetric,
        )
    except ImportError:
        raise ImportError("deepeval not installed — run: pip install deepeval")

    judge = _make_judge()

    faithfulness = FaithfulnessMetric(
        threshold=FAITHFULNESS_THRESHOLD,
        model=judge,
        include_reason=True,
    )
    answer_relevancy = AnswerRelevancyMetric(
        threshold=ANSWER_RELEVANCY_THRESHOLD,
        model=judge,
        include_reason=True,
    )
    hallucination = HallucinationMetric(
        threshold=HALLUCINATION_THRESHOLD,
        model=judge,
        include_reason=True,
    )

    return faithfulness, answer_relevancy, hallucination


# ── Test case builder ──────────────────────────────────────────────────────────

def build_test_case(query: str, memo: str, context_chunks: list[str]):
    """Build a DeepEval LLMTestCase from WealthOS pipeline outputs."""
    try:
        from deepeval.test_case import LLMTestCase
    except ImportError:
        raise ImportError("deepeval not installed — run: pip install deepeval")

    return LLMTestCase(
        input=query,
        actual_output=memo,
        context=context_chunks,           # required by HallucinationMetric
        retrieval_context=context_chunks, # required by FaithfulnessMetric
    )
