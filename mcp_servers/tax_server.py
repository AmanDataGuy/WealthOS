# tax_server.py
# India-specific tax calculations — no external API, no DB.
# All logic based on FY 2024-25 / AY 2025-26 tax slabs.
#
# Tools:
#   calculate_tax            — old vs new regime comparison
#   capital_gains_tax        — STCG / LTCG for stocks, MF, property
#   tax_saving_suggestions   — 80C, 80D, HRA deduction tips
#   advance_tax_schedule     — quarterly payment deadlines & amounts

import logging
from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("tax-mcp")


# ── Tax Slab Data (FY 2024-25) ─────────────────────────────────────────────────

# Old regime slabs (with deductions)
OLD_REGIME_SLABS = [
    (250_000,   0.00),
    (500_000,   0.05),
    (1_000_000, 0.20),
    (float("inf"), 0.30),
]

# New regime slabs (FY 2024-25, post-Budget 2024)
NEW_REGIME_SLABS = [
    (300_000,   0.00),
    (700_000,   0.05),
    (1_000_000, 0.10),
    (1_200_000, 0.15),
    (1_500_000, 0.20),
    (float("inf"), 0.30),
]

STANDARD_DEDUCTION_NEW = 75_000   # FY 2024-25
STANDARD_DEDUCTION_OLD = 50_000


# ── Helpers ───────────────────────────────────────────────────────────────────

def _compute_tax(income: float, slabs: list) -> float:
    """Compute tax from slab list."""
    tax = 0.0
    prev = 0
    for limit, rate in slabs:
        if income <= prev:
            break
        taxable = min(income, limit) - prev
        tax += taxable * rate
        prev = limit
    return tax


def _apply_surcharge(tax: float, income: float) -> float:
    """Apply surcharge based on income level."""
    if income <= 5_000_000:
        surcharge_rate = 0.0
    elif income <= 10_000_000:
        surcharge_rate = 0.10
    elif income <= 20_000_000:
        surcharge_rate = 0.15
    elif income <= 50_000_000:
        surcharge_rate = 0.25
    else:
        surcharge_rate = 0.37
    return tax * surcharge_rate


def _apply_cess(tax_plus_surcharge: float) -> float:
    """4% health and education cess."""
    return tax_plus_surcharge * 0.04


def _total_tax(income: float, slabs: list) -> dict:
    base_tax = _compute_tax(income, slabs)
    surcharge = _apply_surcharge(base_tax, income)
    cess = _apply_cess(base_tax + surcharge)
    total = base_tax + surcharge + cess
    return {
        "base_tax": round(base_tax, 2),
        "surcharge": round(surcharge, 2),
        "cess": round(cess, 2),
        "total_tax": round(total, 2),
        "effective_rate_pct": round((total / income) * 100, 2) if income > 0 else 0,
        "take_home": round(income - total, 2),
    }


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def calculate_tax(
    gross_income: float,
    section_80c: float = 0,
    section_80d: float = 0,
    hra_exemption: float = 0,
    other_deductions: float = 0,
) -> dict:
    """
    Compare old vs new income tax regime for FY 2024-25.

    Args:
        gross_income:       Total annual income (₹)
        section_80c:        80C investments (max 1,50,000) — PF, ELSS, LIC etc.
        section_80d:        80D health insurance premium (max 25,000 self / 50,000 senior parents)
        hra_exemption:      HRA exemption if living in rented accommodation
        other_deductions:   Any other eligible deductions

    Returns:
        old_regime, new_regime tax breakdown + recommendation
    """
    # Old regime
    old_deductions = (
        STANDARD_DEDUCTION_OLD
        + min(section_80c, 150_000)
        + min(section_80d, 75_000)
        + hra_exemption
        + other_deductions
    )
    old_taxable = max(gross_income - old_deductions, 0)
    old_result = _total_tax(old_taxable, OLD_REGIME_SLABS)

    # New regime (standard deduction only, no other deductions allowed)
    new_taxable = max(gross_income - STANDARD_DEDUCTION_NEW, 0)

    # Rebate u/s 87A — new regime: full rebate if taxable income ≤ 7L
    new_result = _total_tax(new_taxable, NEW_REGIME_SLABS)
    if new_taxable <= 700_000:
        new_result = {**new_result, "total_tax": 0, "take_home": gross_income,
                      "rebate_applied": True, "effective_rate_pct": 0}

    # Old regime 87A rebate: rebate up to ₹12,500 if taxable ≤ 5L
    if old_taxable <= 500_000:
        old_result["total_tax"] = max(old_result["total_tax"] - 12_500, 0)
        old_result["rebate_applied"] = True

    savings = round(old_result["total_tax"] - new_result["total_tax"], 2)
    recommendation = "new_regime" if savings > 0 else "old_regime"

    return {
        "gross_income": gross_income,
        "old_regime": {
            "taxable_income": old_taxable,
            "total_deductions": old_deductions,
            **old_result,
        },
        "new_regime": {
            "taxable_income": new_taxable,
            "total_deductions": STANDARD_DEDUCTION_NEW,
            **new_result,
        },
        "tax_savings_with_new_regime": savings,
        "recommendation": recommendation,
        "note": f"Switch to {recommendation} to save ₹{abs(savings):,.0f}",
    }


