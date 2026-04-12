# finance_server.py
# Personal finance data layer — reads from Postgres and exposes tools to the Finance Agent.
# No external API calls. Pure DB reads/writes.
#
# Tools:
#   get_transactions   — fetch raw transaction history for a user
#   analyze_spending   — category breakdown + anomaly detection
#   get_surplus        — monthly income vs expenses
#   get_subscriptions  — list recurring subscriptions
#   get_goals          — fetch financial goals with progress %
#   get_emis           — fetch active EMIs/loans for debt burden calculation

import os
import uuid
import logging
from datetime import datetime, date
from collections import defaultdict

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


# ── Tool 1: get_transactions ───────────────────────────────────────────────────

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


# ── Tool 2: analyze_spending ───────────────────────────────────────────────────

@mcp.tool()
async def analyze_spending(user_id: str, months: int = 3) -> dict:
    """
    Analyze spending by category and flag anomalies.

    Args:
        user_id: UUID of the user
        months:  How many months to analyze (default: 3)

    Returns:
        Category totals, top categories, anomaly flags
    """
    uid = parse_uuid(user_id)
    if not uid:
        return {"error": "Invalid user_id format", "categories": {}, "anomalies": []}

    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT category, amount, date
                FROM transactions
                WHERE user_id = $1
                  AND type = 'debit'
                  AND date >= CURRENT_DATE - INTERVAL '1 month' * $2
                ORDER BY date DESC
                """,
                uid, months
            )

        if not rows:
            return {
                "user_id": user_id,
                "message": "No expense data found for this user",
                "categories": {},
                "anomalies": [],
            }

        # Group by category
        category_totals    = defaultdict(float)
        category_counts    = defaultdict(int)
        monthly_by_cat     = defaultdict(lambda: defaultdict(float))

        for r in rows:
            cat       = r["category"] or "Uncategorized"
            month_key = r["date"].strftime("%Y-%m")
            category_totals[cat]               += r["amount"]
            category_counts[cat]               += 1
            monthly_by_cat[cat][month_key]     += r["amount"]

        total_spend = sum(category_totals.values())

        # Build category breakdown with % share
        categories = {
            cat: {
                "total": round(total, 2),
                "count": category_counts[cat],
                "percent_of_spend": round((total / total_spend) * 100, 1) if total_spend else 0,
                "monthly": dict(monthly_by_cat[cat]),
            }
            for cat, total in sorted(category_totals.items(), key=lambda x: -x[1])
        }

        # Anomaly detection — flag categories where last month > 2x average
        anomalies      = []
        current_month  = date.today().strftime("%Y-%m")

        for cat, data in categories.items():
            monthly = data["monthly"]
            if len(monthly) < 2:
                continue
            avg     = sum(monthly.values()) / len(monthly)
            current = monthly.get(current_month, 0)
            if avg > 0 and current > avg * 2:
                anomalies.append({
                    "category":      cat,
                    "current_month": round(current, 2),
                    "average":       round(avg, 2),
                    "spike_ratio":   round(current / avg, 1),
                    "flag":          f"{cat} spending this month is {round(current / avg, 1)}x your average",
                })

        top_categories = list(categories.keys())[:3]

        return {
            "user_id":         user_id,
            "months_analyzed": months,
            "total_spend":     round(total_spend, 2),
            "top_categories":  top_categories,
            "categories":      categories,
            "anomalies":       anomalies,
        }

    except Exception as e:
        logger.error(f"analyze_spending failed: {e}")
        return {"error": str(e), "categories": {}, "anomalies": []}


# ── Tool 3: get_surplus ────────────────────────────────────────────────────────

@mcp.tool()
async def get_surplus(user_id: str, months: int = 3) -> dict:
    """
    Calculate monthly income vs expenses and net surplus/deficit.

    Args:
        user_id: UUID of the user
        months:  How many months to analyze (default: 3)

    Returns:
        Monthly income, expenses, surplus, and savings rate
    """
    uid = parse_uuid(user_id)
    if not uid:
        return {"error": "Invalid user_id format"}

    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    TO_CHAR(date, 'YYYY-MM') AS month,
                    type,
                    SUM(amount) AS total
                FROM transactions
                WHERE user_id = $1
                  AND date >= CURRENT_DATE - INTERVAL '1 month' * $2
                GROUP BY month, type
                ORDER BY month DESC
                """,
                uid, months
            )

        # Organize by month
        monthly = defaultdict(lambda: {"income": 0.0, "expenses": 0.0})
        for r in rows:
            if r["type"] == "credit":
                monthly[r["month"]]["income"]   += r["total"]
            elif r["type"] == "debit":
                monthly[r["month"]]["expenses"] += r["total"]

        # Calculate surplus per month
        monthly_summary = {}
        for month, data in sorted(monthly.items(), reverse=True):
            income   = data["income"]
            expenses = data["expenses"]
            surplus  = income - expenses
            savings_rate = round((surplus / income) * 100, 1) if income > 0 else 0
            monthly_summary[month] = {
                "income":           round(income, 2),
                "expenses":         round(expenses, 2),
                "surplus":          round(surplus, 2),
                "savings_rate_pct": savings_rate,
            }

        # Averages across all months
        if monthly_summary:
            avg_income       = sum(m["income"]   for m in monthly_summary.values()) / len(monthly_summary)
            avg_expenses     = sum(m["expenses"] for m in monthly_summary.values()) / len(monthly_summary)
            avg_surplus      = avg_income - avg_expenses
            avg_savings_rate = round((avg_surplus / avg_income) * 100, 1) if avg_income > 0 else 0
        else:
            avg_income = avg_expenses = avg_surplus = avg_savings_rate = 0

        return {
            "user_id":         user_id,
            "months_analyzed": months,
            "monthly":         monthly_summary,
            "averages": {
                "monthly_income":   round(avg_income, 2),
                "monthly_expenses": round(avg_expenses, 2),
                "monthly_surplus":  round(avg_surplus, 2),
                "savings_rate_pct": avg_savings_rate,
            },
        }

    except Exception as e:
        logger.error(f"get_surplus failed: {e}")
        return {"error": str(e)}


