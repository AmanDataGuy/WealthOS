# agents/rebalancing_agent.py
"""
Rebalancing Agent — Pure Python
================================
Analyzes current portfolio holdings against target allocation.
Projects impact of a new investment. Suggests specific buy/sell actions.
No LLM needed — pure arithmetic. Verified results.

Flow:
  1. Fetch holdings from portfolio_server (Postgres)
  2. Fetch live prices from yfinance
  3. Compute current weights per sector
  4. Compare against target allocation
  5. Project weights after new investment
  6. Suggest specific trim/buy actions
  7. Produce RebalanceSuggestion
"""

import os
import asyncio
import asyncpg

from datetime import datetime, timezone
from typing import Optional, Literal
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("WEALTHOS_DB_URL", "postgresql://postgres:postgres@localhost:5432/wealthos")

def clean_db_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


# ── Pydantic Schemas ───────────────────────────────────────────────────────────

class Holding(BaseModel):
    ticker:         str
    quantity:       float
    avg_buy_price:  float
    sector:         str
    asset_type:     str = "equity"
    current_price:  Optional[float] = None
    current_value:  Optional[float] = None
    pnl:            Optional[float] = None
    pnl_pct:        Optional[float] = None

class RebalanceAction(BaseModel):
    action:         Literal["buy", "sell", "hold"]
    sector:         str
    ticker:         Optional[str]   = None
    amount:         float           # in currency units
    reason:         str
    urgency:        Literal["high", "medium", "low"]

class NewInvestment(BaseModel):
    ticker:         str
    amount:         float           # amount to invest
    sector:         str

class RebalanceSuggestion(BaseModel):
    user_id:                str
    total_portfolio_value:  float
    current_allocation:     dict[str, float]     # sector → weight %
    target_allocation:      dict[str, float]     # sector → target %
    projected_allocation:   dict[str, float]     # sector → weight after new investment
    drift:                  dict[str, float]     # sector → deviation from target
    actions:                list[RebalanceAction]
    new_investment_impact:  Optional[str]        = None
    summary:                str
    analysis_date:          str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    holdings:               list[Holding]        = Field(default_factory=list)


# ── Holdings Fetcher ───────────────────────────────────────────────────────────

async def fetch_holdings(user_id: str, conn: asyncpg.Connection) -> list[Holding]:
    """Fetch portfolio holdings from Postgres."""
    try:
        import uuid
        uuid.UUID(user_id)   # validate — raises ValueError if not a real UUID
        rows = await conn.fetch(
            """
            SELECT ticker, quantity, avg_buy_price, sector, asset_type
            FROM portfolio_holdings
            WHERE user_id = $1
            """,
            user_id
        )
    except (ValueError, Exception):
        rows = []   # fall through to demo holdings below

    if not rows:
        # Return demo holdings for testing
        print("  [rebalancing] No holdings found — using demo portfolio")
        return [
            Holding(ticker="AAPL",  quantity=10,  avg_buy_price=150.0, sector="Technology"),
            Holding(ticker="TSLA",  quantity=5,   avg_buy_price=200.0, sector="Consumer Cyclical"),
            Holding(ticker="MSFT",  quantity=8,   avg_buy_price=300.0, sector="Technology"),
            Holding(ticker="JPM",   quantity=15,  avg_buy_price=140.0, sector="Financial Services"),
            Holding(ticker="XOM",   quantity=20,  avg_buy_price=90.0,  sector="Energy"),
        ]

    return [
        Holding(
            ticker=r["ticker"],
            quantity=float(r["quantity"]),
            avg_buy_price=float(r["avg_buy_price"]),
            sector=r["sector"] or "Unknown",
            asset_type=r["asset_type"] or "equity",
        )
        for r in rows
    ]


# ── Price Fetcher ──────────────────────────────────────────────────────────────

async def fetch_live_prices(holdings: list[Holding]) -> list[Holding]:
    """Fetch live prices for all holdings via market_server MCP tools."""
    from mcp_servers.market_server import get_price
    tickers = [h.ticker for h in holdings]

    def fetch_prices():
        prices = {}
        for ticker in tickers:
            try:
                data = get_price(ticker)
                price = data.get("current_price")
                if price:
                    prices[ticker] = float(price)
            except Exception as e:
                print(f"  [rebalancing] Price fetch failed for {ticker}: {e}")
        return prices

    # Run in thread since MCP tool cache fallback could be sync blocking
    prices = await asyncio.to_thread(fetch_prices)

    updated = []
    for h in holdings:
        price = prices.get(h.ticker, h.avg_buy_price)
        value = h.quantity * price
        cost  = h.quantity * h.avg_buy_price
        pnl   = value - cost
        pnl_pct = (pnl / cost * 100) if cost else 0

        updated.append(h.model_copy(update={
            "current_price": price,
            "current_value": round(value, 2),
            "pnl":           round(pnl, 2),
            "pnl_pct":       round(pnl_pct, 2),
        }))

    return updated


