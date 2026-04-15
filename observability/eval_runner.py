# observability/eval_runner.py
"""
Writer Agent Eval Runner

Runs 3 versions of the Writer Agent on the same test inputs and scores
each output with LLM-as-Judge. Results are logged to W&B Weave so you
get the before/after comparison table for your README.

The 3 versions:
  1. baseline      — hand-written prompts, no compiled_writer.json
  2. dspy_compiled — compiled_writer.json present (Phase 5)
  3. finetuned     — future Phase 11 (skipped for now, slot reserved)

## How to run
    python -m observability.eval_runner

## What it does
1. Loads 5 test tickers (or your golden dataset if it exists)
2. Runs the full writer pipeline for each ticker × each strategy
3. Scores every output with score_memo()
4. Logs results to Weave via log_eval_result()
5. Prints a comparison table to the terminal

## Output
You'll see something like:

  Strategy        | Structure | Accuracy | Personal | Actionable | Total
  baseline        |   3.4     |   3.8    |   2.1    |    3.2     | 12.5
  dspy_compiled   |   4.1     |   4.0    |   3.6    |    3.9     | 15.6

That table goes in your README.
"""

import asyncio
import json
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


# ── Test inputs ───────────────────────────────────────────────────────────────
# If you have a golden dataset from Phase 5, we load that.
# Otherwise we use these 5 tickers with fake personal finance data.

GOLDEN_DATASET_PATH = "eval/writer_golden_dataset.json"

DEFAULT_TEST_CASES = [
    {"ticker": "TSLA",    "personal_finance": {"monthly_income": 150000, "monthly_surplus": 30000, "debt_burden_ratio": 0.25, "health_score": {"total": 72, "grade": "Good"}, "risk_capacity": "medium", "investable_monthly": 20000}},
    {"ticker": "AAPL",    "personal_finance": {"monthly_income": 200000, "monthly_surplus": 50000, "debt_burden_ratio": 0.15, "health_score": {"total": 85, "grade": "Excellent"}, "risk_capacity": "high",   "investable_monthly": 40000}},
    {"ticker": "INFY.NS", "personal_finance": {"monthly_income": 120000, "monthly_surplus": 18000, "debt_burden_ratio": 0.40, "health_score": {"total": 55, "grade": "Fair"}, "risk_capacity": "low",    "investable_monthly": 10000}},
    {"ticker": "NVDA",    "personal_finance": {"monthly_income": 180000, "monthly_surplus": 40000, "debt_burden_ratio": 0.20, "health_score": {"total": 78, "grade": "Good"}, "risk_capacity": "high",   "investable_monthly": 35000}},
    {"ticker": "HDFC.NS", "personal_finance": {"monthly_income": 100000, "monthly_surplus": 22000, "debt_burden_ratio": 0.30, "health_score": {"total": 65, "grade": "Good"}, "risk_capacity": "medium", "investable_monthly": 15000}},
]


def load_test_cases() -> list[dict]:
    """Load golden dataset if it exists, otherwise use defaults."""
    if os.path.exists(GOLDEN_DATASET_PATH):
        with open(GOLDEN_DATASET_PATH) as f:
            data = json.load(f)
        print(f"[eval] Loaded {len(data)} cases from golden dataset")
        # Golden dataset format may differ — normalize it
        normalized = []
        for item in data[:5]:   # cap at 5 to keep eval fast
            normalized.append({
                "ticker":          item.get("ticker", "TSLA"),
                "personal_finance": item.get("personal_finance", DEFAULT_TEST_CASES[0]["personal_finance"]),
            })
        return normalized
    else:
        print(f"[eval] No golden dataset found at {GOLDEN_DATASET_PATH} — using 5 default test cases")
        return DEFAULT_TEST_CASES


# ── Strategy runner ───────────────────────────────────────────────────────────

