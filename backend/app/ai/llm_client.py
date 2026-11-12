"""
# llm_client.py - the one place WealthOS talks to a language model

- **Primary**: Groq (`GROQ_API_KEY`, model `LLM_MODEL`, default `openai/gpt-oss-120b`). `LLM_BASE_URL` swaps Groq for
  any OpenAI-compatible server (e.g. Ollama at `http://localhost:11434/v1`; `GROQ_API_KEY` then only needs to be
  non-empty), priced at $0 and given `LLM_TIMEOUT_S` (default 30) per call; `LLM_REASONING_EFFORT` (default "low";
  "none" turns thinking off on Ollama). Used by the local-model eval run.
- **Fallback**: OpenRouter (`OPENROUTER_API_KEY`, model `LLM_FALLBACK_MODEL`), used only if the primary fails.
  A `:free` fallback is skipped in production unless `WEALTHOS_ALLOW_FREE_FALLBACK=1` (free endpoints may keep prompts).
- One key per provider.
- **Redaction**: every prompt goes through `redact_pii` here, so no call site can send raw PAN/phone/account numbers.
- **Daily cost caps** over the last 24h: per user (`WEALTHOS_MAX_DAILY_COST_USD_USER`, default $0.50) and for the
  whole server (`WEALTHOS_MAX_DAILY_COST_USD`, default $5). In production a budget that can't be checked refuses.
- **Usage log**: every call's tokens and cost go to the `llm_usage` table, with the user it was for.
- `call_json(...)`: ask for JSON matching a Pydantic model; the answer is checked, and wrong answers get one retry
  with the error shown to the model. Nothing unchecked comes out of it.

Reasoning models (gpt-oss) can spend the whole token budget "thinking" and return empty text; we ask for
`reasoning_effort="low"` and retry once on an empty answer.
"""

import contextvars
import json
import logging
import os
import re
import time

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

from app.core import settings
from app.ai.pii_redaction import redact_pii

load_dotenv()
logger = logging.getLogger(__name__)


# Raised when the day's AI budget is used up (the API answers 429; dashboards and metrics keep working).
class CostCapReached(RuntimeError):
    pass


