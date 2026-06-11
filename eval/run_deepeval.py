#!/usr/bin/env python
# eval/run_deepeval.py
"""
Runs DeepEval metrics against the WealthOS pipeline.

For each example in writer_golden_dataset.json:
  1. Builds a test case from the stored context + memo
  2. Scores it with Faithfulness, Answer Relevancy, Hallucination
  3. Prints a results table to terminal
  4. Saves full results to eval/results/deepeval_{date}.json

Usage:
    python eval/run_deepeval.py
    python eval/run_deepeval.py --limit 5   # run first 5 only
"""

import sys
import json
import argparse
from datetime import date
from pathlib import Path

# Make sure project root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DATASET_PATH = Path(__file__).parent / "writer_golden_dataset.json"
RESULTS_DIR  = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)


def context_chunks_from_example(example: dict) -> list[str]:
    """Pull the context fields into a flat list of strings."""
    ctx = example.get("context", {})
    return [v for v in ctx.values() if v]


def run_eval(limit: int | None = None):
    from eval.deepeval_metrics import get_metrics, build_test_case

    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    if limit:
        dataset = dataset[:limit]

    faithfulness, answer_relevancy, hallucination = get_metrics()
    metrics = [faithfulness, answer_relevancy, hallucination]
    metric_names = ["Faithfulness", "Answer Relevancy", "Hallucination"]

    results = []
    col_w = 30  # query column width

    # Header
    print(f"\n{'Query':<{col_w}}  {'Faithful':>10}  {'Relevancy':>10}  {'Hallucin.':>10}  {'Pass?':>6}")
    print("-" * (col_w + 46))

    for ex in dataset:
        ticker   = ex["ticker"]
        query    = f"[{ex['id']}] {ticker} — {ex['company_name']}"
        memo     = ex["memo"]
        chunks   = context_chunks_from_example(ex)
        test_case = build_test_case(query, memo, chunks)

        scores = {}
        passed = {}
        for metric, name in zip(metrics, metric_names):
            try:
                metric.measure(test_case)
                scores[name]  = round(metric.score, 3)
                passed[name]  = metric.is_successful()
            except Exception as e:
                scores[name]  = None
                passed[name]  = False
                print(f"  [warn] {name} failed for {ticker}: {e}")

        overall_pass = all(passed.values())
        row = {
            "id":          ex["id"],
            "ticker":      ticker,
            "query":       query,
            "scores":      scores,
            "passed":      passed,
            "overall_pass": overall_pass,
        }
        results.append(row)

        f_score = f"{scores['Faithfulness']:.3f}" if scores["Faithfulness"] is not None else "err"
        r_score = f"{scores['Answer Relevancy']:.3f}" if scores["Answer Relevancy"] is not None else "err"
        h_score = f"{scores['Hallucination']:.3f}" if scores["Hallucination"] is not None else "err"
        status  = "✅" if overall_pass else "❌"
        print(f"{query[:col_w]:<{col_w}}  {f_score:>10}  {r_score:>10}  {h_score:>10}  {status:>6}")

    # Summary
    total  = len(results)
    passed_count = sum(1 for r in results if r["overall_pass"])
    print(f"\n{'─'*(col_w + 46)}")
    print(f"  Passed: {passed_count}/{total}  ({100*passed_count//total if total else 0}%)")
    print()

    # Save results
    out_path = RESULTS_DIR / f"deepeval_{date.today().isoformat()}.json"
    out_path.write_text(json.dumps({"date": str(date.today()), "results": results}, indent=2), encoding="utf-8")
    print(f"  Results saved → {out_path}")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Evaluate first N examples only")
    args = parser.parse_args()
    run_eval(limit=args.limit)
