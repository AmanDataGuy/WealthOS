# services/llm_client.py
"""
Shared LLM client for WealthOS agents.
Calls Groq API (llama-3.3-70b-versatile). Returns empty string if all keys fail.
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

GROQ_MODEL = "llama-3.3-70b-versatile"

# Groq pricing for llama-3.3-70b-versatile (per 1M tokens)
_COST_INPUT_PER_M  = 0.05
_COST_OUTPUT_PER_M = 0.08

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


def _track_usage(usage: dict, model: str):
    """Update session totals from a Groq usage dict. Called after every successful Groq call."""
    prompt     = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    total      = usage.get("total_tokens", 0)
    cost       = (prompt / 1_000_000 * _COST_INPUT_PER_M) + \
                 (completion / 1_000_000 * _COST_OUTPUT_PER_M)

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


async def call_llm(
    system: str,
    user: str,
    max_tokens: int = 500,
    temperature: float = 0.1,
    client: httpx.AsyncClient = None,
    model: str = None,
) -> str:
    """
    Call Groq API. Returns empty string if no keys are configured or all keys fail.
    Reuses httpx.AsyncClient if provided, otherwise creates a temporary one.
    Logs token usage and cost after every successful call.
    """
    owns_client = False
    if client is None:
        client = httpx.AsyncClient()
        owns_client = True

    try:
        if not _GROQ_KEYS:
            logger.warning("[llm_client] No Groq keys configured")
            return ""

        for idx, key in enumerate(_GROQ_KEYS, 1):
            logger.info("[llm] calling Groq with key %d/%d", idx, len(_GROQ_KEYS))
            try:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model or GROQ_MODEL,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user",   "content": user},
                        ],
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                    },
                    timeout=30.0,
                )
                if resp.status_code == 429:
                    logger.warning("[llm] Groq key %d/%d rate-limited (429) — trying next key", idx, len(_GROQ_KEYS))
                    continue
                resp.raise_for_status()
                data = resp.json()
                if "usage" in data:
                    _track_usage(data["usage"], model or GROQ_MODEL)
                return data["choices"][0]["message"]["content"].strip()
            except httpx.HTTPStatusError as e:
                logger.warning("[llm_client] Groq key %d HTTP %d — skipping", idx, e.response.status_code)
                break
            except Exception as e:
                logger.warning("[llm_client] Groq key %d failed (%s) — skipping", idx, e)
                break
        else:
            logger.warning("[llm_client] All %d Groq key(s) rate-limited — no fallback available", len(_GROQ_KEYS))

        return ""
    finally:
        if owns_client:
            await client.aclose()
