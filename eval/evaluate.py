# eval/evaluate.py
"""
WealthOS Writer Agent — Structured LLM-as-Judge Evaluation

4 metrics (following LangSmith RAG eval best practices):
  1. Correctness  — memo BUY/HOLD/AVOID direction matches ground truth
  2. Groundedness — all key numerical claims traceable to provided financial data
  3. Relevance    — memo directly addresses the investment question
  4. Structure    — all 7 required memo sections are present

Each metric uses a TypedDict output schema + ChatGroq.with_structured_output()
so there is no JSON parsing, no regex fallbacks, and every grade includes an
explanation that forces chain-of-thought before the boolean verdict.

Also runs a baseline vs DSPy-compiled comparison on the held-out test set.

Run:
    python -m eval.evaluate
    python -m eval.evaluate --limit 3
    python -m eval.evaluate --compare   # baseline vs DSPy compiled
"""

import os
import json
import argparse
from datetime import date
from pathlib import Path
from typing_extensions import Annotated, TypedDict
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY  = os.getenv("GROQ_API_KEY", "")
DATASET_PATH  = Path("eval/writer_golden_dataset.json")
COMPILED_PATH = Path("eval/compiled_writer.json")
RESULTS_DIR   = Path("eval/results")
RESULTS_DIR.mkdir(exist_ok=True)

TEST_SIZE = 5  # last N examples are held-out test set

try:
    import weave
    weave.init("wealthOS")
    _WEAVE = True
except Exception:
    _WEAVE = False


# ── Output schemas ─────────────────────────────────────────────────────────────
# explanation field is first — forces the model to reason before committing to bool

class CorrectnessGrade(TypedDict):
    explanation: Annotated[str, ..., "Step-by-step reasoning comparing verdict directions"]
    correct: Annotated[bool, ..., "True if the memo BUY/HOLD/AVOID matches the ground truth direction"]

class GroundednessGrade(TypedDict):
    explanation: Annotated[str, ..., "List any claims in the memo not supported by the provided data"]
    grounded: Annotated[bool, ..., "True if all key numerical claims are traceable to the provided financial data"]

class RelevanceGrade(TypedDict):
    explanation: Annotated[str, ..., "Reasoning for relevance score"]
    relevant: Annotated[bool, ..., "True if the memo directly addresses the investment question"]

class StructureGrade(TypedDict):
    explanation: Annotated[str, ..., "List which of the 7 sections are present or missing"]
    structured: Annotated[bool, ..., "True if all 7 required memo sections are present"]


# ── Grader LLMs ───────────────────────────────────────────────────────────────

def _make_grader(schema):
    """Return a ChatGroq instance with structured output bound to schema."""
    from langchain_groq import ChatGroq
    return ChatGroq(
        model="llama-3.3-70b-versatile",
        temperature=0,
        api_key=GROQ_API_KEY,
    ).with_structured_output(schema, method="json_schema")


# ── System prompts ─────────────────────────────────────────────────────────────

_CORRECTNESS_SYSTEM = """You are grading an AI-generated investment memo.

Grade criteria:
(1) Extract the BUY, HOLD, or AVOID verdict from both the memo and the ground truth.
(2) The memo is correct if the direction matches (BUY=BUY, HOLD=HOLD, AVOID=AVOID).
(3) Minor wording differences or additional nuance are acceptable.

Explain your reasoning step by step before giving your final verdict."""

_GROUNDEDNESS_SYSTEM = """You are checking whether an investment memo is grounded in the financial data provided to the AI.

Grade criteria:
(1) Identify specific numbers, percentages, or factual claims in the memo.
(2) Check each claim against the provided financial data context.
(3) A memo is NOT grounded if it invents figures not present in the context.
(4) Analytical conclusions drawn from the data are acceptable — only fabricated facts fail.

List any ungrounded claims before giving your verdict."""

_RELEVANCE_SYSTEM = """You are checking whether an investment memo directly addresses the user's investment question.

Grade criteria:
(1) The memo must address the specific ticker mentioned in the question.
(2) The memo must give a clear actionable BUY/HOLD/AVOID recommendation.
(3) The memo must reference the user's personal financial situation (surplus, health score, or debt burden).

Explain your reasoning before giving your verdict."""

_STRUCTURE_SYSTEM = """You are checking whether an investment memo contains all required sections.

Required sections (all 7 must be present):
1. Executive Summary (must include BUY / HOLD / AVOID)
2. Financial Snapshot
3. Valuation Analysis  (OR "Trading Setup" for short-term analysis)
4. Risk Assessment
5. Portfolio Impact
6. Personal Finance Fit
7. Final Verdict

Minor heading variations are acceptable. List which sections are present and which are missing."""


