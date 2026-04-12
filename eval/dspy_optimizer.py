# eval/dspy_optimizer.py
"""
## DSPy Prompt Optimizer — WealthOS Writer Agent

Reads the golden dataset, trains a BootstrapFewShot optimizer,
and saves the compiled prompt to `eval/compiled_writer.json`.

Run this once:
    python -m eval.dspy_optimizer

The writer agent then auto-loads the compiled prompt on startup.
"""

import os
import time
import json
import dspy
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
DATASET      = "eval/writer_golden_dataset.json"
OUTPUT       = "eval/compiled_writer.json"


# --- DSPy signature -----------------------------------------------------------

class WriteMemo(dspy.Signature):
    """
    Write a personalized 7-section investment memo for a retail investor.

    Required sections: Executive Summary, Financial Snapshot, Valuation Analysis,
    Risk Assessment, Portfolio Impact, Personal Finance Fit, Final Verdict.

    Rules:
    - Lead the Executive Summary with the verdict word (BUY / HOLD / AVOID)
    - Reference the user's actual INR surplus and health score by number
    - Every figure in the memo must come from the provided inputs — no invented numbers
    - Bold all key numbers using **markdown bold**
    - Final Verdict must give exactly 3 numbered reasons
    """
    financial_context : str = dspy.InputField(desc="Revenue, net income, price, P/E, debt, FCF, growth rate")
    risk_context      : str = dspy.InputField(desc="Risk score, grade, recommendation, risk factors, personal adjustment")
    code_context      : str = dspy.InputField(desc="DCF intrinsic value, Monte Carlo bear/median/bull, probability of gain")
    personal_context  : str = dspy.InputField(desc="INR income, monthly surplus, debt burden, health score, risk capacity")
    rebalance_context : str = dspy.InputField(desc="Portfolio value, new investment impact, suggested buy/sell actions")
    memo              : str = dspy.OutputField(desc="Complete markdown investment memo with all 7 sections")


# --- DSPy module --------------------------------------------------------------

class MemoWriter(dspy.Module):
    def __init__(self):
        super().__init__()
        self.write = dspy.Predict(WriteMemo)

    def forward(self, financial_context, risk_context, code_context, personal_context, rebalance_context):
        return self.write(
            financial_context=financial_context,
            risk_context=risk_context,
            code_context=code_context,
            personal_context=personal_context,
            rebalance_context=rebalance_context,
        )


# --- Quality metric -----------------------------------------------------------

REQUIRED_SECTIONS = [
    "Executive Summary",
    "Financial Snapshot",
    "Valuation Analysis",
    "Risk Assessment",
    "Portfolio Impact",
    "Personal Finance Fit",
    "Final Verdict",
]

def memo_quality_metric(example, pred, trace=None):
    memo = pred.memo or ""
    sections_present = sum(1 for s in REQUIRED_SECTIONS if s in memo)
    verdict_present  = any(v in memo[:200].upper() for v in ["BUY", "HOLD", "AVOID"])
    score = sections_present / len(REQUIRED_SECTIONS)
    if verdict_present:
        score += 0.1
    return score >= 0.85


# --- Load dataset -------------------------------------------------------------

def load_dataset():
    with open(DATASET) as f:
        data = json.load(f)

    examples = []
    for ex in data:
        ctx = ex["context"]
        examples.append(
            dspy.Example(
                financial_context = ctx["financial_snapshot"],
                risk_context      = ctx["risk_report"],
                code_context      = ctx["code_output"],
                personal_context  = ctx["personal_finance"],
                rebalance_context = ctx["rebalance_suggestion"],
                memo              = ex["memo"],
            ).with_inputs(
                "financial_context", "risk_context", "code_context",
                "personal_context", "rebalance_context"
            )
        )

    return examples[:10], examples[10:]


# --- Main ---------------------------------------------------------------------

def main():
    print("\n## WealthOS — DSPy Prompt Optimizer\n")

    # llama-3.1-8b-instant has 30k TPM on Groq free tier vs 12k for the 70b model.
    # Good enough for optimization — the compiled few-shot examples are what matter,
    # not the model used during compilation.
    lm = dspy.LM(
        "groq/llama-3.1-8b-instant",
        api_key=GROQ_API_KEY,
        max_tokens=1500,
        temperature=0.3,
    )
    dspy.configure(lm=lm)
    print("✅ DSPy configured — groq/llama-3.1-8b-instant (30k TPM)")

    trainset, devset = load_dataset()
    print(f"✅ Dataset loaded — {len(trainset)} train, {len(devset)} dev examples")

    print("\nCompiling with BootstrapFewShot...\n")

    optimizer = dspy.BootstrapFewShot(
        metric=memo_quality_metric,
        max_bootstrapped_demos=3,
        max_labeled_demos=3,
    )

    baseline = MemoWriter()
    compiled = optimizer.compile(baseline, trainset=trainset)

    # Save FIRST — before the dev eval, so a rate limit crash doesn't lose the compiled program
    compiled.save(OUTPUT)
    print(f"\n✅ Compiled program saved → {OUTPUT}")

    # Dev set evaluation — 15s delay between calls to avoid TPM rate limit
    print("\n## Dev set evaluation\n")
    scores = []

    for i, ex in enumerate(devset):
        print(f"  [{i+1}/{len(devset)}] evaluating...")
        try:
            pred   = compiled(
                financial_context = ex.financial_context,
                risk_context      = ex.risk_context,
                code_context      = ex.code_context,
                personal_context  = ex.personal_context,
                rebalance_context = ex.rebalance_context,
            )
            passed = memo_quality_metric(ex, pred)
            scores.append(passed)
            print(f"         {'✅' if passed else '❌'} quality check")
        except Exception as e:
            print(f"         ⚠️  rate limited, skipping: {str(e)[:80]}")
            scores.append(False)

        if i < len(devset) - 1:
            time.sleep(15)

    if scores:
        print(f"\nDev accuracy: {sum(scores)}/{len(scores)}")

    print(f"\n✅ Done. Run `python -m eval.evaluate` for before/after scores.")
    print("   Run `python -m graph.graph TSLA` — writer will now use DSPy path.\n")


if __name__ == "__main__":
    main()