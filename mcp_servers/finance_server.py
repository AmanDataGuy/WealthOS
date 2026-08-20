# finance_server.py
# Personal finance data layer — transactions/goals/EMIs, portfolio holdings, and
# pure financial math, all in one server. Merged from three separate servers
# (finance_server + portfolio_server + calculator_server) that all served the
# same "your money" domain and — as of this merge — weren't even spawned via
# MCP by any live caller for the latter two, so consolidating cost nothing in
# practice while cutting two subprocess lifecycles down to zero.
#
# Tools — personal finance (Postgres):
#   get_transactions   — fetch raw transaction history for a user
#   analyze_spending   — category breakdown + anomaly detection
#   get_surplus        — monthly income vs expenses
#   get_subscriptions  — list recurring subscriptions
#   get_goals          — fetch financial goals with progress %
#   get_emis           — fetch active EMIs/loans for debt burden calculation
#
# Tools — portfolio (Postgres + yfinance):
#   get_holdings          — fetch user's portfolio from DB
#   get_portfolio_value   — holdings × live prices = current value
#   get_pnl               — profit/loss per stock and overall
#   get_allocation        — sector/asset class breakdown as percentages
#   add_holding           — insert or update a holding in DB
#   remove_holding        — delete a holding from DB
#
# Tools — calculator (pure math, no I/O):
#   compound_interest, loan_emi, inflation_adjusted, fire_number,
#   xirr, sip_returns, goal_monthly_saving

import os
import uuid
import logging
from datetime import datetime, date, timezone
from collections import defaultdict

import asyncpg
import yfinance as yf
from scipy.optimize import brentq
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


# ═════════════════════════════════════════════════════════════════════════════
# Portfolio tools (merged from portfolio_server.py)
# ═════════════════════════════════════════════════════════════════════════════

def _fetch_price(ticker: str) -> float:
    """Fetch latest close price from yfinance."""
    try:
        data = yf.Ticker(ticker).fast_info
        price = data.last_price or data.previous_close
        return float(price) if price else 0.0
    except Exception as e:
        logger.error(f"Price fetch failed for {ticker}: {e}")
        return 0.0


def _fetch_prices_bulk(tickers: list[str]) -> dict[str, float]:
    """Fetch prices for multiple tickers at once."""
    prices = {}
    for ticker in tickers:
        prices[ticker] = _fetch_price(ticker)
    return prices


@mcp.tool()
async def get_holdings(user_id: str) -> dict:
    """
    Fetch user's portfolio holdings from the database.

    Args:
        user_id:  UUID of the user

    Returns:
        List of holdings with ticker, quantity, avg_buy_price, sector, asset_type
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ticker, quantity, avg_buy_price, sector, asset_type, added_at
            FROM portfolio_holdings
            WHERE user_id = $1
            ORDER BY ticker
            """,
            user_id,
        )
        holdings = [
            {
                "ticker": r["ticker"],
                "quantity": float(r["quantity"]),
                "avg_buy_price": float(r["avg_buy_price"]),
                "invested_value": round(float(r["quantity"]) * float(r["avg_buy_price"]), 2),
                "sector": r["sector"] or "Unknown",
                "asset_type": r["asset_type"] or "equity",
                "added_at": str(r["added_at"]),
            }
            for r in rows
        ]
        return {
            "user_id": user_id,
            "holdings_count": len(holdings),
            "holdings": holdings,
        }


