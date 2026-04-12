# eval/evaluate.py
"""
## WealthOS Writer Agent — Before/After Evaluation

Scores the baseline hand-written prompt vs the DSPy compiled prompt
using LLM-as-Judge (Groq). Logs everything to W&B Weave.

Run after dspy_optimizer.py has produced compiled_writer.json:
    python -m eval.evaluate

Prints a comparison table and posts results to your W&B project.
"""

import os
import json
import asyncio
import httpx
import weave
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY  = os.getenv("GROQ_API_KEY", "")
WANDB_API_KEY = os.getenv("WANDB_API_KEY", "")
DATASET_PATH  = "eval/writer_golden_dataset.json"
COMPILED_PATH = "eval/compiled_writer.json"

# Use last 5 examples as held-out test set (same split as optimizer)
TEST_SIZE = 5


# --- W&B Weave init -----------------------------------------------------------

weave.init("wealthOS")


# --- LLM-as-Judge -------------------------------------------------------------
# Scores a memo on 4 dimensions. Returns a dict of scores and overall average.

JUDGE_PROMPT = """You are evaluating an AI-generated investment memo.
Score it on these 4 dimensions. Return ONLY valid JSON, no explanation.

Memo to evaluate:
{memo}

Score each dimension 1-5:

- structure: Does it have all 7 sections? (Executive Summary, Financial Snapshot,
  Valuation Analysis, Risk Assessment, Portfolio Impact, Personal Finance Fit, Final Verdict)
- accuracy: Do all numbers appear to come from the data (no hallucinated figures)?
- personalization: Does it reference the user's actual INR surplus, health score, risk capacity?
- actionability: Does the Final Verdict give a clear recommendation and next step?

Return exactly this JSON:
{{"structure": X, "accuracy": X, "personalization": X, "actionability": X}}"""


@weave.op()
async def judge_memo(memo: str, client: httpx.AsyncClient) -> dict:
    """Calls Groq to score a memo. Returns scores dict."""
    resp = await client.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": JUDGE_PROMPT.format(memo=memo[:3000])}],
            "max_tokens": 100,
            "temperature": 0.0,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"].strip()

    try:
        scores = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback if model adds extra text
        import re
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        scores = json.loads(match.group()) if match else {}

    scores["overall"] = round(sum(scores.values()) / len(scores), 2) if scores else 0.0
    return scores


# --- Baseline writer ----------------------------------------------------------
# Calls the current hand-written writer agent (no DSPy).

@weave.op()
async def run_baseline(ticker: str, context: dict, client: httpx.AsyncClient) -> str:
    """Generates a memo using the baseline hand-written prompt."""
    prompt = f"""Write a 7-section investment memo for {ticker}.

Financial data:   {context['financial_snapshot']}
Risk assessment:  {context['risk_report']}
Valuation models: {context['code_output']}
Personal finance: {context['personal_finance']}
Rebalancing:      {context['rebalance_suggestion']}

Include: Executive Summary, Financial Snapshot, Valuation Analysis,
Risk Assessment, Portfolio Impact, Personal Finance Fit, Final Verdict.
Bold all key numbers. Lead Executive Summary with BUY/HOLD/AVOID."""

    resp = await client.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1500,
            "temperature": 0.3,
        },
        timeout=60.0,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


# --- DSPy compiled writer -----------------------------------------------------

@weave.op()
def run_compiled(context: dict) -> str:
    """Generates a memo using the DSPy compiled prompt."""
    import dspy
    from eval.dspy_optimizer import MemoWriter

    # Load compiled program
    program = MemoWriter()
    program.load(COMPILED_PATH)

    # Configure DSPy lm (needed even just for inference)
    lm = dspy.LM("groq/llama-3.3-70b-versatile", api_key=GROQ_API_KEY, max_tokens=1500, temperature=0.3)
    dspy.configure(lm=lm)

    result = program(
        financial_context = context["financial_snapshot"],
        risk_context      = context["risk_report"],
        code_context      = context["code_output"],
        personal_context  = context["personal_finance"],
        rebalance_context = context["rebalance_suggestion"],
    )
    return result.memo


# --- Main ---------------------------------------------------------------------

async def main():
    print("\n## WealthOS — Writer Agent Evaluation\n")
    print(f"{'Ticker':<12} {'Baseline':>10} {'DSPy':>10} {'Delta':>10}")
    print("-" * 46)

    with open(DATASET_PATH) as f:
        data = json.load(f)

    test_examples = data[-TEST_SIZE:]   # last 5 = held-out test set

    baseline_scores = []
    compiled_scores = []

    async with httpx.AsyncClient() as client:
        for ex in test_examples:
            ticker  = ex["ticker"]
            context = ex["context"]

            # Run both versions
            baseline_memo = await run_baseline(ticker, context, client)
            compiled_memo = run_compiled(context)

            # Score both
            b_scores = await judge_memo(baseline_memo, client)
            c_scores = await judge_memo(compiled_memo, client)

            b_avg = b_scores.get("overall", 0.0)
            c_avg = c_scores.get("overall", 0.0)
            delta = c_avg - b_avg

            baseline_scores.append(b_avg)
            compiled_scores.append(c_avg)

            print(f"{ticker:<12} {b_avg:>10.2f} {c_avg:>10.2f} {delta:>+10.2f}")

    # Summary
    b_mean = sum(baseline_scores) / len(baseline_scores)
    c_mean = sum(compiled_scores) / len(compiled_scores)
    print("-" * 46)
    print(f"{'AVERAGE':<12} {b_mean:>10.2f} {c_mean:>10.2f} {c_mean - b_mean:>+10.2f}")

    print(f"\n✅ Results logged to W&B Weave — project: wealthOS")
    print(f"   Baseline avg : {b_mean:.2f} / 5.0")
    print(f"   DSPy avg     : {c_mean:.2f} / 5.0")
    print(f"   Improvement  : {((c_mean - b_mean) / b_mean * 100):.1f}%\n")


if __name__ == "__main__":
    asyncio.run(main())