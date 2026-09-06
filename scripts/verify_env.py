#!/usr/bin/env python
# scripts/verify_env.py
"""
Environment/schema smoke test — checks that the running stack actually
matches what the code expects, instead of trusting docs/assumptions.

Built after a single manual stress test (2026-09-03) found FOUR real bugs
that nothing had caught: a Postgres volume whose password had drifted from
docker-compose.yml, a WEALTHOS_JWT_SECRET that was never set (causing raw
500s on signup/login instead of the documented fail-open behavior), the
users/analysis_history tables never created, and the full 9-table schema
from init_db.sql never applied to that volume at all. Run this before
assuming a fresh checkout or a freshly deployed box actually works.

Usage:
    python scripts/verify_env.py

Exits 0 if every REQUIRED check passes, 1 otherwise. WARN checks (missing
optional API keys) never fail the run — they just get printed.
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

EXPECTED_TABLES = {
    "transactions", "subscriptions", "financial_goals", "emis",
    "financial_facts", "portfolio_holdings", "tracked_symbols",
    "indexed_tickers", "user_risk_profiles", "llm_usage",  # from scripts/init_db.sql
    "users", "analysis_history",              # created lazily by api/main.py
}

results: list[tuple[str, bool, str]] = []  # (check_name, required, message)


def report(name: str, ok: bool, message: str, required: bool = True):
    results.append((name, required, "OK" if ok else message))
    tag = "OK  " if ok else ("FAIL" if required else "WARN")
    print(f"  [{tag}] {name}: {message if not ok else 'ok'}")


async def check_postgres():
    db_url = os.getenv("WEALTHOS_DB_URL", "").replace("postgresql+asyncpg://", "postgresql://")
    if not db_url:
        report("Postgres connection", False, "WEALTHOS_DB_URL not set")
        return

    try:
        import asyncpg
        conn = await asyncpg.connect(db_url, timeout=10)
    except Exception as e:
        report("Postgres connection", False, f"{type(e).__name__}: {e}")
        return

    report("Postgres connection", True, "ok")

    try:
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        found = {r["table_name"] for r in rows}
        missing = EXPECTED_TABLES - found
        if missing:
            report(
                "Postgres schema", False,
                f"missing tables: {sorted(missing)} — run scripts/init_db.sql",
            )
        else:
            report("Postgres schema", True, f"all {len(EXPECTED_TABLES)} expected tables present")
    finally:
        await conn.close()


async def check_redis():
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(redis_url, socket_connect_timeout=5)
        await r.ping()
        await r.aclose()
        report("Redis connection", True, "ok")
    except Exception as e:
        report("Redis connection", False, f"{type(e).__name__}: {e}")


async def check_qdrant():
    try:
        from qdrant_client import QdrantClient
        url = os.getenv("QDRANT_URL", "http://localhost:6333")
        api_key = os.getenv("QDRANT_API_KEY", "")
        client = QdrantClient(url=url, api_key=api_key or None, timeout=10)
        client.get_collections()
        report("Qdrant connection", True, "ok")
    except Exception as e:
        report("Qdrant connection", False, f"{type(e).__name__}: {e}")


def check_secrets():
    jwt_secret = os.getenv("WEALTHOS_JWT_SECRET", "")
    report(
        "WEALTHOS_JWT_SECRET", bool(jwt_secret),
        "unset — signup/login will 500 instead of failing open (see api/main.py's _hash_password path)",
        required=False,
    )

    groq_keys = [k for k in [os.getenv("GROQ_API_KEY"), os.getenv("GROQ_API_KEY_2"), os.getenv("GROQ_API_KEY_3")] if k]
    report("At least one GROQ_API_KEY", bool(groq_keys), "no Groq keys configured — every agent LLM call will fail")

    optional = {
        "COHERE_API_KEY": "RAG reranking will fall back to raw hybrid-search order",
        "FIRECRAWL_API_KEY": "news/Reddit scraping and earnings-call indexing will skip",
        "E2B_API_KEY": "Code Agent's DCF/Monte Carlo sandbox will be unavailable",
        "MEM0_API_KEY": "cross-session memory read/write will skip",
    }
    for key, consequence in optional.items():
        report(key, bool(os.getenv(key)), f"unset — {consequence}", required=False)


async def main():
    print("\nWealthOS environment verification\n")
    check_secrets()
    await check_postgres()
    await check_redis()
    await check_qdrant()

    required_failures = [r for r in results if r[1] and r[2] != "OK" and r[2] != "ok"]
    print(f"\n  {len(results) - len(required_failures)}/{len(results)} checks OK")
    if required_failures:
        print(f"  {len(required_failures)} REQUIRED check(s) failed — fix these before deploying.\n")
        sys.exit(1)
    print("  All required checks passed.\n")


if __name__ == "__main__":
    asyncio.run(main())
