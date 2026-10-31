"""
Indian equity capital-gains tax: LTCG/STCG estimation + a tax-loss/gain
harvesting suggestion function, called by rebalancing_agent for holdings on
.NS/.BO tickers.

Rates verified live via web search on 2026-09-16, effective since Budget
2024 (23 Jul 2024), unchanged through Budget 2026:
  - LTCG (held > 12 months): 12.5% on aggregate gains above Rs 1.25 lakh/yr
  - STCG (held <= 12 months): 20% flat, no exemption threshold
Sources: business-standard.com (Budget 2024 LTCG/STCG hike),
         bajajamc.com/knowledge-centre (LTCG on mutual funds)

# ponytail: rates are hardcoded as of the 2024 Budget. India's capital-gains
# rules change with the annual Finance Act — re-verify these two constants
# (and the exemption threshold) before trusting this for a future tax year.
# Also ignores surcharge and the 4% health/education cess, both of which
# depend on the user's total income slab, not modeled here — ballpark
# estimate for a rebalancing suggestion, not a tax filing.
#
# Holding period is derived from `added_at` (when the row was added to our
# DB), not a real purchase date — the schema doesn't track one. This is a
# reasonable proxy if the user added holdings when they bought them, but
# is not authoritative. Upgrade path: add a real purchase_date column.
"""

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel

LTCG_RATE = 0.125
LTCG_EXEMPTION = 125_000.0    # Rs, aggregate per financial year
STCG_RATE = 0.20
LONG_TERM_DAYS = 365


def is_indian_ticker(ticker: str) -> bool:
    return ticker.upper().endswith((".NS", ".BO"))


class HarvestSuggestion(BaseModel):
    ticker: str
    action: str                              # harvest_loss | realize_ltcg_within_exemption | hold_for_ltcg
    reason: str
    unrealized_pnl: float
    holding_period_days: Optional[int] = None
    tax_saved_or_owed: Optional[float] = None


def classify_holding_period(added_at) -> tuple[Optional[int], bool]:
    """(days_held, is_long_term) — (None, False) if added_at is unknown."""
    if not added_at:
        return None, False
    if isinstance(added_at, str):
        added_at = datetime.fromisoformat(added_at)
    if added_at.tzinfo is None:
        added_at = added_at.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - added_at).days
    return days, days > LONG_TERM_DAYS


def estimate_capital_gains_tax(unrealized_pnl: float, is_long_term: bool, ytd_realized_ltcg: float = 0.0) -> float:
    """Rough tax if this position were sold today. Ignores surcharge/cess."""
    if unrealized_pnl <= 0:
        return 0.0
    if is_long_term:
        headroom_before = max(0.0, LTCG_EXEMPTION - ytd_realized_ltcg)
        taxable = max(0.0, unrealized_pnl - headroom_before)
        return taxable * LTCG_RATE
    return unrealized_pnl * STCG_RATE


def suggest_harvesting(
    holdings,
    macro_risk_high: bool = False,
    ytd_realized_ltcg: float = 0.0,
) -> list[HarvestSuggestion]:
    """
    For each Indian equity holding with an unrealized loss or gain, suggest
    whether harvesting now makes sense:
      - Losses: always worth realizing — India allows carrying forward
        capital losses up to 8 assessment years to offset future gains.
      - Long-term gains still inside the yearly exemption: realizing now is
        tax-free; a future year's exemption may already be used up.
      - Short-term gains close to crossing into long-term: flag the 20% vs
        12.5% difference so the user can decide whether to wait.
    """
    suggestions = []
    for h in holdings:
        if not is_indian_ticker(h.ticker) or h.pnl is None:
            continue
        days, is_lt = classify_holding_period(getattr(h, "held_since", None))

        if h.pnl < 0:
            reason = (
                f"Unrealized loss of Rs {abs(h.pnl):,.0f} — India allows carrying "
                "forward capital losses up to 8 assessment years to offset future "
                "gains. Harvesting now locks in the offset."
            )
            if macro_risk_high:
                reason += " Elevated macro risk in the current analysis makes this a reasonable time to de-risk anyway."
            suggestions.append(HarvestSuggestion(
                ticker=h.ticker, action="harvest_loss", reason=reason,
                unrealized_pnl=h.pnl, holding_period_days=days,
            ))
        elif h.pnl > 0 and is_lt:
            tax = estimate_capital_gains_tax(h.pnl, True, ytd_realized_ltcg)
            if tax == 0.0:
                suggestions.append(HarvestSuggestion(
                    ticker=h.ticker, action="realize_ltcg_within_exemption",
                    reason=(
                        f"Rs {h.pnl:,.0f} long-term gain currently falls within the "
                        f"Rs {LTCG_EXEMPTION:,.0f} annual LTCG exemption — realizing "
                        "it now would be tax-free."
                    ),
                    unrealized_pnl=h.pnl, holding_period_days=days, tax_saved_or_owed=0.0,
                ))
        elif h.pnl > 0 and not is_lt and days is not None:
            days_to_lt = LONG_TERM_DAYS - days
            if 0 < days_to_lt <= 60:
                suggestions.append(HarvestSuggestion(
                    ticker=h.ticker, action="hold_for_ltcg",
                    reason=(
                        f"{days_to_lt} days from long-term status — selling now pays "
                        "20% STCG instead of 12.5% LTCG. Consider waiting if there's "
                        "no urgent need to sell."
                    ),
                    unrealized_pnl=h.pnl, holding_period_days=days,
                ))
    return suggestions


if __name__ == "__main__":
    from types import SimpleNamespace
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    holdings = [
        SimpleNamespace(ticker="TCS.NS", pnl=-5000, held_since=(now - timedelta(days=400)).isoformat()),
        SimpleNamespace(ticker="RELIANCE.NS", pnl=50000, held_since=(now - timedelta(days=400)).isoformat()),
        SimpleNamespace(ticker="INFY.NS", pnl=8000, held_since=(now - timedelta(days=340)).isoformat()),
        SimpleNamespace(ticker="AAPL", pnl=-1000, held_since=(now - timedelta(days=400)).isoformat()),
    ]
    out = suggest_harvesting(holdings)
    assert len(out) == 3, f"expected 3 suggestions (AAPL skipped, not Indian), got {len(out)}"
    assert out[0].action == "harvest_loss"
    assert out[1].action == "realize_ltcg_within_exemption"
    assert out[2].action == "hold_for_ltcg"
    assert estimate_capital_gains_tax(200_000, True, 0.0) == 75_000 * LTCG_RATE
    assert estimate_capital_gains_tax(50_000, False, 0.0) == 50_000 * STCG_RATE
    assert estimate_capital_gains_tax(-1000, True, 0.0) == 0.0
    print("tax_calculator self-check passed.")