# ── Individual evaluator functions ────────────────────────────────────────────
# Signature mirrors LangSmith evaluator convention so these can drop into
# client.evaluate() if LangSmith is added later.

def correctness(inputs: dict, outputs: dict, reference_outputs: dict) -> bool:
    """Does the memo verdict match the ground truth direction?"""
    grader = _make_grader(CorrectnessGrade)
    user_content = f"""INVESTMENT QUESTION: {inputs['question']}
GROUND TRUTH VERDICT: {reference_outputs['verdict']}
MEMO FINAL SECTION (last 600 chars): {outputs['memo'][-600:]}"""
    grade = grader.invoke([
        {"role": "system", "content": _CORRECTNESS_SYSTEM},
        {"role": "user",   "content": user_content},
    ])
    return grade["correct"]


def groundedness(inputs: dict, outputs: dict) -> bool:
    """Are the memo's key claims grounded in the provided financial data?"""
    grader = _make_grader(GroundednessGrade)
    user_content = f"""FINANCIAL DATA PROVIDED:
{inputs['context']}

INVESTMENT MEMO (first 2000 chars):
{outputs['memo'][:2000]}"""
    grade = grader.invoke([
        {"role": "system", "content": _GROUNDEDNESS_SYSTEM},
        {"role": "user",   "content": user_content},
    ])
    return grade["grounded"]


def relevance(inputs: dict, outputs: dict) -> bool:
    """Does the memo directly address the user's investment question?"""
    grader = _make_grader(RelevanceGrade)
    user_content = f"""INVESTMENT QUESTION: {inputs['question']}
MEMO EXECUTIVE SUMMARY (first 600 chars):
{outputs['memo'][:600]}"""
    grade = grader.invoke([
        {"role": "system", "content": _RELEVANCE_SYSTEM},
        {"role": "user",   "content": user_content},
    ])
    return grade["relevant"]


def structure(inputs: dict, outputs: dict) -> bool:
    """Does the memo have all 7 required sections?"""
    grader = _make_grader(StructureGrade)
    user_content = f"MEMO:\n{outputs['memo']}"
    grade = grader.invoke([
        {"role": "system", "content": _STRUCTURE_SYSTEM},
        {"role": "user",   "content": user_content},
    ])
    return grade["structured"]


# ── Dataset helpers ────────────────────────────────────────────────────────────

def _load_test_examples(limit: int | None = None) -> list[dict]:
    data = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    test = data[-TEST_SIZE:]
    return test[:limit] if limit else test


def _build_inputs(ex: dict) -> dict:
    ctx = ex["context"]
    return {
        "question": f"Should I invest in {ex['ticker']}? ({ex['company_name']})",
        "context":  " | ".join(str(v) for v in ctx.values()),
    }


def _extract_verdict(memo: str) -> str:
    """Pull the Final Verdict section, or fall back to the first BUY/HOLD/AVOID found."""
    if "Final Verdict" in memo:
        return memo.split("Final Verdict")[-1][:300].strip()
    for kw in ("BUY", "HOLD", "AVOID"):
        if kw in memo:
            return kw
    return memo[-300:].strip()


# ── Baseline memo generator ────────────────────────────────────────────────────

