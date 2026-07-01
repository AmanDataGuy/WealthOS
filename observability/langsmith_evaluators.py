# observability/langsmith_evaluators.py
"""
LangSmith custom evaluators for WealthOS memo quality.

Three evaluators:
  section_completeness — checks that all required sections appear in the memo
  verdict_consistency  — verifies verdict matches risk score band
  number_grounding     — heuristic: does every dollar figure have a source tag?

Usage:
    from langsmith import evaluate
    from observability.langsmith_evaluators import (
        section_completeness, verdict_consistency, number_grounding,
    )

    results = evaluate(
        target_fn,
        data="wealthos-eval-v1",
        evaluators=[section_completeness, verdict_consistency, number_grounding],
    )
"""

from __future__ import annotations

import re
from typing import Any

# ── Required memo sections ─────────────────────────────────────────────────────

REQUIRED_SECTIONS = [
    "Financial Snapshot",
    "Risk Assessment",
    "Final Verdict",
]


# ── Evaluator 1 — Section Completeness ────────────────────────────────────────

def section_completeness(run: Any, example: Any) -> dict:
    """
    Returns score 0.0–1.0: fraction of required sections present in the memo.
    A memo missing even one section scores < 1.0 and triggers a LangSmith flag.
    """
    try:
        memo: str = (
            run.outputs.get("final_memo", "")
            or run.outputs.get("output", "")
            or ""
        )
        if not memo:
            return {"key": "section_completeness", "score": 0.0, "comment": "empty memo"}

        found   = sum(1 for s in REQUIRED_SECTIONS if s.lower() in memo.lower())
        score   = found / len(REQUIRED_SECTIONS)
        missing = [s for s in REQUIRED_SECTIONS if s.lower() not in memo.lower()]
        comment = "all sections present" if not missing else f"missing: {', '.join(missing)}"
        return {"key": "section_completeness", "score": score, "comment": comment}

    except Exception as e:
        return {"key": "section_completeness", "score": 0.0, "comment": f"evaluator error: {e}"}


# ── Evaluator 2 — Verdict Consistency ─────────────────────────────────────────

def verdict_consistency(run: Any, example: Any) -> dict:
    """
    Checks that the verdict word (Buy / Hold / Avoid) in the memo is consistent
    with the risk_score from the Risk Agent:
      score 1-4  → expect Buy
      score 5-6  → expect Hold
      score 7-10 → expect Avoid

    Returns 1.0 if consistent, 0.0 if contradictory, 0.5 if undeterminable.
    """
    try:
        memo: str       = run.outputs.get("final_memo", "") or ""
        risk_score: Any = (run.outputs.get("risk_report") or {}).get("risk_score")

        if not memo or risk_score is None:
            return {"key": "verdict_consistency", "score": 0.5, "comment": "insufficient data"}

        risk_score = int(risk_score)
        expected = (
            "buy"   if risk_score <= 4 else
            "hold"  if risk_score <= 6 else
            "avoid"
        )

        match = re.search(r"\b(buy|hold|avoid)\b", memo, re.IGNORECASE)
        if not match:
            return {"key": "verdict_consistency", "score": 0.5, "comment": "no verdict word found"}

        actual = match.group(1).lower()
        ok     = actual == expected
        return {
            "key":     "verdict_consistency",
            "score":   1.0 if ok else 0.0,
            "comment": f"risk={risk_score} → expected={expected}, found={actual}",
        }

    except Exception as e:
        return {"key": "verdict_consistency", "score": 0.5, "comment": f"evaluator error: {e}"}


# ── Evaluator 3 — Number Grounding ────────────────────────────────────────────

def number_grounding(run: Any, example: Any) -> dict:
    """
    Heuristic: for every dollar/rupee figure in the memo, check whether it is
    followed by a source citation within 80 characters.

    A citation is any of: "(10-K", "(Q1", "(Reuters", "(Bloomberg", "(EDGAR",
    "(FY", "(per ", "(source:".

    Returns score = grounded_numbers / total_numbers (1.0 if no numbers found).
    """
    try:
        memo: str = run.outputs.get("final_memo", "") or ""
        if not memo:
            return {"key": "number_grounding", "score": 0.0, "comment": "empty memo"}

        number_positions = [m.start() for m in re.finditer(r"[\$₹]\s*[\d,\.]+[BMKk]?", memo)]
        if not number_positions:
            return {"key": "number_grounding", "score": 1.0, "comment": "no numeric figures found"}

        citation_pattern = re.compile(
            r"\((?:10-K|10-Q|Q[1-4]\s*\d{4}|FY\d{4}|Reuters|Bloomberg|EDGAR|per |source:)",
            re.IGNORECASE,
        )

        grounded = sum(
            1 for pos in number_positions
            if citation_pattern.search(memo[pos: pos + 80])
        )
        total = len(number_positions)
        return {
            "key":     "number_grounding",
            "score":   round(grounded / total, 3),
            "comment": f"{grounded}/{total} figures have source citations",
        }

    except Exception as e:
        return {"key": "number_grounding", "score": 0.0, "comment": f"evaluator error: {e}"}