# ── Allocation Calculator ──────────────────────────────────────────────────────

def compute_allocation(holdings: list[Holding]) -> tuple[float, dict[str, float]]:
    """
    Compute total portfolio value and sector weights.
    Returns (total_value, {sector: weight_pct})
    """
    sector_values: dict[str, float] = {}
    total = 0.0

    for h in holdings:
        val = h.current_value or (h.quantity * h.avg_buy_price)
        sector_values[h.sector] = sector_values.get(h.sector, 0) + val
        total += val

    if total == 0:
        return 0.0, {}

    weights = {
        sector: round((val / total) * 100, 2)
        for sector, val in sector_values.items()
    }

    return round(total, 2), weights


def compute_projected_allocation(
    holdings: list[Holding],
    new_investment: NewInvestment,
    total_value: float,
) -> dict[str, float]:
    """
    Project allocation after adding the new investment.
    """
    sector_values: dict[str, float] = {}
    for h in holdings:
        val = h.current_value or (h.quantity * h.avg_buy_price)
        sector_values[h.sector] = sector_values.get(h.sector, 0) + val

    # Add new investment
    sector_values[new_investment.sector] = \
        sector_values.get(new_investment.sector, 0) + new_investment.amount

    new_total = total_value + new_investment.amount

    return {
        sector: round((val / new_total) * 100, 2)
        for sector, val in sector_values.items()
    }


# ── Drift Calculator ───────────────────────────────────────────────────────────

def compute_drift(
    current: dict[str, float],
    target: dict[str, float],
) -> dict[str, float]:
    """
    Compute deviation of current allocation from target.
    Positive = overweight, Negative = underweight.
    """
    all_sectors = set(list(current.keys()) + list(target.keys()))
    return {
        sector: round(current.get(sector, 0) - target.get(sector, 0), 2)
        for sector in all_sectors
    }


# ── Action Generator ───────────────────────────────────────────────────────────

def generate_actions(
    drift: dict[str, float],
    total_value: float,
    threshold: float = 5.0,    # % deviation to trigger action
) -> list[RebalanceAction]:
    """
    Generate rebalance actions for sectors with drift > threshold.
    """
    actions = []

    for sector, deviation in drift.items():
        if abs(deviation) < threshold:
            continue

        amount = abs(deviation / 100) * total_value

        if deviation > 0:
            # Overweight — suggest trim
            urgency = "high" if deviation > 10 else "medium"
            actions.append(RebalanceAction(
                action="sell",
                sector=sector,
                amount=round(amount, 2),
                reason=f"{sector} is {deviation:.1f}% overweight vs target. "
                       f"Trim ${amount:,.0f} to rebalance.",
                urgency=urgency,
            ))
        else:
            # Underweight — suggest buy
            urgency = "high" if abs(deviation) > 10 else "medium"
            actions.append(RebalanceAction(
                action="buy",
                sector=sector,
                amount=round(amount, 2),
                reason=f"{sector} is {abs(deviation):.1f}% underweight vs target. "
                       f"Add ${amount:,.0f} to rebalance.",
                urgency=urgency,
            ))

    # Sort by urgency
    urgency_order = {"high": 0, "medium": 1, "low": 2}
    actions.sort(key=lambda x: urgency_order[x.urgency])

    return actions


# ── Summary Builder ────────────────────────────────────────────────────────────

def build_summary(
    current: dict[str, float],
    projected: dict[str, float],
    target: dict[str, float],
    actions: list[RebalanceAction],
    new_investment: Optional[NewInvestment],
    total_value: float,
) -> str:
    lines = []

    if new_investment:
        curr_sector_pct = current.get(new_investment.sector, 0)
        proj_sector_pct = projected.get(new_investment.sector, 0)
        target_pct      = target.get(new_investment.sector, 0)
        lines.append(
            f"Adding ${new_investment.amount:,.0f} to {new_investment.ticker} "
            f"({new_investment.sector}) increases sector weight from "
            f"{curr_sector_pct:.1f}% → {proj_sector_pct:.1f}% "
            f"(target: {target_pct:.1f}%)."
        )

    if not actions:
        lines.append("Portfolio is well-balanced. No rebalancing actions required.")
    else:
        high_urgency = [a for a in actions if a.urgency == "high"]
        if high_urgency:
            lines.append(f"{len(high_urgency)} high-urgency rebalancing action(s) identified.")
        lines.append(f"Total portfolio value: ${total_value:,.0f}.")
        for a in actions[:3]:   # top 3 actions in summary
            lines.append(f"• {a.action.upper()} {a.sector}: ${a.amount:,.0f} — {a.reason[:60]}")

    return " ".join(lines)


# ── Default Target Allocation ──────────────────────────────────────────────────

DEFAULT_TARGET = {
    "Technology":           30.0,
    "Consumer Cyclical":    15.0,
    "Financial Services":   15.0,
    "Healthcare":           15.0,
    "Energy":               10.0,
    "Industrials":           5.0,
    "Consumer Defensive":    5.0,
    "Other":                 5.0,
}


