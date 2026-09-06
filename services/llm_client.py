# services/llm_client.py
"""
Shared LLM client for WealthOS agents.
Calls Groq API (openai/gpt-oss-120b). Returns empty string if all keys fail.
"""

import os
import logging
import httpx
from dotenv import load_dotenv

load_dotenv()

# All configured Groq keys, in priority order, with empty values filtered out.
_GROQ_KEYS = [
    k for k in [
        os.getenv("GROQ_API_KEY",   ""),
        os.getenv("GROQ_API_KEY_2", ""),
        os.getenv("GROQ_API_KEY_3", ""),
    ] if k
]

# Fallback provider, tried only if every Groq key fails. OpenRouter's API is
# OpenAI-compatible (same request/response shape as Groq's), verified live
# 2026-08-20 — openai/gpt-oss-20b:free responds correctly. It's a reasoning
# model: completion_tokens_details showed 32 reasoning tokens consumed before
# any visible content on a trivial prompt, so the fallback call pads
# max_tokens rather than reusing the caller's original (often small) budget.
OPENROUTER_API_KEY     = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL       = "openai/gpt-oss-20b:free"
_OPENROUTER_MIN_TOKENS = 300

# Single source of truth for Groq model IDs — every agent/eval script should
# import these rather than hardcoding the string locally. When Groq deprecated
# llama-3.3-70b-versatile and llama-3.1-8b-instant (2026-08-19), the model name
# had to be fixed in 11 separate files because nothing imported from here yet.
GROQ_MODEL      = "openai/gpt-oss-120b"  # primary — reasoning, writing, judging
GROQ_MODEL_FAST = "openai/gpt-oss-20b"   # cheap/fast — classification only

# Groq pricing for openai/gpt-oss-120b (per 1M tokens)
_COST_INPUT_PER_M  = 0.15
_COST_OUTPUT_PER_M = 0.60

# Session-level running totals — resets on process restart
_session_cost = {
    "prompt_tokens":      0,
    "completion_tokens":  0,
    "total_tokens":       0,
    "estimated_cost_usd": 0.0,
    "calls":              0,
}

logger = logging.getLogger(__name__)


def get_session_cost() -> dict:
    """Return a copy of token usage and cost accumulated this process lifetime."""
    return dict(_session_cost)


