"""
# llm_client.py - the one place WealthOS talks to a language model

- **Primary**: Groq (`GROQ_API_KEY`, model `LLM_MODEL`, default `openai/gpt-oss-120b`).
- **Fallback**: OpenRouter (`OPENROUTER_API_KEY`, model `LLM_FALLBACK_MODEL`), used only if the primary fails.
- One key per provider.
- **Daily cost cap** (`WEALTHOS_MAX_DAILY_COST_USD`, default $5): calls stop once it is reached.
- **Usage log**: every call's tokens and cost go to the `llm_usage` table, with the user it was for.
- `call_json(...)`: ask for JSON matching a Pydantic model; the answer is checked, and wrong answers get one retry
  with the error shown to the model. Nothing unchecked comes out of it.

Reasoning models (gpt-oss) can spend the whole token budget "thinking" and return empty text; we ask for
`reasoning_effort="low"` and retry once on an empty answer.
"""

import json
import logging
import os
import re

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

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

# $ per 1M tokens (input, output): Groq gpt-oss-120b list price (the free tier is billed $0, so this is an upper
# bound); OpenRouter ":free" models cost nothing, other OpenRouter models are estimated at gpt-oss-20b's price.
_PRICES = {"groq": (0.15, 0.60), "openrouter": (0.0, 0.0) if OPENROUTER_MODEL.endswith(":free") else (0.018, 0.09)}
_MAX_DAILY_COST_USD = float(os.getenv("WEALTHOS_MAX_DAILY_COST_USD", "5.0"))
_PROVIDERS = [
    ("groq", "https://api.groq.com/openai/v1/chat/completions", GROQ_API_KEY),
    ("openrouter", "https://openrouter.ai/api/v1/chat/completions", OPENROUTER_API_KEY),
]

# The Postgres URL in the form asyncpg wants, or "" when no database is configured.
def _db_url() -> str:
    return os.getenv("WEALTHOS_DB_URL", "").replace("postgresql+asyncpg://", "postgresql://")


# Stop if the last 24 hours of LLM spend reached the cap. Allows the call if the DB can't be checked
# (an optional safety check must never take the app down). ponytail: no lock, two parallel calls can both pass.
async def _check_cost_cap():
    if not _db_url():
        return
    try:
        import asyncpg
        conn = await asyncpg.connect(_db_url(), timeout=5)
        try:
            total = await conn.fetchval("SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM llm_usage "
                                        "WHERE created_at > NOW() - INTERVAL '1 day'")
        finally:
            await conn.close()
    except Exception as e:
        logger.warning("[llm_client] Cost cap check unavailable, allowing call: %s", e)
        return
    if total is not None and float(total) >= _MAX_DAILY_COST_USD:
        raise CostCapReached(f"Daily LLM usage cap exceeded: ${float(total):.4f} spent in the last 24h, "
                           f"limit is ${_MAX_DAILY_COST_USD:.2f}.")


# Log a call's tokens and cost, and save them to the llm_usage table (best effort: never breaks a call).
async def _track_usage(usage: dict, model: str, provider: str, user_id: str | None):
    prompt, completion = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    cost_in, cost_out = _PRICES.get(provider, _PRICES["groq"])
    cost = prompt / 1e6 * cost_in + completion / 1e6 * cost_out
    logger.info("[llm] %s/%s prompt=%d completion=%d cost=$%.6f", provider, model, prompt, completion, cost)
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
    await _check_cost_cap()
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    async with httpx.AsyncClient() as client:
        for provider, url, key in _PROVIDERS:
            if not key:
                continue
            groq = provider == "groq"
            model = GROQ_MODEL if groq else OPENROUTER_MODEL
            payload = {"model": model, "messages": messages, "temperature": temperature, "reasoning_effort": "low",
                       "max_tokens": max_tokens if groq else max(max_tokens, _OPENROUTER_MIN_TOKENS)}
            for attempt in (1, 2):  # second try only when the answer came back empty
                try:
                    resp = await client.post(url, headers={"Authorization": f"Bearer {key}"}, json=payload, timeout=30.0)
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as e:
                    logger.warning("[llm_client] %s failed: %s", provider, e)
                    break
                if "usage" in data:
                    await _track_usage(data["usage"], model, provider, user_id)
                content = (data["choices"][0]["message"].get("content") or "").strip()
                if content:
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