GROQ_API_KEY       = os.getenv("GROQ_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
GROQ_MODEL         = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
# Free on OpenRouter (":free" models cost nothing, rate-limited); checked 2026-10-31: answers the planner's JSON correctly.
OPENROUTER_MODEL   = os.getenv("LLM_FALLBACK_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")
_OPENROUTER_MIN_TOKENS = 300
# The model that wrote the latest answer in this request or task (the fallback's name when Groq failed). Read it right
# after a call to record which model really answered; "" before any call. A ContextVar, so parallel requests don't mix.
SERVED_MODEL: contextvars.ContextVar[str] = contextvars.ContextVar("served_model", default="")
# Opt-in per-call log: when a caller (the eval runner) sets a list here, every provider reply appends
# {provider, model, prompt_tokens, completion_tokens, usd, seconds}. None (the default) records nothing.
CALL_LOG: contextvars.ContextVar[list | None] = contextvars.ContextVar("call_log", default=None)

# $ per 1M tokens (input, output): Groq gpt-oss-120b list price (the free tier is billed $0, so this is an upper
# bound); OpenRouter ":free" models cost nothing, other OpenRouter models are estimated at gpt-oss-20b's price.
_PRICES = {"groq": (0.15, 0.60), "local": (0.0, 0.0),
           "openrouter": (0.0, 0.0) if OPENROUTER_MODEL.endswith(":free") else (0.018, 0.09)}
_MAX_DAILY_COST_USD = float(os.getenv("WEALTHOS_MAX_DAILY_COST_USD", "5.0"))
_MAX_DAILY_COST_USD_USER = float(os.getenv("WEALTHOS_MAX_DAILY_COST_USD_USER", "0.50"))
_PROVIDERS = [
    ("local" if os.getenv("LLM_BASE_URL") else "groq",
     os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/") + "/chat/completions", GROQ_API_KEY),
    ("openrouter", "https://openrouter.ai/api/v1/chat/completions", OPENROUTER_API_KEY),
]

# May the fallback be used? A ":free" OpenRouter model can log or train on prompts, so production sends financial
# data there only with WEALTHOS_ALLOW_FREE_FALLBACK=1; a paid fallback model is always allowed.
def _free_fallback_ok() -> bool:
    if not OPENROUTER_MODEL.endswith(":free"):
        return True
    ok = os.getenv("WEALTHOS_ALLOW_FREE_FALLBACK", "0" if settings.env() == "production" else "1") == "1"
    if not ok:
        logger.info("[llm_client] skipping the free fallback %s in production (WEALTHOS_ALLOW_FREE_FALLBACK)", OPENROUTER_MODEL)
    return ok


# The Postgres URL in the form asyncpg wants, or "" when no database is configured.
def _db_url() -> str:
    return os.getenv("WEALTHOS_DB_URL", "").replace("postgresql+asyncpg://", "postgresql://")


# Last-24h LLM spend as (this user's, everyone's). Its own function so the budget check reads as one query.
async def _spend_last_day(user_id: str | None) -> tuple[float, float]:
    import asyncpg
    conn = await asyncpg.connect(_db_url(), timeout=5)
    try:
        row = await conn.fetchrow(
            "SELECT COALESCE(SUM(estimated_cost_usd) FILTER (WHERE user_id = $1), 0), COALESCE(SUM(estimated_cost_usd), 0) "
            "FROM llm_usage WHERE created_at > NOW() - INTERVAL '1 day'", user_id)
    finally:
        await conn.close()
    return float(row[0]), float(row[1])


# Stop if this user's or everyone's last-24h LLM spend reached its cap. If the DB can't be checked, production refuses
# the call (an unchecked budget is unbounded spend) and dev allows it with a warning.
# ponytail: no lock, two parallel calls can both pass (a few cents over); a per-user advisory lock fixes it.
async def _check_cost_cap(user_id: str | None = None):
    if not _db_url():
        return
    try:
        mine, total = await _spend_last_day(user_id)
    except Exception as e:
        if settings.env() == "production":
            logger.error("[llm_client] Cost cap check unavailable, refusing call: %s", e)
            raise CostCapReached("the AI budget can't be checked right now; try again shortly") from None
        logger.warning("[llm_client] Cost cap check unavailable, allowing call (not production): %s", e)
        return
    if user_id and mine >= _MAX_DAILY_COST_USD_USER:
        raise CostCapReached(f"You reached today's AI limit (${_MAX_DAILY_COST_USD_USER:.2f} in 24h). "
                             "Dashboards and metrics keep working.")
    if total >= _MAX_DAILY_COST_USD:
        raise CostCapReached("The server reached today's AI limit. Dashboards and metrics keep working.")


# Log a call's tokens and cost, and save them to the llm_usage table (best effort: never breaks a call).
async def _track_usage(usage: dict, model: str, provider: str, user_id: str | None, seconds: float = 0.0):
    prompt, completion = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    cost_in, cost_out = _PRICES.get(provider, _PRICES["groq"])
    cost = usage.get("cost") or prompt / 1e6 * cost_in + completion / 1e6 * cost_out  # OpenRouter reports the real cost
    logger.info("[llm] %s/%s prompt=%d completion=%d cost=$%.6f %.2fs", provider, model, prompt, completion, cost, seconds)
    if (log := CALL_LOG.get()) is not None:
        log.append({"provider": provider, "model": model, "prompt_tokens": prompt, "completion_tokens": completion,
                    "usd": cost, "seconds": round(seconds, 3)})
    if not _db_url():
        return
    try:
        import asyncpg
        conn = await asyncpg.connect(_db_url(), timeout=5)
        try:
            await conn.execute(
                "INSERT INTO llm_usage (provider, model, prompt_tokens, completion_tokens, total_tokens, estimated_cost_usd, user_id) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7)", provider, model, prompt, completion, prompt + completion, cost, user_id)
        finally:
            await conn.close()
    except Exception as e:
        logger.warning("[llm_client] Could not persist llm_usage row: %s", e)


# Ask the model. Returns the text, or "" if every provider failed.
async def call_llm(system: str, user: str, max_tokens: int = 500, temperature: float = 0.1,
                   user_id: str | None = None) -> str:
    await _check_cost_cap(user_id)
    # The one redaction choke point: nothing reaches a provider unredacted, whichever call site built the prompt.
    messages = [{"role": "system", "content": redact_pii(system)}, {"role": "user", "content": redact_pii(user)}]
    async with httpx.AsyncClient() as client:
        for provider, url, key in _PROVIDERS:
            if not key or (provider == "openrouter" and not _free_fallback_ok()):
                continue
            groq = provider != "openrouter"  # the primary slot: Groq, or the LLM_BASE_URL server
            model = GROQ_MODEL if groq else OPENROUTER_MODEL
            payload = {"model": model, "messages": messages, "temperature": temperature,
                       "reasoning_effort": os.getenv("LLM_REASONING_EFFORT", "low"),
                       "max_tokens": max_tokens if groq else max(max_tokens, _OPENROUTER_MIN_TOKENS)}
            for attempt in (1, 2):  # second try only when the answer came back empty
                start = time.perf_counter()
                try:
                    resp = await client.post(url, headers={"Authorization": f"Bearer {key}"}, json=payload,
                                             timeout=float(os.getenv("LLM_TIMEOUT_S", "30")))
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as e:
                    logger.warning("[llm_client] %s failed: %s", provider, e)
                    break
                if "usage" in data:
                    await _track_usage(data["usage"], model, provider, user_id, time.perf_counter() - start)
                content = (data["choices"][0]["message"].get("content") or "").strip()
                if content:
                    SERVED_MODEL.set(model)
                    return content
                logger.warning("[llm_client] %s returned empty content (attempt %d)", provider, attempt)
    logger.error("[llm_client] All providers failed or returned nothing (Groq %s, OpenRouter %s)",
                 "set" if GROQ_API_KEY else "not set", "set" if OPENROUTER_API_KEY else "not set")
    return ""


# Ask for JSON that must match `model_cls`; one retry with the validation error. Returns a checked object or None.
async def call_json(system: str, user: str, model_cls: type[BaseModel], max_tokens: int = 800,
                    user_id: str | None = None) -> BaseModel | None:
    schema = json.dumps(model_cls.model_json_schema())
    prompt = f"{system}\n\nAnswer with ONE JSON object only (no prose, no code fences) matching this JSON schema:\n{schema}"
    question = user
    for _ in (1, 2):
        raw = await call_llm(prompt, question, max_tokens=max_tokens, temperature=0, user_id=user_id)
        match = re.search(r"\{.*\}", raw or "", re.S)
        try:
            return model_cls.model_validate_json(match.group(0) if match else "")
        except (ValidationError, ValueError) as e:
            question = f"{user}\n\nYour previous answer was invalid: {str(e)[:500]}\nAnswer again with valid JSON only."
    return None