@mcp.tool()
async def get_portfolio_value(user_id: str) -> dict:
    """
    Calculate current portfolio value using live prices from yfinance.

    Args:
        user_id:  UUID of the user

    Returns:
        Per-holding current value and total portfolio value
    """
    holdings_result = await get_holdings(user_id)
    holdings = holdings_result["holdings"]

    if not holdings:
        return {"user_id": user_id, "total_value": 0, "holdings": [], "note": "No holdings found"}

    tickers = [h["ticker"] for h in holdings]
    prices = _fetch_prices_bulk(tickers)

    enriched = []
    total_value = 0.0
    total_invested = 0.0

    for h in holdings:
        current_price = prices.get(h["ticker"], 0.0)
        current_value = round(h["quantity"] * current_price, 2)
        invested = h["invested_value"]
        total_value += current_value
        total_invested += invested

        enriched.append({
            **h,
            "current_price": round(current_price, 2),
            "current_value": current_value,
        })

    return {
        "user_id": user_id,
        "total_invested": round(total_invested, 2),
        "total_current_value": round(total_value, 2),
        "holdings": enriched,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


@mcp.tool()
async def get_pnl(user_id: str) -> dict:
    """
    Calculate profit and loss for each holding and overall portfolio.

    Args:
        user_id:  UUID of the user

    Returns:
        Per-holding P&L, overall P&L, return percentage
    """
    portfolio = await get_portfolio_value(user_id)
    holdings = portfolio["holdings"]

    if not holdings:
        return {"user_id": user_id, "total_pnl": 0, "holdings": []}

    pnl_breakdown = []
    total_invested = 0.0
    total_current = 0.0

    for h in holdings:
        invested = h["invested_value"]
        current = h["current_value"]
        pnl = round(current - invested, 2)
        pnl_pct = round((pnl / invested) * 100, 2) if invested else 0

        total_invested += invested
        total_current += current

        pnl_breakdown.append({
            "ticker": h["ticker"],
            "quantity": h["quantity"],
            "avg_buy_price": h["avg_buy_price"],
            "current_price": h["current_price"],
            "invested_value": round(invested, 2),
            "current_value": round(current, 2),
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "status": "profit" if pnl >= 0 else "loss",
        })

    total_pnl = round(total_current - total_invested, 2)
    total_pnl_pct = round((total_pnl / total_invested) * 100, 2) if total_invested else 0

    # Sort by P&L descending
    pnl_breakdown.sort(key=lambda x: x["pnl"], reverse=True)

    return {
        "user_id": user_id,
        "total_invested": round(total_invested, 2),
        "total_current_value": round(total_current, 2),
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "overall_status": "profit" if total_pnl >= 0 else "loss",
        "top_gainer": pnl_breakdown[0] if pnl_breakdown else None,
        "top_loser": pnl_breakdown[-1] if pnl_breakdown else None,
        "holdings": pnl_breakdown,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


@mcp.tool()
async def get_allocation(user_id: str) -> dict:
    """
    Get portfolio allocation breakdown by sector and asset type.

    Args:
        user_id:  UUID of the user

    Returns:
        Sector-wise and asset-type-wise allocation as percentages
    """
    portfolio = await get_portfolio_value(user_id)
    holdings = portfolio["holdings"]
    total = portfolio["total_current_value"]

    if not holdings or total == 0:
        return {"user_id": user_id, "total_value": 0, "by_sector": {}, "by_asset_type": {}}

    # By sector
    sector_map: dict[str, float] = {}
    asset_map: dict[str, float] = {}

    for h in holdings:
        sector = h.get("sector", "Unknown")
        asset = h.get("asset_type", "equity")
        val = h["current_value"]

        sector_map[sector] = sector_map.get(sector, 0) + val
        asset_map[asset] = asset_map.get(asset, 0) + val

    by_sector = {
        k: {
            "value": round(v, 2),
            "allocation_pct": round((v / total) * 100, 2),
        }
        for k, v in sorted(sector_map.items(), key=lambda x: -x[1])
    }

    by_asset_type = {
        k: {
            "value": round(v, 2),
            "allocation_pct": round((v / total) * 100, 2),
        }
        for k, v in sorted(asset_map.items(), key=lambda x: -x[1])
    }

    # Concentration risk flag
    top_sector = max(sector_map, key=sector_map.get)
    top_sector_pct = round((sector_map[top_sector] / total) * 100, 2)
    concentration_warning = top_sector_pct > 40

    return {
        "user_id": user_id,
        "total_value": round(total, 2),
        "by_sector": by_sector,
        "by_asset_type": by_asset_type,
        "concentration_warning": concentration_warning,
        "note": (
            f"⚠️ {top_sector} sector is {top_sector_pct}% of portfolio — consider diversifying"
            if concentration_warning else "Allocation looks diversified"
        ),
    }


@mcp.tool()
async def add_holding(
    user_id: str,
    ticker: str,
    quantity: float,
    avg_buy_price: float,
    sector: str = "Unknown",
    asset_type: str = "equity",
) -> dict:
    """
    Add or update a holding in the portfolio.
    If ticker already exists for user, it updates quantity and recalculates avg price.

    Args:
        user_id:        UUID of the user
        ticker:         Stock/ETF ticker (e.g. RELIANCE.NS, AAPL)
        quantity:       Number of shares/units
        avg_buy_price:  Average purchase price per unit (₹)
        sector:         Sector (e.g. Technology, Banking)
        asset_type:     Asset class (equity, mutual_fund, etf, gold, crypto)

    Returns:
        Confirmation with the saved holding details
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT quantity, avg_buy_price FROM portfolio_holdings WHERE user_id=$1 AND ticker=$2",
            user_id, ticker,
        )

        if existing:
            old_qty = float(existing["quantity"])
            old_price = float(existing["avg_buy_price"])
            new_qty = old_qty + quantity
            new_avg = round((old_qty * old_price + quantity * avg_buy_price) / new_qty, 4)

            await conn.execute(
                """
                UPDATE portfolio_holdings
                SET quantity=$1, avg_buy_price=$2
                WHERE user_id=$3 AND ticker=$4
                """,
                new_qty, new_avg, user_id, ticker,
            )
            action = "updated"
        else:
            await conn.execute(
                """
                INSERT INTO portfolio_holdings (user_id, ticker, quantity, avg_buy_price, sector, asset_type)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                user_id, ticker, quantity, avg_buy_price, sector, asset_type,
            )
            action = "added"
            new_qty = quantity
            new_avg = avg_buy_price

        return {
            "action": action,
            "user_id": user_id,
            "ticker": ticker,
            "new_quantity": new_qty,
            "new_avg_buy_price": new_avg,
            "sector": sector,
            "asset_type": asset_type,
        }


@mcp.tool()
async def remove_holding(user_id: str, ticker: str) -> dict:
    """
    Remove a holding from the portfolio.

    Args:
        user_id:  UUID of the user
        ticker:   Ticker to remove

    Returns:
        Confirmation of deletion
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM portfolio_holdings WHERE user_id=$1 AND ticker=$2",
            user_id, ticker,
        )
        deleted = result.split()[-1] != "0"
        return {
            "user_id": user_id,
            "ticker": ticker,
            "deleted": deleted,
            "message": f"{ticker} removed from portfolio" if deleted else f"{ticker} not found",
        }


# ═════════════════════════════════════════════════════════════════════════════
# Calculator tools (merged from calculator_server.py) — pure math, no I/O
# ═════════════════════════════════════════════════════════════════════════════

def _xnpv(rate: float, cashflows: list[float], dates: list[date]) -> float:
    """Net present value for irregular cashflows."""
    t0 = dates[0]
    return sum(
        cf / (1 + rate) ** ((d - t0).days / 365.0)
        for cf, d in zip(cashflows, dates)
    )


@mcp.tool()
def compound_interest(
    principal: float,
    annual_rate: float,
    years: int,
    compounds_per_year: int = 12,
) -> dict:
    """
    Calculate compound interest growth.

    Args:
        principal:           Initial amount (₹)
        annual_rate:         Annual interest rate (e.g. 8.5 for 8.5%)
        years:               Investment duration in years
        compounds_per_year:  How many times interest compounds per year (default 12 = monthly)

    Returns:
        final_amount, interest_earned, year_by_year breakdown
    """
    r = annual_rate / 100
    n = compounds_per_year
    breakdown = []

    for y in range(1, years + 1):
        amount = principal * (1 + r / n) ** (n * y)
        breakdown.append({
            "year": y,
            "amount": round(amount, 2),
            "interest_earned": round(amount - principal, 2),
        })

    final = breakdown[-1]["amount"]
    return {
        "principal": principal,
        "annual_rate_pct": annual_rate,
        "years": years,
        "final_amount": final,
        "total_interest_earned": round(final - principal, 2),
        "year_by_year": breakdown,
    }


@mcp.tool()
def loan_emi(
    principal: float,
    annual_rate: float,
    tenure_months: int,
) -> dict:
    """
    Calculate monthly EMI for a loan.

    Args:
        principal:       Loan amount (₹)
        annual_rate:     Annual interest rate (e.g. 9.0 for 9%)
        tenure_months:   Loan duration in months

    Returns:
        emi, total_payment, total_interest, amortization schedule
    """
    r = annual_rate / (12 * 100)

    if r == 0:
        emi = principal / tenure_months
    else:
        emi = principal * r * (1 + r) ** tenure_months / ((1 + r) ** tenure_months - 1)

    emi = round(emi, 2)
    balance = principal
    schedule = []

    for month in range(1, tenure_months + 1):
        interest = round(balance * r, 2)
        principal_paid = round(emi - interest, 2)
        balance = round(max(balance - principal_paid, 0), 2)
        schedule.append({
            "month": month,
            "emi": emi,
            "principal_paid": principal_paid,
            "interest_paid": interest,
            "balance": balance,
        })

    total_payment = round(emi * tenure_months, 2)
    return {
        "loan_amount": principal,
        "annual_rate_pct": annual_rate,
        "tenure_months": tenure_months,
        "monthly_emi": emi,
        "total_payment": total_payment,
        "total_interest": round(total_payment - principal, 2),
        "amortization": schedule,
    }


@mcp.tool()
def inflation_adjusted(
    amount: float,
    annual_inflation_rate: float,
    years: int,
) -> dict:
    """
    Calculate real (inflation-adjusted) value of money over time.

    Args:
        amount:                 Current amount (₹)
        annual_inflation_rate:  Expected inflation % per year (e.g. 6.0)
        years:                  Number of years ahead

    Returns:
        future_value_needed (to match today's purchasing power),
        purchasing_power_loss
    """
    r = annual_inflation_rate / 100
    future_value_needed = round(amount * (1 + r) ** years, 2)
    purchasing_power = round(amount / (1 + r) ** years, 2)

    return {
        "current_amount": amount,
        "inflation_rate_pct": annual_inflation_rate,
        "years": years,
        "future_value_needed": future_value_needed,
        "purchasing_power_today": purchasing_power,
        "purchasing_power_loss_pct": round((1 - purchasing_power / amount) * 100, 2),
    }


@mcp.tool()
def fire_number(
    monthly_expenses: float,
    annual_return_rate: float = 7.0,
    withdrawal_rate: float = 4.0,
    inflation_rate: float = 6.0,
) -> dict:
    """
    Calculate the FIRE corpus — how much you need invested to retire.

    Args:
        monthly_expenses:    Current monthly spending (₹)
        annual_return_rate:  Expected portfolio return % (default 7%)
        withdrawal_rate:     Safe withdrawal rate % (default 4%)
        inflation_rate:      Expected inflation % (default 6%)

    Returns:
        fire_corpus, years_of_coverage, monthly_withdrawal_capacity
    """
    annual_expenses = monthly_expenses * 12
    corpus = round((annual_expenses / withdrawal_rate) * 100, 2)
    monthly_withdrawal = round(corpus * (withdrawal_rate / 100) / 12, 2)
    real_return = annual_return_rate - inflation_rate

    return {
        "monthly_expenses": monthly_expenses,
        "annual_expenses": annual_expenses,
        "withdrawal_rate_pct": withdrawal_rate,
        "fire_corpus_needed": corpus,
        "monthly_withdrawal_capacity": monthly_withdrawal,
        "real_return_rate_pct": real_return,
        "note": (
            "Based on the 4% rule. Corpus invested at "
            f"{annual_return_rate}% with {inflation_rate}% inflation "
            f"gives {real_return}% real return."
        ),
    }


@mcp.tool()
def xirr(
    cashflows: list[float],
    dates: list[str],
) -> dict:
    """
    Calculate XIRR — annualized return for irregular cashflows.

    Args:
        cashflows:  List of amounts. Investments are negative, redemptions positive.
                    e.g. [-10000, -10000, 25000]
        dates:      Corresponding dates in YYYY-MM-DD format.
                    e.g. ["2023-01-01", "2023-07-01", "2024-01-01"]

    Returns:
        xirr_pct — annualized return percentage
    """
    if len(cashflows) != len(dates):
        return {"error": "cashflows and dates must have equal length"}

    parsed_dates = [date.fromisoformat(d) for d in dates]

    try:
        rate = brentq(_xnpv, -0.999, 10.0, args=(cashflows, parsed_dates), xtol=1e-6)
        return {
            "xirr_pct": round(rate * 100, 4),
            "xirr_label": f"{round(rate * 100, 2)}% per annum",
        }
    except ValueError:
        return {"error": "Could not converge — check that cashflows have at least one sign change"}


@mcp.tool()
def sip_returns(
    monthly_investment: float,
    annual_rate: float,
    years: int,
) -> dict:
    """
    Calculate SIP (Systematic Investment Plan) maturity value.

    Args:
        monthly_investment:  Amount invested each month (₹)
        annual_rate:         Expected annual return % (e.g. 12.0)
        years:               Investment duration in years

    Returns:
        maturity_value, total_invested, total_gains
    """
    r = annual_rate / (12 * 100)
    n = years * 12
    maturity = round(monthly_investment * ((1 + r) ** n - 1) / r * (1 + r), 2)
    total_invested = round(monthly_investment * n, 2)

    return {
        "monthly_investment": monthly_investment,
        "annual_rate_pct": annual_rate,
        "years": years,
        "total_months": n,
        "total_invested": total_invested,
        "maturity_value": maturity,
        "total_gains": round(maturity - total_invested, 2),
        "wealth_ratio": round(maturity / total_invested, 2),
    }


@mcp.tool()
def goal_monthly_saving(
    goal_amount: float,
    years: int,
    annual_rate: float = 10.0,
) -> dict:
    """
    Calculate how much to save/invest monthly to reach a financial goal.

    Args:
        goal_amount:   Target corpus (₹)
        years:         Time available in years
        annual_rate:   Expected annual return % (default 10%)

    Returns:
        monthly_saving_needed, total_invested, gains
    """
    r = annual_rate / (12 * 100)
    n = years * 12
    monthly = round(goal_amount * r / ((1 + r) ** n - 1) / (1 + r), 2)
    total_invested = round(monthly * n, 2)

    return {
        "goal_amount": goal_amount,
        "years": years,
        "annual_rate_pct": annual_rate,
        "monthly_saving_needed": monthly,
        "total_invested": total_invested,
        "total_gains": round(goal_amount - total_invested, 2),
    }


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()