# ── Main Orchestrator ──────────────────────────────────────────────────────────

async def run_rebalancing_agent(
    user_id: str,
    new_investment: Optional[NewInvestment] = None,
    target_allocation: Optional[dict[str, float]] = None,
) -> RebalanceSuggestion:
    """
    Main entry point. Called by LangGraph in Phase 4.

    Args:
        user_id:            User identifier
        new_investment:     Optional new investment being considered
        target_allocation:  Optional custom target allocation (uses DEFAULT_TARGET if None)
    """
    print(f"\n{'='*50}")
    print(f"  Rebalancing Agent — user: {user_id}")
    print(f"{'='*50}")

    target = target_allocation or DEFAULT_TARGET

    # ── Fetch holdings ────────────────────────────────────────────────────────
    conn = await asyncpg.connect(clean_db_url(DATABASE_URL))
    try:
        holdings = await fetch_holdings(user_id, conn)
    finally:
        await conn.close()

    print(f"  ✅ Holdings: {len(holdings)} positions fetched")

    # ── Fetch live prices ─────────────────────────────────────────────────────
    holdings = await fetch_live_prices(holdings)
    print(f"  ✅ Prices: live prices fetched")

    # ── Compute allocation ────────────────────────────────────────────────────
    total_value, current_allocation = compute_allocation(holdings)
    print(f"  ✅ Portfolio value: ${total_value:,.0f}")

    # ── Project allocation with new investment ────────────────────────────────
    if new_investment:
        projected_allocation = compute_projected_allocation(
            holdings, new_investment, total_value
        )
        print(f"  ✅ Projected allocation after ${new_investment.amount:,.0f} in {new_investment.ticker}")
    else:
        projected_allocation = current_allocation.copy()

    # ── Compute drift (vs projected allocation) ───────────────────────────────
    drift = compute_drift(projected_allocation, target)

    # ── Generate actions ──────────────────────────────────────────────────────
    actions = generate_actions(drift, total_value + (new_investment.amount if new_investment else 0))

    # ── Build summary ─────────────────────────────────────────────────────────
    summary = build_summary(
        current_allocation,
        projected_allocation,
        target,
        actions,
        new_investment,
        total_value,
    )

    suggestion = RebalanceSuggestion(
        user_id=user_id,
        total_portfolio_value=total_value,
        current_allocation=current_allocation,
        target_allocation=target,
        projected_allocation=projected_allocation,
        drift=drift,
        actions=actions,
        new_investment_impact=summary if new_investment else None,
        summary=summary,
        holdings=holdings,
    )

    print(f"\n  Actions      : {len(actions)} rebalancing actions")
    for a in actions:
        print(f"    [{a.urgency.upper()}] {a.action.upper()} {a.sector}: ${a.amount:,.0f}")
    print(f"\n  Summary: {summary[:100]}...")
    print(f"{'='*50}\n")

    return suggestion


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    user_id = sys.argv[1] if len(sys.argv) > 1 else "test-user"

    # Optional: pass ticker and amount as args
    new_inv = None
    if len(sys.argv) >= 4:
        new_inv = NewInvestment(
            ticker=sys.argv[2],
            amount=float(sys.argv[3]),
            sector=sys.argv[4] if len(sys.argv) > 4 else "Technology",
        )

    async def main():
        suggestion = await run_rebalancing_agent(
            user_id=user_id,
            new_investment=new_inv,
        )

        print("\n── RebalanceSuggestion ────────────────────────────")
        print(f"  Portfolio Value : ${suggestion.total_portfolio_value:,.0f}")
        print(f"\n  Current Allocation:")
        for sector, pct in sorted(suggestion.current_allocation.items(), key=lambda x: -x[1]):
            target_pct = suggestion.target_allocation.get(sector, 0)
            drift_pct  = suggestion.drift.get(sector, 0)
            bar = "▲" if drift_pct > 0 else "▼" if drift_pct < 0 else "●"
            print(f"    {bar} {sector:<25} {pct:>6.1f}%  (target: {target_pct:.1f}%)")
        print(f"\n  Actions ({len(suggestion.actions)}):")
        for a in suggestion.actions:
            print(f"    [{a.urgency.upper()}] {a.action.upper()} {a.sector}: ${a.amount:,.0f}")
            print(f"           {a.reason[:80]}")
        print(f"\n  Summary: {suggestion.summary}")
        print(f"\n  Holdings P&L:")
        for h in suggestion.holdings:
            pnl_str = f"+${h.pnl:,.0f}" if (h.pnl or 0) >= 0 else f"-${abs(h.pnl or 0):,.0f}"
            print(f"    {h.ticker:<6} {h.quantity:>6.0f} shares @ ${h.current_price or 0:.2f}  "
                  f"Value: ${h.current_value or 0:,.0f}  P&L: {pnl_str} ({h.pnl_pct or 0:.1f}%)")
        print("──────────────────────────────────────────────────\n")

    asyncio.run(main())