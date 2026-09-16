# finance_server.py
# Personal finance data layer — transaction history (Postgres).
#
# Tools:
#   get_transactions   — fetch raw transaction history for a user
#
# ponytail: this file previously carried 18 more tools (spending analysis,
# goals, EMIs, portfolio holdings/P&L/allocation, and a standalone calculator
# suite) merged in from portfolio_server.py + calculator_server.py. A repo-wide
# grep found zero callers for any of them, so they were removed. Re-add from
# git history (see `mcp_servers/finance_server.py` prior to this cleanup) if
# an agent starts calling them.

import os
import uuid
import logging

import asyncpg
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# ── Load env first ─────────────────────────────────────────────────────────────
load_dotenv()

# ── Setup ──────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("finance-mcp")

DATABASE_URL = os.getenv(
    "WEALTHOS_DB_URL",
    "postgresql://wealthos_user:wealthos_pass@localhost:5432/wealthos"
)
# asyncpg uses postgresql:// not postgresql+asyncpg://
DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


# ── Connection Pool ────────────────────────────────────────────────────────────
# One pool shared across all tool calls — much cheaper than open/close per call

_pool = None

async def get_pool() -> asyncpg.Pool:
    """Return the shared connection pool, creating it on first call."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=2,
            max_size=10,
        )
    return _pool


# ── UUID Validator ─────────────────────────────────────────────────────────────

def parse_uuid(user_id: str) -> uuid.UUID | None:
    """Return UUID object or None if invalid."""
    try:
        return uuid.UUID(user_id)
    except ValueError:
        return None


# ── Tool: get_transactions ──────────────────────────────────────────────────────

@mcp.tool()
async def get_transactions(user_id: str, months: int = 3) -> dict:
    """
    Fetch raw transaction history for a user.

    Args:
        user_id: UUID of the user
        months:  How many months back to fetch (default: 3)

    Returns:
        List of transactions with date, amount, type, category
    """
    uid = parse_uuid(user_id)
    if not uid:
        return {"error": "Invalid user_id format", "transactions": []}

    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, date, description, amount, type, category, source
                FROM transactions
                WHERE user_id = $1
                  AND date >= CURRENT_DATE - INTERVAL '1 month' * $2
                ORDER BY date DESC
                """,
                uid, months
            )

        transactions = [
            {
                "id": str(r["id"]),
                "date": r["date"].isoformat(),
                "description": r["description"],
                "amount": r["amount"],
                "type": r["type"],
                "category": r["category"],
                "source": r["source"],
            }
            for r in rows
        ]

        return {
            "user_id": user_id,
            "months": months,
            "count": len(transactions),
            "transactions": transactions,
        }

    except Exception as e:
        logger.error(f"get_transactions failed: {e}")
        return {"error": str(e), "transactions": []}


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