@mcp.tool()
def capital_gains_tax(
    buy_price: float,
    sell_price: float,
    quantity: float,
    holding_days: int,
    asset_type: str = "equity",
) -> dict:
    """
    Calculate capital gains tax (India, FY 2024-25).

    Args:
        buy_price:     Purchase price per unit (₹)
        sell_price:    Sale price per unit (₹)
        quantity:      Number of units/shares
        holding_days:  Days between buy and sell
        asset_type:    "equity", "mutual_fund", "debt_fund", "property", "gold"

    Returns:
        gain_type (STCG/LTCG), tax_rate, tax_amount, net_profit
    """
    gain = (sell_price - buy_price) * quantity
    invested = buy_price * quantity
    proceeds = sell_price * quantity

    # Determine STCG vs LTCG threshold
    ltcg_threshold = {
        "equity": 365,
        "mutual_fund": 365,
        "debt_fund": 1095,   # 3 years — note: indexation removed from FY24-25 Budget
        "property": 730,     # 2 years
        "gold": 1095,        # 3 years
    }.get(asset_type, 365)

    is_ltcg = holding_days >= ltcg_threshold

    # Tax rates (post-Budget 2024)
    if asset_type in ("equity", "mutual_fund"):
        if is_ltcg:
            gain_type = "LTCG"
            rate = 0.125   # 12.5% flat (removed indexation)
            exemption = 125_000  # ₹1.25L exempt
            taxable_gain = max(gain - exemption, 0)
        else:
            gain_type = "STCG"
            rate = 0.20    # 20% flat (was 15%, increased in Budget 2024)
            taxable_gain = gain
    elif asset_type == "debt_fund":
        gain_type = "LTCG" if is_ltcg else "STCG"
        rate = 0.30        # Taxed at slab rate (simplified as 30% here)
        taxable_gain = gain
    elif asset_type == "property":
        if is_ltcg:
            gain_type = "LTCG"
            rate = 0.125   # 12.5% without indexation (Budget 2024)
            taxable_gain = gain
        else:
            gain_type = "STCG"
            rate = 0.30
            taxable_gain = gain
    else:  # gold, others
        gain_type = "LTCG" if is_ltcg else "STCG"
        rate = 0.125 if is_ltcg else 0.30
        taxable_gain = gain

    tax = round(max(taxable_gain * rate, 0), 2)
    net_profit = round(gain - tax, 2)

    return {
        "asset_type": asset_type,
        "buy_price": buy_price,
        "sell_price": sell_price,
        "quantity": quantity,
        "total_invested": round(invested, 2),
        "total_proceeds": round(proceeds, 2),
        "gross_gain": round(gain, 2),
        "holding_days": holding_days,
        "gain_type": gain_type,
        "tax_rate_pct": round(rate * 100, 1),
        "taxable_gain": round(taxable_gain, 2),
        "tax_payable": tax,
        "net_profit_after_tax": net_profit,
        "return_pct": round((gain / invested) * 100, 2) if invested else 0,
    }