async def _track_usage(usage: dict, model: str, provider: str = "groq", cost_per_m: tuple[float, float] = None):
    """
    Update session totals and persist one row to llm_usage. Called after
    every successful LLM call (Groq or OpenRouter).

    Was in-memory only (_session_cost resets on every process restart) — no
    historical record existed anywhere, which is part of why the 2026-09-03
    Groq daily-quota exhaustion was only discovered by hitting a live 429
    instead of by watching usage climb toward the ceiling. Persistence here
    is best-effort: a DB outage must never break an LLM call, so failures
    are logged and swallowed, same pattern as market_server.py's cache.
    """
    prompt     = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    total      = usage.get("total_tokens", 0)
    cost_in, cost_out = cost_per_m or (_COST_INPUT_PER_M, _COST_OUTPUT_PER_M)
    cost       = (prompt / 1_000_000 * cost_in) + (completion / 1_000_000 * cost_out)

    _session_cost["prompt_tokens"]      += prompt
    _session_cost["completion_tokens"]  += completion
    _session_cost["total_tokens"]       += total
    _session_cost["estimated_cost_usd"] += cost
    _session_cost["calls"]              += 1

    logger.info(
        "[llm] %s — prompt=%d completion=%d total=%d cost=$%.6f | session: $%.4f (%d calls)",
        model, prompt, completion, total, cost,
        _session_cost["estimated_cost_usd"], _session_cost["calls"],
    )

    db_url = os.getenv("WEALTHOS_DB_URL", "").replace("postgresql+asyncpg://", "postgresql://")
    if not db_url:
        return
    try:
        import asyncpg
        conn = await asyncpg.connect(db_url, timeout=5)
        try:
            await conn.execute(
                """
                INSERT INTO llm_usage (provider, model, prompt_tokens, completion_tokens, total_tokens, estimated_cost_usd)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                provider, model, prompt, completion, total, cost,
            )
        finally:
            await conn.close()
    except Exception as e:
        logger.warning("[llm_client] Could not persist llm_usage row: %s", e)


async def call_llm(
    system: str,
    user: str,
    max_tokens: int = 500,
    temperature: float = 0.1,
    client: httpx.AsyncClient = None,
    model: str = None,
    tools: list[dict] = None,
    messages: list[dict] = None,
):
    """
    Call Groq API. Returns empty string if no keys are configured or all keys fail.
    Reuses httpx.AsyncClient if provided, otherwise creates a temporary one.
    Logs token usage and cost after every successful call.

    If `tools` is given, returns the raw assistant message dict (may contain
    `tool_calls`) instead of a plain string, and `messages` (full turn
    history) is used in place of the single system/user pair — needed for
    multi-turn tool-calling loops. No OpenRouter fallback when tools are
    requested; the free-tier fallback model doesn't reliably support it.
    """
    owns_client = False
    if client is None:
        client = httpx.AsyncClient()
        owns_client = True

    request_messages = messages or [
        {"role": "system", "content": system},
        {"role": "user",   "content": user},
    ]

    try:
        if _GROQ_KEYS:
            for idx, key in enumerate(_GROQ_KEYS, 1):
                logger.info("[llm] calling Groq with key %d/%d", idx, len(_GROQ_KEYS))
                try:
                    payload = {
                        "model": model or GROQ_MODEL,
                        "messages": request_messages,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                    }
                    if tools:
                        payload["tools"] = tools
                    resp = await client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                        timeout=30.0,
                    )
                    if resp.status_code == 429:
                        logger.warning("[llm] Groq key %d/%d rate-limited (429) — trying next key", idx, len(_GROQ_KEYS))
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    if "usage" in data:
                        await _track_usage(data["usage"], model or GROQ_MODEL)
                    message = data["choices"][0]["message"]
                    if tools:
                        return message
                    return message["content"].strip()
                except httpx.HTTPStatusError as e:
                    logger.warning("[llm_client] Groq key %d HTTP %d — skipping", idx, e.response.status_code)
                    break
                except Exception as e:
                    logger.warning("[llm_client] Groq key %d failed (%s) — skipping", idx, e)
                    break
            else:
                logger.warning("[llm_client] All %d Groq key(s) rate-limited", len(_GROQ_KEYS))
        else:
            logger.warning("[llm_client] No Groq keys configured")

        if tools:
            # No OpenRouter fallback for tool-calling requests — the free-tier
            # fallback model doesn't reliably support it, and a plain-string
            # response here would break every tools=... caller's message-dict
            # handling.
            return {}

        # Every Groq key either failed outright or was rate-limited — try
        # OpenRouter before giving up entirely. This is the fallback that
        # didn't exist when Groq retired its own models mid-2026 and broke
        # every agent call with no alternative provider to fall back to.
        if OPENROUTER_API_KEY:
            logger.info("[llm] Groq exhausted — trying OpenRouter fallback (%s)", OPENROUTER_MODEL)
            try:
                resp = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": OPENROUTER_MODEL,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user",   "content": user},
                        ],
                        "max_tokens": max(max_tokens, _OPENROUTER_MIN_TOKENS),
                        "temperature": temperature,
                    },
                    timeout=30.0,
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"].strip()
                if content:
                    logger.info("[llm] OpenRouter fallback succeeded")
                    if "usage" in data:
                        # Free tier — $0 either way, but still worth counting
                        # tokens so usage history shows fallback activity.
                        await _track_usage(data["usage"], OPENROUTER_MODEL, provider="openrouter", cost_per_m=(0.0, 0.0))
                    return content
            except Exception as e:
                logger.warning("[llm_client] OpenRouter fallback also failed: %s", e)

        return ""
    finally:
        if owns_client:
            await client.aclose()
