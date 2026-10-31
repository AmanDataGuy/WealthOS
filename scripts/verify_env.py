#!/usr/bin/env python
"""
# verify_env.py - check that this machine is ready to run WealthOS

Checks the settings in .env and the Postgres tables.

    python scripts/verify_env.py

Exits 1 if a required check fails. Optional ones (sign-in database, encryption key ...) only warn.
"""
import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()
failures = 0


# Print one check; a failed required check makes the script exit 1.
def report(name: str, ok: bool, problem: str, required: bool = True) -> None:
    global failures
    failures += (not ok) and required
    print(f"  [{'OK  ' if ok else 'FAIL' if required else 'WARN'}] {name}{'' if ok else ': ' + problem}")


# Postgres holds only users (sign-in) and llm_usage (cost log); the API creates both at startup.
async def check_postgres() -> None:
    url = os.getenv("WEALTHOS_DB_URL", "").replace("postgresql+asyncpg://", "postgresql://")
    if not url:
        return report("Postgres", False, "WEALTHOS_DB_URL not set: sign-in and the LLM cost log are off", required=False)
    try:
        import asyncpg
        conn = await asyncpg.connect(url, timeout=10)
    except Exception as e:
        return report("Postgres", False, f"{type(e).__name__}: {e}", required=False)
    try:
        found = {r["table_name"] for r in await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")}
        missing = {"users", "llm_usage"} - found
        report("Postgres tables", not missing, f"missing {sorted(missing)}: start the API once, it creates them",
               required=False)
    finally:
        await conn.close()


# Run every check and exit 1 if a required one failed.
def main() -> None:
    print("\nWealthOS environment check\n")
    report("LLM key", bool(os.getenv("GROQ_API_KEY") or os.getenv("OPENROUTER_API_KEY")),
           "set GROQ_API_KEY (or OPENROUTER_API_KEY): the assistant, AI edit and strategy parsing need one")
    report("WEALTHOS_JWT_SECRET", bool(os.getenv("WEALTHOS_JWT_SECRET")),
           "unset: fine for local use, required in production (WEALTHOS_ENV=production)", required=False)
    report("WEALTHOS_FERNET_KEY", bool(os.getenv("WEALTHOS_FERNET_KEY")),
           "unset: database connections are switched off (see .env.example)", required=False)
    report("SEC_USER_AGENT", bool(os.getenv("SEC_USER_AGENT")), "unset: SEC asks for a contact email", required=False)
    asyncio.run(check_postgres())
    print("\n  " + ("All required checks passed." if not failures else f"{failures} required check(s) failed."), "\n")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
