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

import os
import json
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

DATABASE_URL = os.getenv("WEALTHOS_DB_URL", "postgresql://wealthos_user:wealthos_pass@localhost:5432/wealthos")

# asyncpg uses postgresql:// not postgresql+asyncpg://
# Strip the +asyncpg part if present
DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


# ── DB Helper ──────────────────────────────────────────────────────────────────

async def get_conn():
    """Get a single asyncpg connection."""
    return await asyncpg.connect(DATABASE_URL)


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
    conn = await get_conn()
    try:
        rows = await conn.fetch(
            """
            SELECT id, date, description, amount, type, category, source
            FROM transactions
            WHERE user_id = $1
              AND date >= CURRENT_DATE - INTERVAL '1 month' * $2
            ORDER BY date DESC
            """,
            user_id, months
        )

        transactions = [
            {
                "id": str(r["id"]),
                "date": r["date"].isoformat(),
                "description": r["description"],
                "amount": r["amount"],
                "type": r["type"],           # 'credit' or 'debit'
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
    finally:
        await conn.close()


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
    conn = await get_conn()
    try:
        # Get all debits (expenses) in the period
        rows = await conn.fetch(
            """
            SELECT category, amount, date
            FROM transactions
            WHERE user_id = $1
              AND type = 'debit'
              AND date >= CURRENT_DATE - INTERVAL '1 month' * $2
            ORDER BY date DESC
            """,
            user_id, months
        )

        if not rows:
            return {
                "user_id": user_id,
                "message": "No expense data found for this user",
                "categories": {},
                "anomalies": [],
            }

        # Group by category
        category_totals = defaultdict(float)
        category_counts = defaultdict(int)
        monthly_by_category = defaultdict(lambda: defaultdict(float))

        for r in rows:
            cat = r["category"] or "Uncategorized"
            month_key = r["date"].strftime("%Y-%m")
            category_totals[cat] += r["amount"]
            category_counts[cat] += 1
            monthly_by_category[cat][month_key] += r["amount"]

        total_spend = sum(category_totals.values())

        # Build category breakdown with % share
        categories = {
            cat: {
                "total": round(total, 2),
                "count": category_counts[cat],
                "percent_of_spend": round((total / total_spend) * 100, 1) if total_spend else 0,
                "monthly": dict(monthly_by_category[cat]),
            }
            for cat, total in sorted(category_totals.items(), key=lambda x: -x[1])
        }

        # Anomaly detection — flag categories where last month > 2x average
        anomalies = []
        current_month = date.today().strftime("%Y-%m")

        for cat, data in categories.items():
            monthly = data["monthly"]
            if len(monthly) < 2:
                continue
            avg = sum(monthly.values()) / len(monthly)
            current = monthly.get(current_month, 0)
            if avg > 0 and current > avg * 2:
                anomalies.append({
                    "category": cat,
                    "current_month": round(current, 2),
                    "average": round(avg, 2),
                    "spike_ratio": round(current / avg, 1),
                    "flag": f"{cat} spending this month is {round(current/avg, 1)}x your average",
                })

        # Top 3 spending categories
        top_categories = list(categories.keys())[:3]

        return {
            "user_id": user_id,
            "months_analyzed": months,
            "total_spend": round(total_spend, 2),
            "top_categories": top_categories,
            "categories": categories,
            "anomalies": anomalies,
        }

    except Exception as e:
        logger.error(f"analyze_spending failed: {e}")
        return {"error": str(e), "categories": {}, "anomalies": []}
    finally:
        await conn.close()


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
    conn = await get_conn()
    try:
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
            user_id, months
        )

        # Organize by month
        monthly = defaultdict(lambda: {"income": 0.0, "expenses": 0.0})
        for r in rows:
            if r["type"] == "credit":
                monthly[r["month"]]["income"] += r["total"]
            elif r["type"] == "debit":
                monthly[r["month"]]["expenses"] += r["total"]

        # Calculate surplus per month
        monthly_summary = {}
        for month, data in sorted(monthly.items(), reverse=True):
            income = data["income"]
            expenses = data["expenses"]
            surplus = income - expenses
            savings_rate = round((surplus / income) * 100, 1) if income > 0 else 0
            monthly_summary[month] = {
                "income": round(income, 2),
                "expenses": round(expenses, 2),
                "surplus": round(surplus, 2),
                "savings_rate_pct": savings_rate,
            }

        # Averages across all months
        if monthly_summary:
            avg_income   = sum(m["income"]   for m in monthly_summary.values()) / len(monthly_summary)
            avg_expenses = sum(m["expenses"] for m in monthly_summary.values()) / len(monthly_summary)
            avg_surplus  = avg_income - avg_expenses
            avg_savings_rate = round((avg_surplus / avg_income) * 100, 1) if avg_income > 0 else 0
        else:
            avg_income = avg_expenses = avg_surplus = avg_savings_rate = 0

        return {
            "user_id": user_id,
            "months_analyzed": months,
            "monthly": monthly_summary,
            "averages": {
                "monthly_income": round(avg_income, 2),
                "monthly_expenses": round(avg_expenses, 2),
                "monthly_surplus": round(avg_surplus, 2),
                "savings_rate_pct": avg_savings_rate,
            },
        }

    except Exception as e:
        logger.error(f"get_surplus failed: {e}")
        return {"error": str(e)}
    finally:
        await conn.close()


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
    conn = await get_conn()
    try:
        rows = await conn.fetch(
            """
            SELECT id, name, amount, frequency, last_charged, is_flagged
            FROM subscriptions
            WHERE user_id = $1
            ORDER BY amount DESC
            """,
            user_id
        )

        subscriptions = []
        total_monthly = 0.0

        for r in rows:
            # Normalize to monthly cost
            freq = r["frequency"]
            amount = r["amount"]
            if freq == "yearly":
                monthly_cost = amount / 12
            elif freq == "weekly":
                monthly_cost = amount * 4
            else:
                monthly_cost = amount  # already monthly

            total_monthly += monthly_cost

            subscriptions.append({
                "id": str(r["id"]),
                "name": r["name"],
                "amount": amount,
                "frequency": freq,
                "monthly_cost": round(monthly_cost, 2),
                "last_charged": r["last_charged"].isoformat() if r["last_charged"] else None,
                "is_flagged": r["is_flagged"],
            })

        return {
            "user_id": user_id,
            "count": len(subscriptions),
            "total_monthly_cost": round(total_monthly, 2),
            "subscriptions": subscriptions,
        }

    except Exception as e:
        logger.error(f"get_subscriptions failed: {e}")
        return {"error": str(e), "subscriptions": []}
    finally:
        await conn.close()


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
    conn = await get_conn()
    try:
        rows = await conn.fetch(
            """
            SELECT id, name, target_amount, current_amount, deadline_date, created_at
            FROM financial_goals
            WHERE user_id = $1
            ORDER BY deadline_date ASC
            """,
            user_id
        )

        goals = []
        today = date.today()

        for r in rows:
            target = r["target_amount"]
            current = r["current_amount"]
            progress_pct = round((current / target) * 100, 1) if target > 0 else 0
            remaining = target - current

            days_left = None
            on_track = None
            if r["deadline_date"]:
                days_left = (r["deadline_date"] - today).days
                # Check if on track — need to save 'remaining' in 'days_left' days
                if days_left > 0:
                    daily_needed = remaining / days_left
                    on_track = daily_needed < (remaining / max(days_left, 1))  # simple check

            goals.append({
                "id": str(r["id"]),
                "name": r["name"],
                "target_amount": target,
                "current_amount": round(current, 2),
                "remaining": round(remaining, 2),
                "progress_pct": progress_pct,
                "deadline_date": r["deadline_date"].isoformat() if r["deadline_date"] else None,
                "days_left": days_left,
                "status": "completed" if progress_pct >= 100 else "in_progress",
            })

        return {
            "user_id": user_id,
            "count": len(goals),
            "goals": goals,
        }

    except Exception as e:
        logger.error(f"get_goals failed: {e}")
        return {"error": str(e), "goals": []}
    finally:
        await conn.close()


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()