@mcp.tool()
def tax_saving_suggestions(
    gross_income: float,
    current_80c: float = 0,
    has_health_insurance: bool = False,
    has_home_loan: bool = False,
    is_renting: bool = False,
    monthly_rent: float = 0,
    monthly_hra_received: float = 0,
) -> dict:
    """
    Suggest tax-saving options under old regime for a given income.

    Args:
        gross_income:           Annual gross income (₹)
        current_80c:            Already invested under 80C (₹)
        has_health_insurance:   Whether health insurance premium is being paid
        has_home_loan:          Whether repaying a home loan
        is_renting:             Whether living in a rented house
        monthly_rent:           Monthly rent paid (₹)
        monthly_hra_received:   Monthly HRA component in salary (₹)

    Returns:
        list of suggestions with potential tax savings
    """
    suggestions = []
    total_potential_saving = 0

    # 80C headroom
    max_80c = 150_000
    remaining_80c = max(max_80c - current_80c, 0)
    if remaining_80c > 0:
        tax_saving = round(remaining_80c * 0.30, 2)  # approx 30% bracket
        suggestions.append({
            "section": "80C",
            "description": f"Invest ₹{remaining_80c:,.0f} more in ELSS/PPF/NPS to fully utilize 80C",
            "max_deduction": max_80c,
            "remaining_room": remaining_80c,
            "potential_tax_saving": tax_saving,
        })
        total_potential_saving += tax_saving

    # 80D health insurance
    if not has_health_insurance:
        saving = round(25_000 * 0.30, 2)
        suggestions.append({
            "section": "80D",
            "description": "Buy health insurance — deduction up to ₹25,000 (₹50,000 for senior citizen parents)",
            "max_deduction": 25_000,
            "potential_tax_saving": saving,
        })
        total_potential_saving += saving

    # 80EE / 24(b) home loan interest
    if has_home_loan:
        suggestions.append({
            "section": "24(b)",
            "description": "Home loan interest deduction up to ₹2,00,000 under Section 24(b)",
            "max_deduction": 200_000,
            "potential_tax_saving": round(200_000 * 0.30, 2),
            "note": "Ensure you claim this when filing ITR",
        })

    # HRA
    if is_renting and monthly_rent > 0 and monthly_hra_received > 0:
        annual_hra = monthly_hra_received * 12
        annual_rent_paid = monthly_rent * 12
        rent_excess = max(annual_rent_paid - 0.10 * gross_income, 0)
        hra_exemption = min(annual_hra, rent_excess)
        saving = round(hra_exemption * 0.30, 2)
        suggestions.append({
            "section": "HRA",
            "description": "House Rent Allowance exemption — submit rent receipts to employer",
            "estimated_exemption": round(hra_exemption, 2),
            "potential_tax_saving": saving,
        })
        total_potential_saving += saving

    # NPS 80CCD(1B)
    nps_extra = 50_000
    saving = round(nps_extra * 0.30, 2)
    suggestions.append({
        "section": "80CCD(1B)",
        "description": "Additional ₹50,000 NPS contribution over and above 80C limit",
        "max_deduction": nps_extra,
        "potential_tax_saving": saving,
    })
    total_potential_saving += saving

    return {
        "gross_income": gross_income,
        "suggestions": suggestions,
        "total_potential_tax_saving": round(total_potential_saving, 2),
        "note": "Savings estimated at 30% bracket. Actual saving depends on your tax slab.",
    }


@mcp.tool()
def advance_tax_schedule(
    estimated_annual_income: float,
    tds_already_deducted: float = 0,
) -> dict:
    """
    Calculate advance tax installments and deadlines (FY 2024-25).

    Args:
        estimated_annual_income:  Expected total income for the year (₹)
        tds_already_deducted:     TDS already deducted by employer / bank (₹)

    Returns:
        total_tax_liability, net_payable, installment schedule with deadlines
    """
    # Use new regime as default estimate
    new_taxable = max(estimated_annual_income - STANDARD_DEDUCTION_NEW, 0)
    tax_info = _total_tax(new_taxable, NEW_REGIME_SLABS)
    total_tax = tax_info["total_tax"]
    net_payable = max(total_tax - tds_already_deducted, 0)

    # Advance tax is required only if liability > ₹10,000
    if net_payable <= 10_000:
        return {
            "estimated_income": estimated_annual_income,
            "total_tax_liability": total_tax,
            "tds_deducted": tds_already_deducted,
            "net_payable": net_payable,
            "advance_tax_required": False,
            "note": "Advance tax not required — liability ≤ ₹10,000",
        }

    # Installment percentages and deadlines
    installments = [
        {"due_date": "15 June 2024",      "cumulative_pct": 15, "installment_no": 1},
        {"due_date": "15 September 2024", "cumulative_pct": 45, "installment_no": 2},
        {"due_date": "15 December 2024",  "cumulative_pct": 75, "installment_no": 3},
        {"due_date": "15 March 2025",     "cumulative_pct": 100, "installment_no": 4},
    ]

    schedule = []
    prev_paid = 0
    for inst in installments:
        cumulative = round(net_payable * inst["cumulative_pct"] / 100, 2)
        this_installment = round(cumulative - prev_paid, 2)
        schedule.append({
            "installment_no": inst["installment_no"],
            "due_date": inst["due_date"],
            "amount_to_pay": this_installment,
            "cumulative_paid": cumulative,
        })
        prev_paid = cumulative

    return {
        "estimated_income": estimated_annual_income,
        "total_tax_liability": total_tax,
        "tds_deducted": tds_already_deducted,
        "net_payable": net_payable,
        "advance_tax_required": True,
        "installment_schedule": schedule,
        "note": "Delay in advance tax attracts 1% per month interest u/s 234B and 234C",
    }


if __name__ == "__main__":
    mcp.run()