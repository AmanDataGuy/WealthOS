# calculator_server.py
# Pure financial math engine — no external APIs, no DB.
# All calculations are stateless and deterministic.
#
# Tools:
#   compound_interest       — growth projection over time
#   loan_emi                — monthly EMI for a loan
#   inflation_adjusted      — real value of money after inflation
#   fire_number             — corpus needed to retire
#   xirr                    — actual annualized return on cashflows
#   sip_returns             — SIP maturity value
#   goal_monthly_saving     — how much to save monthly to hit a goal

import logging
from datetime import date, timedelta
from scipy.optimize import brentq
from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("calculator-mcp")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _xnpv(rate: float, cashflows: list[float], dates: list[date]) -> float:
    """Net present value for irregular cashflows."""
    t0 = dates[0]
    return sum(
        cf / (1 + rate) ** ((d - t0).days / 365.0)
        for cf, d in zip(cashflows, dates)
    )


# ── Tools ─────────────────────────────────────────────────────────────────────

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


if __name__ == "__main__":
    mcp.run()