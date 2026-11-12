"""
# statements.py - five fiscal years of income statement, balance sheet and cash flow from SEC XBRL facts

- `LINES` is the whole layout: per statement, each row is either **reported** (standard us-gaap concepts tried in
  order, then any concept whose SEC label equals the row's label) or a **formula** over other rows (`{revenue} -
  {cost_of_revenue}`). The same formula text is evaluated here in Python and written into the workbook.
- A value is picked by its real period end date; when a period appears in several 10-Ks the newest filing wins
  (`tools._sec_annual_values`), so restated figures replace old ones.
- Money in $ millions, per-share amounts in $, share counts in millions. Every reported value keeps its concept,
  accession number and filing date, so each cell can name its source.

Used by `pipeline.py`, `analysis.py`, `tieout.py` and `workbook.py`.
"""
from __future__ import annotations

import re

from app.ai import tools

R = "reported"
# statement -> rows of (key, label, kind, tags or formula, unit). Units: "usd" ($M), "per_share" ($), "shares" (M).
LINES = {
    "Income Statement": [
        ("revenue", "Revenue", R, ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                                   "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"], "usd"),
        ("cost_of_revenue", "Cost of revenue", R, ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"], "usd"),
        ("gross_profit", "Gross profit", "formula", "{revenue} - {cost_of_revenue}", "usd"),
        ("gross_profit_reported", "Gross profit as reported", R, ["GrossProfit"], "usd"),
        ("rnd", "Research and development", R, ["ResearchAndDevelopmentExpense",
                                                "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"], "usd"),
        ("sga", "Selling, general and administrative", R, ["SellingGeneralAndAdministrativeExpense"], "usd"),
        ("operating_income", "Operating income", R, ["OperatingIncomeLoss"], "usd"),
        ("interest_expense", "Interest expense", R, ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"], "usd"),
        ("pretax_income", "Income before income taxes", R, [
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"], "usd"),
        ("income_tax", "Income tax expense", R, ["IncomeTaxExpenseBenefit"], "usd"),
        ("net_income", "Net income", R, ["NetIncomeLoss"], "usd"),
        ("eps_diluted", "Diluted EPS", R, ["EarningsPerShareDiluted"], "per_share"),
        ("diluted_shares", "Diluted shares (weighted average)", R, ["WeightedAverageNumberOfDilutedSharesOutstanding"], "shares"),
    ],
    "Balance Sheet": [
        ("cash", "Cash and cash equivalents", R, ["CashAndCashEquivalentsAtCarryingValue",
                                                  "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"], "usd"),
        ("short_term_investments", "Short-term investments", R, [
            "ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"], "usd"),
        ("receivables", "Accounts receivable", R, ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"], "usd"),
        ("inventory", "Inventories", R, ["InventoryNet"], "usd"),
        ("current_assets", "Total current assets", R, ["AssetsCurrent"], "usd"),
        ("ppe", "Property, plant and equipment, net", R, ["PropertyPlantAndEquipmentNet"], "usd"),
        ("goodwill", "Goodwill", R, ["Goodwill"], "usd"),
        ("total_assets", "Total assets", R, ["Assets"], "usd"),
        ("accounts_payable", "Accounts payable", R, ["AccountsPayableCurrent"], "usd"),
        ("short_term_borrowings", "Short-term borrowings and commercial paper", R, ["ShortTermBorrowings", "CommercialPaper"], "usd"),
        ("current_debt", "Current portion of long-term debt", R, ["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"], "usd"),
        ("current_liabilities", "Total current liabilities", R, ["LiabilitiesCurrent"], "usd"),
        ("long_term_debt", "Long-term debt", R, ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"], "usd"),
        ("total_debt", "Total debt", "formula", "{short_term_borrowings} + {current_debt} + {long_term_debt}", "usd"),
        ("total_liabilities", "Total liabilities", R, ["Liabilities"], "usd"),
        ("temporary_equity", "Redeemable (temporary) equity", R, [
            "TemporaryEquityCarryingAmountAttributableToParent", "RedeemableNoncontrollingInterestEquityCarryingAmount"], "usd"),
        ("shareholders_equity", "Shareholders' equity", R, ["StockholdersEquity"], "usd"),
        ("total_equity", "Total equity incl. non-controlling interests", R, [
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "StockholdersEquity"], "usd"),
        ("total_liabilities_and_equity", "Total liabilities and equity", R, ["LiabilitiesAndStockholdersEquity"], "usd"),
    ],
    "Cash Flow": [
        ("cf_net_income", "Net income (incl. non-controlling interests)", R, ["ProfitLoss", "NetIncomeLoss"], "usd"),
        ("dna", "Depreciation and amortization", R, ["DepreciationDepletionAndAmortization",
                                                     "DepreciationAmortizationAndAccretionNet", "DepreciationAndAmortization",
                                                     "DepreciationAmortizationAndOther"], "usd"),
        ("sbc", "Share-based compensation", R, ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"], "usd"),
        ("cfo", "Cash from operating activities", R, ["NetCashProvidedByUsedInOperatingActivities",
                                                      "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"], "usd"),
        ("capex", "Capital expenditure (payments for PP&E)", R, ["PaymentsToAcquirePropertyPlantAndEquipment",
                                                                 "PaymentsToAcquireProductiveAssets"], "usd"),
        ("cfi", "Cash from investing activities", R, ["NetCashProvidedByUsedInInvestingActivities",
                                                      "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations"], "usd"),
        ("cff", "Cash from financing activities", R, ["NetCashProvidedByUsedInFinancingActivities",
                                                      "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations"], "usd"),
        ("fx", "Effect of exchange rates on cash", R, [
            "EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
            "EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsIncludingDisposalGroupAndDiscontinuedOperations",
            "EffectOfExchangeRateOnCashAndCashEquivalents"], "usd"),
        ("net_change", "Net change in cash (sum of the above)", "formula", "{cfo} + {cfi} + {cff} + {fx}", "usd"),
        ("net_change_reported", "Net change in cash as reported", R, [
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect",
            "CashAndCashEquivalentsPeriodIncreaseDecrease"], "usd"),
        ("cash_start", "Cash at start of year (incl. restricted)", "start", [
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "CashAndCashEquivalentsAtCarryingValue"], "usd"),
        ("cash_end", "Cash at end of year (incl. restricted)", R, [
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "CashAndCashEquivalentsAtCarryingValue"], "usd"),
        ("dividends", "Dividends paid", R, ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"], "usd"),
        ("buybacks", "Share repurchases", R, ["PaymentsForRepurchaseOfCommonStock"], "usd"),
        ("fcf", "Free cash flow (operating cash flow - capex)", "formula", "{cfo} - {capex}", "usd"),
    ],
}
# Rows a company leaves out when they are nil (no foreign-currency effect, no short-term debt). Long-term debt is not
# here: a missing total debt must stay missing rather than become zero.
ZERO_IF_MISSING = {"fx", "short_term_borrowings", "current_debt", "temporary_equity"}
_DURATION_SHEETS = {"Income Statement", "Cash Flow"}
_CASH_BALANCES = {"cash_start", "cash_end"}  # balances (one day), although they sit on the cash-flow sheet
_UNITS = {"usd": ("USD", 1e6), "per_share": ("USD/shares", 1.0), "shares": ("shares", 1e6)}
_REF = re.compile(r"\{(\w+)(@prev)?\}")


# Evaluate a formula template for one year column; None if a needed value is missing (or it divides by zero).
def evaluate(template: str, values: dict[str, list], i: int) -> float | None:
    def one(m):
        series = values.get(m.group(1)) or []
        j = i - 1 if m.group(2) else i
        v = series[j] if 0 <= j < len(series) else None
        if v is None and m.group(1) in ZERO_IF_MISSING:
            v = 0.0
        if v is None:
            raise LookupError
        return f"({v!r})"
    try:
        return tools.calc(_REF.sub(one, template))
    except (LookupError, ValueError):
        return None


# Every usable concept for a row: its listed tags first, then any us-gaap concept SEC labels exactly like the row.
def _tags(facts: dict, label: str, tags: list[str]) -> list[str]:
    gaap = facts.get("facts", {}).get("us-gaap", {})
    return tags + [c for c, d in gaap.items() if c not in tags and (d.get("label") or "").lower() == label.lower()]


# Build the three statements over the newest five fiscal years. Returns years, rows per statement, values and sources.
def build(facts: dict) -> dict:
    revenue_tags = LINES["Income Statement"][0][3]
    anchor = tools._sec_annual_values(facts, "year", revenue_tags) or tools._sec_annual_values(facts, "year", ["NetIncomeLoss"])
    if not anchor:
        raise ValueError("SEC has no annual revenue or net income for this company")
    ends = sorted(r["end"] for r in anchor)
    years, prev_end = ends[-5:], (ends[-6] if len(ends) > 5 else None)
    values, sources, rows = {}, {}, {}
    for sheet, lines in LINES.items():
        rows[sheet] = []
        for key, label, kind, spec, unit in lines:
            rows[sheet].append({"key": key, "label": label, "unit": unit, "formula": spec if kind == "formula" else None})
            if kind == "formula":
                continue
            xbrl_unit, scale = _UNITS[unit]
            period = "year" if sheet in _DURATION_SHEETS and key not in _CASH_BALANCES else "day"
            found = {r["end"]: r for r in tools._sec_annual_values(facts, period, _tags(facts, label, spec), xbrl_unit)}
            dates = [prev_end] + years[:-1] if kind == "start" else years
            picked = [found.get(d) if d else None for d in dates]
            values[key] = [r["value"] / scale if r else None for r in picked]
            sources[key] = [{"concept": r["tag"], "accn": r["accn"], "filed": r["filed"], "end": r["end"]} if r else None
                            for r in picked]
    for sheet, lines in LINES.items():  # formulas last: they may use rows of any statement
        for key, _label, kind, spec, _unit in lines:
            if kind == "formula":
                values[key] = [evaluate(spec, values, i) for i in range(len(years))]
    return {"years": years, "rows": rows, "values": values, "sources": sources}