async def run_writer_for_strategy(
    ticker: str,
    personal_finance: dict,
    strategy: str,
) -> str:
    """
    Runs the writer agent for a given strategy.

    strategy: "baseline" | "dspy_compiled"

    For "baseline": temporarily renames compiled_writer.json so it's not found,
    forcing the hand-written fallback path. Restores it after.

    For "dspy_compiled": just calls run_writer_agent normally — it auto-detects
    the compiled prompt if the file exists.
    """
    from agents.writer_agent import run_writer_agent
    from agents.data_agent   import run_data_agent
    from agents.risk_agent   import run_risk_agent

    print(f"\n[eval] Running {strategy} — {ticker}")

    # Get real agent data so memos have actual content to score
    try:
        snapshot = await run_data_agent(ticker, use_rag=False)
        risk     = await run_risk_agent(ticker=ticker, financial_snapshot=snapshot, personal_finance=personal_finance)
    except Exception as e:
        print(f"[eval] ⚠️  Agent data failed for {ticker}: {e} — using None")
        snapshot = None
        risk     = None

    compiled_path     = "eval/compiled_writer.json"
    compiled_path_tmp = "eval/compiled_writer.json.bak"

    # For baseline: hide the compiled prompt so the fallback path kicks in
    if strategy == "baseline" and os.path.exists(compiled_path):
        os.rename(compiled_path, compiled_path_tmp)

    try:
        memo = await run_writer_agent(
            ticker=ticker,
            financial_snapshot=snapshot,
            risk_report=risk,
            personal_finance=personal_finance,
        )
        return memo.full_memo or ""
    except Exception as e:
        print(f"[eval] ❌ Writer failed ({strategy} / {ticker}): {e}")
        return ""
    finally:
        # Always restore the compiled prompt
        if strategy == "baseline" and os.path.exists(compiled_path_tmp):
            os.rename(compiled_path_tmp, compiled_path)


# ── Main eval loop ────────────────────────────────────────────────────────────

async def run_eval():
    """
    Main eval loop.

    For each test case:
      1. Run baseline writer → score → log to Weave
      2. Run dspy_compiled writer → score → log to Weave

    Prints summary table at the end.
    """
    from observability.weave_config import score_memo, log_eval_result, init_weave

    init_weave()

    test_cases = load_test_cases()

    # Collect results for the summary table
    results = {
        "baseline":      [],
        "dspy_compiled": [],
    }

    compiled_exists = os.path.exists("eval/compiled_writer.json")
    strategies = ["baseline", "dspy_compiled"] if compiled_exists else ["baseline"]

    if not compiled_exists:
        print("[eval] ⚠️  compiled_writer.json not found — only running baseline strategy")
        print("[eval]     Run the DSPy optimizer first (Phase 5) to enable comparison")

    for case in test_cases:
        ticker   = case["ticker"]
        personal = case["personal_finance"]

        for strategy in strategies:
            memo_text = await run_writer_for_strategy(ticker, personal, strategy)

            if not memo_text:
                print(f"[eval] ⚠️  Skipping score — empty memo ({strategy} / {ticker})")
                continue

            scores = await score_memo(memo_text, ticker, personal)
            log_eval_result(
                prompt_strategy=strategy,
                ticker=ticker,
                scores=scores,
                memo_length=len(memo_text),
            )

            results[strategy].append(scores)
            print(f"[eval] ✅ {strategy:15} / {ticker:10} — total: {scores['total']}/20")

    # Print summary table
    _print_summary_table(results, strategies)

    # Save results to file for easy reference
    output_path = f"eval/eval_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    os.makedirs("eval", exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n[eval] Results saved to {output_path}")


def _print_summary_table(results: dict, strategies: list):
    """Prints a clean comparison table to the terminal."""
    dimensions = ["structure", "accuracy", "personalization", "actionability", "total"]

    print("\n" + "="*70)
    print("  WRITER AGENT EVAL RESULTS")
    print("="*70)
    print(f"  {'Strategy':<18} | {'Structure':>9} | {'Accuracy':>8} | {'Personal':>8} | {'Action':>6} | {'Total':>5}")
    print(f"  {'-'*18}-+-{'-'*9}-+-{'-'*8}-+-{'-'*8}-+-{'-'*6}-+-{'-'*5}")

    max_totals = {}
    for strategy in strategies:
        scores_list = results.get(strategy, [])
        if not scores_list:
            print(f"  {strategy:<18} | {'no data':>9}")
            continue

        def avg(dim):
            vals = [s.get(dim, 0) for s in scores_list]
            return round(sum(vals) / len(vals), 1) if vals else 0

        s = avg("structure")
        a = avg("accuracy")
        p = avg("personalization")
        ac = avg("actionability")
        t = avg("total")
        max_totals[strategy] = t

        print(f"  {strategy:<18} | {s:>9} | {a:>8} | {p:>8} | {ac:>6} | {t:>5}")

    print("="*70)

    # Show improvement if both strategies ran
    if "baseline" in max_totals and "dspy_compiled" in max_totals:
        improvement = round(max_totals["dspy_compiled"] - max_totals["baseline"], 1)
        direction   = "improvement" if improvement > 0 else "regression"
        print(f"\n  DSPy vs baseline: {'+' if improvement > 0 else ''}{improvement} points ({direction})")

    print()


# ── CLI entrypoint ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\nWealthOS — Writer Agent Eval Runner")
    print("Comparing baseline vs DSPy-compiled prompt quality\n")
    asyncio.run(run_eval())