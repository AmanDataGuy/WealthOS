# portfolio_server.py
# Portfolio data layer — reads holdings from Postgres, prices from yfinance.
#
# Tools:
#   get_holdings          — fetch user's portfolio from DB
#   get_portfolio_value   — holdings × live prices = current value
#   get_pnl               — profit/loss per stock and overall
#   get_allocation        — sector/asset class breakdown as percentages
#   add_holding           — insert or update a holding in DB
#   remove_holding        — delete a holding from DB

import os
import logging
import asyncio
from datetime import datetime

import asyncpg
import yfinance as yf
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("portfolio-mcp")

# ── DB connection ──────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv("WEALTHOS_DB_URL", "")
DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


async def get_db():
    return await asyncpg.connect(DATABASE_URL)


# ── Helpers ───────────────────────────────────────────────────────────────────

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


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_holdings(user_id: str) -> dict:
    """
    Fetch user's portfolio holdings from the database.

    Args:
        user_id:  UUID of the user

    Returns:
        List of holdings with ticker, quantity, avg_buy_price, sector, asset_type
    """
    conn = await get_db()
    try:
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
    finally:
        await conn.close()


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
        "fetched_at": datetime.utcnow().isoformat(),
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
        "fetched_at": datetime.utcnow().isoformat(),
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
    conn = await get_db()
    try:
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
    finally:
        await conn.close()


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
    conn = await get_db()
    try:
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
    finally:
        await conn.close()


if __name__ == "__main__":
    mcp.run()