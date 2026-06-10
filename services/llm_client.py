# services/llm_client.py
"""
Shared LLM client for WealthOS agents.
Calls Groq API by default, falls back to local Ollama.
"""

import os
import httpx
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
OLLAMA_URL   = os.getenv("OLLAMA_URL", "http://localhost:11434")
GROQ_MODEL   = "llama-3.3-70b-versatile"
OLLAMA_MODEL = "qwen2.5:7b"

async def call_llm(
    system: str, 
    user: str, 
    max_tokens: int = 500, 
    temperature: float = 0.1, 
    client: httpx.AsyncClient = None,
    model: str = None,
    ollama_model: str = None,
) -> str:
    """
    Call Groq API. Falls back to local Ollama if Groq fails.
    Reuses httpx.AsyncClient if provided, otherwise creates a temporary one.
    """
    owns_client = False
    if client is None:
        client = httpx.AsyncClient()
        owns_client = True
        
    try:
        # Try Groq first
        if GROQ_API_KEY:
            try:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {GROQ_API_KEY}",
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
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"].strip()
            except Exception as e:
                print(f"  [llm_client] Groq failed, falling back to Ollama: {e}")

        # Ollama fallback
        resp = await client.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": ollama_model or OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                "stream": False,
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()
    finally:
        if owns_client:
            await client.aclose()
