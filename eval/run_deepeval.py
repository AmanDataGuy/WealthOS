#!/usr/bin/env python
# eval/run_deepeval.py
"""
CI entry point for the DeepEval quality gate (.github/workflows/eval.yml).

Scores golden-dataset memos with the 9 metrics defined in eval/deepeval_metrics.py
and writes eval/results/deepeval_{date}.json, which the workflow's pass-rate
step reads to decide pass/fail (>= 80%).

Run:
    python eval/run_deepeval.py --limit 5
"""

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# openai/gpt-oss-120b's free tier caps at 8k TPM. Each metric call runs ~3-5k
# tokens, so 9 calls back-to-back on one memo blows through the limit almost
# immediately — pace them enough to let the rolling window recover.
#
# Known separate issue (not fixed by pacing): gpt-oss-120b is a reasoning model
# that sometimes wraps its answer in reasoning tokens DeepEval's plain-JSON
# prompt can't parse, surfacing as "Evaluation LLM outputted an invalid JSON."
# That's a DeepEval/reasoning-model prompt-format mismatch, not a rate-limit
# issue — scores[name] will be None for that metric on this entry either way.
METRIC_SLEEP_SECONDS = 30

from eval.deepeval_metrics import (
    make_memo_test_case,
    faithfulness_metric,
    hallucination_metric,
    answer_relevancy_metric,
    contextual_precision_metric,
    contextual_recall_metric,
    contextual_relevancy_metric,
    financial_verdict_geval,
    task_completion_geval,
    verdict_consistency_metric,
)

DATASET_PATH = Path("eval/writer_golden_dataset.json")
RESULTS_DIR = Path("eval/results")

METRICS = [
    ("Faithfulness", faithfulness_metric),
    ("Hallucination", hallucination_metric),
    ("AnswerRelevancy", answer_relevancy_metric),
    ("ContextualPrecision", contextual_precision_metric),
    ("ContextualRecall", contextual_recall_metric),
    ("ContextualRelevancy", contextual_relevancy_metric),
    ("FinancialVerdict", financial_verdict_geval),
    ("TaskCompletion", task_completion_geval),
    ("VerdictConsistency", verdict_consistency_metric),
]


def score_entry(entry: dict) -> dict:
    """Run every metric in METRICS against one golden-dataset entry."""
    test_case = make_memo_test_case(entry)
    scores = {}
    # Groq's free-tier 8k TPM cap is why every metric call needs pacing —
    # a paid Gemini key (see eval/deepeval_metrics.py's GeminiJudge) has no
    # such wall, so skip the wait entirely when one's configured.
    use_gemini = bool(os.getenv("GEMINI_API_KEY"))
    for i, (name, metric) in enumerate(METRICS):
        if i > 0 and not use_gemini:
            time.sleep(METRIC_SLEEP_SECONDS)
        try:
            metric.measure(test_case)
            scores[name] = bool(metric.success)
        except Exception as e:
            print(f"  [warn] {name} failed for {entry.get('ticker')}: {e}")
            scores[name] = None

    completed = [v for v in scores.values() if v is not None]
    # all() on an empty list is vacuously True — without this guard, a total
    # outage (every metric erroring, e.g. Groq's daily token quota exhausted)
    # silently reported "PASS" with zero real signal. Verified live 2026-09-03:
    # every metric 429'd and this still printed "1/1 passed (100%)".
    overall_pass = bool(completed) and all(completed)
    return {"ticker": entry.get("ticker"), "scores": scores, "overall_pass": overall_pass}


def main(limit: int | None) -> None:
    entries = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    if limit:
        entries = entries[:limit]

    print(f"\nWealthOS DeepEval Gate — {len(entries)} examples\n")

    results = []
    for entry in entries:
        result = score_entry(entry)
        results.append(result)
        status = "PASS" if result["overall_pass"] else "FAIL"
        print(f"  {result['ticker']:<12} {status}")

    total = len(results)
    passed = sum(1 for r in results if r["overall_pass"])
    print(f"\n  {passed}/{total} passed ({100 * passed // total if total else 0}%)")

    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / f"deepeval_{date.today().isoformat()}.json"
    out_path.write_text(
        json.dumps({"date": str(date.today()), "results": results}, indent=2),
        encoding="utf-8",
    )
    print(f"  Results saved -> {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Evaluate first N examples")
    args = parser.parse_args()
    main(args.limit)