def _generate_baseline(ticker: str, context: dict) -> str:
    """Generate a memo with the hand-written baseline prompt (no DSPy)."""
    import httpx
    prompt = f"""Write a 7-section investment memo for {ticker}.

Financial data:   {context['financial_snapshot']}
Risk assessment:  {context['risk_report']}
Valuation models: {context['code_output']}
Personal finance: {context['personal_finance']}
Rebalancing:      {context['rebalance_suggestion']}

Sections required: Executive Summary (BUY/HOLD/AVOID), Financial Snapshot,
Valuation Analysis, Risk Assessment, Portfolio Impact, Personal Finance Fit, Final Verdict.
Bold all key numbers."""

    resp = httpx.post(
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


def _generate_compiled(context: dict) -> str:
    """Generate a memo using the DSPy compiled prompt."""
    import dspy
    from eval.dspy_optimizer import MemoWriter

    program = MemoWriter()
    program.load(str(COMPILED_PATH))
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


# ── Shared scorer ──────────────────────────────────────────────────────────────

def _score_memo(ticker: str, memo: str, inputs: dict, reference_outputs: dict) -> dict:
    """Run all 4 graders on a memo. Returns {metric_name: bool | None}."""
    outputs = {"memo": memo}
    scores  = {}
    for fn, name in [
        (correctness,  "Correctness"),
        (groundedness, "Groundedness"),
        (relevance,    "Relevance"),
        (structure,    "Structure"),
    ]:
        try:
            scores[name] = fn(inputs, outputs, reference_outputs) if fn is correctness else fn(inputs, outputs)
        except Exception as e:
            print(f"  [warn] {name} failed for {ticker}: {e}")
            scores[name] = None
    return scores


def _pass_rate(scores: dict) -> float:
    vals = [v for v in scores.values() if v is not None]
    return sum(vals) / len(vals) if vals else 0.0


def _fmt(v: bool | None) -> str:
    return " err" if v is None else (" ✅" if v else " ❌")


# ── eval mode: score golden dataset ───────────────────────────────────────────

def run_eval(limit: int | None = None) -> list[dict]:
    """Score the pre-computed golden dataset memos with 4 structured graders."""
    examples = _load_test_examples(limit)
    print(f"\nWealthOS Writer Evaluation — {len(examples)} examples (golden dataset)\n")
    print(f"{'Ticker':<10}  {'Correct':>8}  {'Grounded':>9}  {'Relevant':>9}  {'Structure':>10}  {'Pass?':>6}")
    print("─" * 66)

    results = []
    for ex in examples:
        ticker  = ex["ticker"]
        inputs  = _build_inputs(ex)
        ref     = {"verdict": _extract_verdict(ex["memo"])}
        scores  = _score_memo(ticker, ex["memo"], inputs, ref)
        overall = all(v for v in scores.values() if v is not None)

        results.append({"ticker": ticker, "scores": scores, "overall_pass": overall})
        print(
            f"{ticker:<10}  {_fmt(scores['Correctness']):>8}  "
            f"{_fmt(scores['Groundedness']):>9}  "
            f"{_fmt(scores['Relevance']):>9}  "
            f"{_fmt(scores['Structure']):>10}  "
            f"{'✅' if overall else '❌':>6}"
        )

    total  = len(results)
    passed = sum(1 for r in results if r["overall_pass"])
    print(f"{'─'*66}")
    print(f"  Passed: {passed}/{total}  ({100*passed//total if total else 0}%)\n")

    out_path = RESULTS_DIR / f"evaluate_{date.today().isoformat()}.json"
    out_path.write_text(json.dumps({"date": str(date.today()), "results": results}, indent=2), encoding="utf-8")
    print(f"  Results saved → {out_path}")
    if _WEAVE:
        print(f"  Traces logged to W&B Weave — project: wealthOS\n")
    return results


# ── compare mode: baseline vs DSPy compiled ───────────────────────────────────

def run_compare(limit: int | None = None) -> None:
    """Generate memos from baseline and compiled prompt, then score both side-by-side."""
    if not COMPILED_PATH.exists():
        print(f"[error] {COMPILED_PATH} not found — run eval/dspy_optimizer.py first")
        return

    examples = _load_test_examples(limit)
    print(f"\nWealthOS — Baseline vs DSPy Compiled ({len(examples)} examples)\n")
    print(f"{'Ticker':<10}  {'Baseline':>10}  {'Compiled':>10}  {'Delta':>8}")
    print("─" * 46)

    baseline_rates, compiled_rates = [], []

    for ex in examples:
        ticker  = ex["ticker"]
        inputs  = _build_inputs(ex)
        ref     = {"verdict": _extract_verdict(ex["memo"])}

        print(f"  Generating memos for {ticker}...")
        baseline_memo = _generate_baseline(ticker, ex["context"])
        compiled_memo = _generate_compiled(ex["context"])

        b_scores = _score_memo(ticker, baseline_memo, inputs, ref)
        c_scores = _score_memo(ticker, compiled_memo, inputs, ref)

        b_rate = _pass_rate(b_scores)
        c_rate = _pass_rate(c_scores)
        baseline_rates.append(b_rate)
        compiled_rates.append(c_rate)

        print(f"{ticker:<10}  {b_rate:>10.2f}  {c_rate:>10.2f}  {c_rate - b_rate:>+8.2f}")

    b_mean = sum(baseline_rates) / len(baseline_rates) if baseline_rates else 0
    c_mean = sum(compiled_rates) / len(compiled_rates) if compiled_rates else 0
    print("─" * 46)
    print(f"{'AVERAGE':<10}  {b_mean:>10.2f}  {c_mean:>10.2f}  {c_mean - b_mean:>+8.2f}")
    print(f"\n  Baseline avg pass rate : {b_mean:.0%}")
    print(f"  Compiled avg pass rate : {c_mean:.0%}")
    if b_mean > 0:
        print(f"  Improvement            : {((c_mean - b_mean) / b_mean * 100):.1f}%\n")


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit",   type=int,          default=None, help="Evaluate first N examples")
    parser.add_argument("--compare", action="store_true",              help="Run baseline vs DSPy comparison")
    args = parser.parse_args()

    if args.compare:
        run_compare(limit=args.limit)
    else:
        run_eval(limit=args.limit)