# ── Tool 4: get_subscriptions ──────────────────────────────────────────────────

@mcp.tool()
async def get_subscriptions(user_id: str) -> dict:
    """
    List all recurring subscriptions and flag suspicious ones.

    Args:
        user_id: UUID of the user

    Returns:
        List of subscriptions with monthly cost and flags
    """
    uid = parse_uuid(user_id)
    if not uid:
        return {"error": "Invalid user_id format", "subscriptions": []}

    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, name, amount, frequency, last_charged, is_flagged
                FROM subscriptions
                WHERE user_id = $1
                ORDER BY amount DESC
                """,
                uid
            )

        subscriptions  = []
        total_monthly  = 0.0

        for r in rows:
            freq   = r["frequency"]
            amount = r["amount"]

            # Normalize to monthly cost
            if freq == "yearly":
                monthly_cost = amount / 12
            elif freq == "weekly":
                monthly_cost = amount * 4
            else:
                monthly_cost = amount  # already monthly

            total_monthly += monthly_cost

            subscriptions.append({
                "id":           str(r["id"]),
                "name":         r["name"],
                "amount":       amount,
                "frequency":    freq,
                "monthly_cost": round(monthly_cost, 2),
                "last_charged": r["last_charged"].isoformat() if r["last_charged"] else None,
                "is_flagged":   r["is_flagged"],
            })

        return {
            "user_id":            user_id,
            "count":              len(subscriptions),
            "total_monthly_cost": round(total_monthly, 2),
            "subscriptions":      subscriptions,
        }

    except Exception as e:
        logger.error(f"get_subscriptions failed: {e}")
        return {"error": str(e), "subscriptions": []}


# ── Tool 5: get_goals ─────────────────────────────────────────────────────────

@mcp.tool()
async def get_goals(user_id: str) -> dict:
    """
    Fetch financial goals and calculate progress for each.

    Args:
        user_id: UUID of the user

    Returns:
        List of goals with progress % and days remaining
    """
    uid = parse_uuid(user_id)
    if not uid:
        return {"error": "Invalid user_id format", "goals": []}

    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, name, target_amount, current_amount, deadline_date, created_at
                FROM financial_goals
                WHERE user_id = $1
                ORDER BY deadline_date ASC
                """,
                uid
            )

        goals = []
        today = date.today()

        for r in rows:
            target       = r["target_amount"]
            current      = r["current_amount"]
            progress_pct = round((current / target) * 100, 1) if target > 0 else 0
            remaining    = target - current

            days_left = None
            on_track  = None

            if r["deadline_date"] and r["created_at"]:
                deadline      = r["deadline_date"]
                created_date  = r["created_at"].date() if hasattr(r["created_at"], "date") else r["created_at"]

                total_days    = (deadline - created_date).days
                days_elapsed  = (today - created_date).days
                days_left     = (deadline - today).days

                if total_days > 0:
                    # Expected progress % by today based on time elapsed
                    expected_progress = (days_elapsed / total_days) * 100
                    on_track = progress_pct >= expected_progress

            goals.append({
                "id":             str(r["id"]),
                "name":           r["name"],
                "target_amount":  target,
                "current_amount": round(current, 2),
                "remaining":      round(remaining, 2),
                "progress_pct":   progress_pct,
                "deadline_date":  r["deadline_date"].isoformat() if r["deadline_date"] else None,
                "days_left":      days_left,
                "on_track":       on_track,
                "status":         "completed" if progress_pct >= 100 else "in_progress",
            })

        return {
            "user_id": user_id,
            "count":   len(goals),
            "goals":   goals,
        }

    except Exception as e:
        logger.error(f"get_goals failed: {e}")
        return {"error": str(e), "goals": []}


