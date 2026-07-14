#!/usr/bin/env python
# eval/reliability_eval.py
"""
## Reliability Evaluation — WealthOS Phase 3

Tests whether the pipeline gives consistent answers across repeated runs.

---

### What it checks

| Test        | Method                                              | Pass threshold        |
|-------------|-----------------------------------------------------|-----------------------|
| `pass^k`    | Run same query k=5 times, count matching verdicts   | >= 4 of 5 runs agree  |
| `BERTScore` | Semantic similarity of memos across all 5 runs      | min F1 >= 0.85        |

---

### Why this matters

LLMs are non-deterministic. A pipeline that returns BUY on Monday and AVOID
on Tuesday for the same stock is not trustworthy, even if both memos look
reasonable in isolation. This test quantifies that instability.

`pass^k` catches verdict flips (the most visible failure).
`BERTScore` catches content drift — risk factors that change run-to-run even
when the verdict stays the same.

---

### Requirements

    pip install bert-score httpx

---

### Usage

    # Run 5x in-process for AAPL (default)
    python eval/reliability_eval.py

    # Custom ticker and k
    python eval/reliability_eval.py --ticker NVDA --runs 3

    # Call the deployed API instead of running in-process
    python eval/reliability_eval.py --api http://13.203.227.73:8000

    # Results saved to:
    #   eval/results/reliability_{ticker}_{date}.json
"""

import sys
import os
import json
import time
import asyncio
import argparse
from datetime import date
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

# Pass thresholds
CONSISTENCY_THRESHOLD = 0.80   # >= 4 of 5 runs must agree on verdict
BERT_MIN_F1           = 0.85   # minimum per-run BERTScore F1 vs first run


# ── Verdict extraction ────────────────────────────────────────────────────────

from eval._verdict_utils import extract_verdict


# ── Pipeline runner ───────────────────────────────────────────────────────────

async def _run_via_api(ticker: str, query: str, api_url: str) -> str:
    """Call the WealthOS REST API and return the memo."""
    import httpx
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            f"{api_url}/analyze",
            json={
                "ticker":               ticker,
                "query":                query,
                "user_id":              "00000000-0000-0000-0000-000000000001",
                "investment_horizon":   "long",
            },
        )
        resp.raise_for_status()
        return resp.json().get("memo", "")


async def _run_in_process(ticker: str, query: str) -> str:
    """Run the LangGraph pipeline in-process and return the memo."""
    from graph.graph import wealthos_graph
    result = await wealthos_graph.ainvoke({
        "query":               query,
        "tickers":             [ticker],
        "user_id":             "00000000-0000-0000-0000-000000000001",
        "investment_horizon":  "long",
        "messages":            [],
    })
    return result.get("final_memo", "")


async def run_pipeline(ticker: str, query: str, api_url: str | None) -> str:
    """Run one pipeline pass and return the memo string."""
    if api_url:
        return await _run_via_api(ticker, query, api_url)
    return await _run_in_process(ticker, query)


# ── BERTScore ─────────────────────────────────────────────────────────────────

def compute_bert_scores(memos: list[str]) -> dict:
    """
    Compute BERTScore F1 for each run vs the first run as reference.

    Returns dict with per-run F1 scores and aggregate stats.
    Requires: pip install bert-score
    """
    try:
        from bert_score import score as _bert_score
    except ImportError:
        return {"error": "bert-score not installed — run: pip install bert-score"}

    if len(memos) < 2:
        return {"error": "Need at least 2 runs to compute BERTScore"}

    refs = [memos[0]] * (len(memos) - 1)   # first memo is the reference
    hyps = memos[1:]                         # compare each subsequent run to it

    _, _, f1 = _bert_score(hyps, refs, lang="en", verbose=False)
    scores    = f1.tolist()

    return {
        "run_f1":  [round(s, 4) for s in scores],
        "mean_f1": round(sum(scores) / len(scores), 4),
        "min_f1":  round(min(scores), 4),
        "passed":  min(scores) >= BERT_MIN_F1,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

async def run_reliability_eval(
    ticker:  str        = "AAPL",
    query:   str        = "Should I invest in this stock? Give me a complete analysis.",
    k:       int        = 5,
    api_url: str | None = None,
) -> dict:
    """
    Run the same query k times and measure verdict stability + BERTScore.

    Args:
        ticker:  Stock ticker to analyse.
        query:   Investment query to repeat k times.
        k:       Number of runs (default: 5).
        api_url: REST API base URL, or None to run LangGraph in-process.

    Returns:
        Summary dict — also saved to eval/results/reliability_{ticker}_{date}.json.
    """
    print(f"\n## Reliability Evaluation — {ticker} x {k} runs")
    print(f"   Query : {query[:70]}...")
    print(f"   Mode  : {'API -> ' + api_url if api_url else 'in-process LangGraph'}\n")

    memos    = []
    verdicts = []

    for i in range(k):
        print(f"  Run {i + 1}/{k}...", end=" ", flush=True)
        t0      = time.time()
        memo    = await run_pipeline(ticker, query, api_url)
        elapsed = time.time() - t0
        verdict = extract_verdict(memo)
        memos.append(memo)
        verdicts.append(verdict)
        print(f"verdict={verdict}  ({elapsed:.1f}s)")

    # pass^k — fraction of runs that match the most common verdict
    counts       = Counter(v for v in verdicts if v)
    mode_verdict = counts.most_common(1)[0][0] if counts else None
    mode_count   = counts[mode_verdict] if mode_verdict else 0
    consistency  = mode_count / k

    bert = compute_bert_scores(memos)

    summary = {
        "ticker":           ticker,
        "query":            query,
        "runs":             k,
        "verdicts":         verdicts,
        "mode_verdict":     mode_verdict,
        "consistency":      round(consistency, 4),
        "consistency_pass": consistency >= CONSISTENCY_THRESHOLD,
        "bert_score":       bert,
        "overall_pass":     consistency >= CONSISTENCY_THRESHOLD and bert.get("passed", False),
    }

    # Print results
    print(f"\n{'─' * 60}")
    print(f"  Verdicts    : {verdicts}")
    print(f"  Mode        : {mode_verdict} ({mode_count}/{k} runs)")
    print(f"  Consistency : {consistency:.0%}  {'PASS' if summary['consistency_pass'] else 'FAIL'}")
    if "mean_f1" in bert:
        print(f"  BERTScore   : mean={bert['mean_f1']:.4f}  min={bert['min_f1']:.4f}  "
              f"{'PASS' if bert.get('passed') else 'FAIL'}")
    elif "error" in bert:
        print(f"  BERTScore   : {bert['error']}")
    print(f"  Overall     : {'PASS' if summary['overall_pass'] else 'FAIL'}\n")

    # Save results
    out = RESULTS_DIR / f"reliability_{ticker}_{date.today().isoformat()}.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Saved -> {out}\n")

    return summary


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="WealthOS reliability evaluation (pass^k + BERTScore)"
    )
    parser.add_argument("--ticker", default="AAPL",
                        help="Stock ticker (default: AAPL)")
    parser.add_argument("--runs",   type=int, default=5,
                        help="Number of runs (default: 5)")
    parser.add_argument("--api",    default=None,
                        help="REST API base URL (default: run in-process)")
    parser.add_argument(
        "--query",
        default="Should I invest in this stock? Give me a complete analysis.",
        help="Query to repeat for each run",
    )
    args = parser.parse_args()

    asyncio.run(run_reliability_eval(
        ticker  = args.ticker,
        query   = args.query,
        k       = args.runs,
        api_url = args.api,
    ))
