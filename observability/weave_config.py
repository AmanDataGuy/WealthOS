# observability/weave_config.py
"""
W&B Weave for WealthOS — eval quality tracking only.

Answers: "Are the Writer Agent outputs actually good?"
Compares baseline prompts vs DSPy-compiled prompts using LLM-as-Judge.

Pipeline tracing is handled by LangSmith. This file owns eval scoring only.

## Setup
    WANDB_API_KEY=...   (in .env)

## Usage (in eval_runner.py)
    from observability.weave_config import init_weave, score_memo, log_eval_result
    init_weave()
    scores = await score_memo(memo, "AAPL", personal_finance)
    log_eval_result("dspy_compiled", "AAPL", scores, len(memo))
"""

import os
import functools
from dotenv import load_dotenv

try:
    import wandb
    WANDB_AVAILABLE = True
except ImportError:
    WANDB_AVAILABLE = False

load_dotenv()

WEAVE_ENABLED = bool(os.getenv("WANDB_API_KEY"))
WEAVE_PROJECT = "WealthOS"


# ── Startup init ──────────────────────────────────────────────────────────────

def init_weave():
    """
    Call once at app startup.
    After this, any function decorated with @weave_op gets logged automatically.
    """
    if not WEAVE_ENABLED:
        print("[weave] ⚠️  WANDB_API_KEY not set — eval tracking disabled")
        return

    try:
        import weave
        weave.init(WEAVE_PROJECT)
        print(f"[weave] ✅ Initialized — project '{WEAVE_PROJECT}' ready")
    except ImportError:
        print("[weave] not installed — pip install weave")
    except Exception as e:
        print(f"[weave] ❌ Init failed: {e}")


# ── LLM-as-Judge scorer ───────────────────────────────────────────────────────

async def score_memo(memo: str, ticker: str, personal_finance: dict) -> dict:
    """
    Scores a Writer Agent memo on 4 dimensions using Groq as judge.

    Dimensions (each 1-5):
      - structure      : does it have all 7 required sections?
      - accuracy       : are the numbers and facts internally consistent?
      - personalization: does it reference the user's actual financial situation?
      - actionability  : does it end with a clear, specific next step?

    Returns a dict with individual scores and a total out of 20.

    Usage (in eval_runner.py):
        scores = await score_memo(memo.full_memo, "TSLA", personal_finance)
        print(scores["total"])  # e.g. 16.5
    """
    import httpx

    groq_api_key = os.getenv("GROQ_API_KEY", "")
    if not groq_api_key:
        print("[weave] ⚠️  GROQ_API_KEY not set — cannot score memo")
        return {"structure": 0, "accuracy": 0, "personalization": 0, "actionability": 0, "total": 0}

    judge_prompt = f"""You are evaluating an AI-generated investment memo for {ticker}.

Score it on these 4 dimensions. Return ONLY a JSON object, nothing else.

MEMO:
{memo[:3000]}

USER PERSONAL FINANCE CONTEXT:
Monthly surplus: ₹{personal_finance.get('monthly_surplus', 'N/A')}
Health score: {(personal_finance.get('health_score') or {}).get('total', 'N/A')}/100
Risk capacity: {personal_finance.get('risk_capacity', 'N/A')}

SCORING RUBRIC (1 = poor, 5 = excellent):

structure (1-5):
  5 = Has all 7 sections: Executive Summary, Financial Snapshot, Valuation, Risk, Portfolio Impact, Personal Finance Fit, Final Verdict
  3 = Has 4-5 sections
  1 = Missing most sections

accuracy (1-5):
  5 = All numbers are consistent (DCF, P/E, price all align), no contradictions
  3 = Minor inconsistencies
  1 = Numbers contradict each other or are clearly hallucinated

personalization (1-5):
  5 = Directly references user's surplus, health score, or risk capacity with specific numbers
  3 = Generic mention of personal finance but no specific numbers
  1 = Zero personalization, could be written for anyone

actionability (1-5):
  5 = Ends with a specific, concrete next step (e.g. "Buy X shares at $Y if it dips to $Z")
  3 = Vague next step ("consider investing")
  1 = No actionable guidance

Return exactly this JSON:
{{"structure": <score>, "accuracy": <score>, "personalization": <score>, "actionability": <score>}}"""

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {groq_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": judge_prompt}],
                    "max_tokens": 100,
                    "temperature": 0.0,   # deterministic scoring
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()

            import json
            scores = json.loads(raw)
            scores["total"] = sum(scores.values())
            return scores

    except Exception as e:
        print(f"[weave] ⚠️  Scoring failed: {e}")
        return {"structure": 0, "accuracy": 0, "personalization": 0, "actionability": 0, "total": 0}


def log_eval_result(
    prompt_strategy: str,
    ticker: str,
    scores: dict,
    memo_length: int,
):
    """
    Logs one eval result to Weave so it appears in the comparison table.

    Call this inside eval_runner.py after scoring each memo.

    prompt_strategy: "baseline" | "dspy_compiled" | "finetuned"
    """
    if not WEAVE_ENABLED:
        return

    try:
        # wandb.log() sends metrics to the W&B dashboard.
        # Weave traces are captured at the score_memo level via init_weave().
        if WANDB_AVAILABLE:
            wandb.log({
                "prompt_strategy": prompt_strategy,
                "ticker":          ticker,
                "score_structure":        scores.get("structure", 0),
                "score_accuracy":         scores.get("accuracy", 0),
                "score_personalization":  scores.get("personalization", 0),
                "score_actionability":    scores.get("actionability", 0),
                "score_total":            scores.get("total", 0),
                "memo_length_chars":      memo_length,
            })
        print(f"[weave] 📊 Logged — {prompt_strategy} / {ticker} — total: {scores.get('total', 0)}/20")
    except Exception as e:
        print(f"[weave] ⚠️  Could not log result: {e}")