# ── Tool 6: get_emis ──────────────────────────────────────────────────────────

@mcp.tool()
async def get_emis(user_id: str) -> dict:
    """
    Fetch active EMIs and loans for debt burden calculation.
    Used by Finance Agent to compute debt_burden_ratio and
    recommend repayment order (avalanche vs snowball).

    Args:
        user_id: UUID of the user

    Returns:
        List of active EMIs with monthly amount, outstanding balance,
        interest rate, and debt burden ratio
    """
    uid = parse_uuid(user_id)
    if not uid:
        return {"error": "Invalid user_id format", "emis": []}

    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            # Check if emis table exists yet
            table_exists = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'emis'
                )
                """
            )

            if not table_exists:
                return {
                    "user_id": user_id,
                    "message": "EMI table not yet created — will be added in Phase 3",
                    "emis": [],
                    "total_monthly_emi": 0,
                    "debt_burden_ratio": 0,
                }

            rows = await conn.fetch(
                """
                SELECT id, loan_name, lender, principal_amount,
                       outstanding_balance, monthly_emi, interest_rate,
                       tenure_months, emi_date, loan_type, is_active
                FROM emis
                WHERE user_id = $1 AND is_active = TRUE
                ORDER BY interest_rate DESC
                """,
                uid
            )

        # Get monthly income for debt burden ratio
        surplus_data = await get_surplus(user_id, months=3)
        monthly_income = surplus_data.get("averages", {}).get("monthly_income", 0)

        emis              = []
        total_monthly_emi = 0.0

        for r in rows:
            total_monthly_emi += r["monthly_emi"]
            emis.append({
                "id":                   str(r["id"]),
                "loan_name":            r["loan_name"],
                "lender":               r["lender"],
                "principal_amount":     r["principal_amount"],
                "outstanding_balance":  r["outstanding_balance"],
                "monthly_emi":          r["monthly_emi"],
                "interest_rate":        r["interest_rate"],
                "tenure_months":        r["tenure_months"],
                "emi_date":             r["emi_date"],
                "loan_type":            r["loan_type"],  # home/car/personal/education
            })

        # Debt burden ratio = total EMI / monthly income
        debt_burden_ratio = round(total_monthly_emi / monthly_income, 2) if monthly_income > 0 else 0

        # Repayment order suggestions
        # Avalanche = highest interest first (saves most money)
        avalanche_order = sorted(emis, key=lambda x: -x["interest_rate"])
        # Snowball  = lowest balance first (psychological wins)
        snowball_order  = sorted(emis, key=lambda x:  x["outstanding_balance"])

        return {
            "user_id":              user_id,
            "count":                len(emis),
            "emis":                 emis,
            "total_monthly_emi":    round(total_monthly_emi, 2),
            "debt_burden_ratio":    debt_burden_ratio,
            "debt_burden_flag":     debt_burden_ratio > 0.5,  # >50% income = high risk
            "avalanche_order":      [e["loan_name"] for e in avalanche_order],
            "snowball_order":       [e["loan_name"] for e in snowball_order],
        }

    except Exception as e:
        logger.error(f"get_emis failed: {e}")
        return {"error": str(e), "emis": []}


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()