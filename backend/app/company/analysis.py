"""
# analysis.py - ratios and a small DCF valuation, as pure functions over the statements

- `RATIOS`: growth, margins, returns, liquidity, leverage and cash conversion, each one formula text over statement
  rows. `ratios()` evaluates them in Python; `workbook.py` writes the very same text as Excel formulas. A ratio whose
  rows are missing is left blank.
- `dcf()`: five years of free cash flow growing at a fixed rate, a Gordon-growth terminal value, discounted at one
  flat rate. The assumptions are explicit inputs (`ASSUMPTIONS`), shown in blue on the Valuation sheet so a user can
  change them; there is no market data (no share price, beta or bond yield is fetched).

Used by `pipeline.py`, `workbook.py` and `tieout.py`.
"""
from __future__ import annotations

from app.company.statements import evaluate

# (key, label, formula over statement rows, display: "pct" or "x")
RATIOS = [
    ("revenue_growth", "Revenue growth", "{revenue} / {revenue@prev} - 1", "pct"),
    ("gross_margin", "Gross margin", "{gross_profit} / {revenue}", "pct"),
    ("operating_margin", "Operating margin", "{operating_income} / {revenue}", "pct"),
    ("net_margin", "Net margin", "{net_income} / {revenue}", "pct"),
    ("roe", "Return on equity (year-end equity)", "{net_income} / {total_equity}", "pct"),
    ("roa", "Return on assets (year-end assets)", "{net_income} / {total_assets}", "pct"),
    ("current_ratio", "Current ratio", "{current_assets} / {current_liabilities}", "x"),
    ("debt_to_equity", "Debt to equity", "{total_debt} / {total_equity}", "x"),
    ("interest_cover", "Interest cover (operating income / interest)", "{operating_income} / {interest_expense}", "x"),
    ("cash_conversion", "Cash conversion (operating cash flow / net income)", "{cfo} / {net_income}", "x"),
    ("fcf_margin", "Free cash flow margin", "{fcf} / {revenue}", "pct"),
    ("capex_intensity", "Capex / revenue", "{capex} / {revenue}", "pct"),
]

# House assumptions, in one place. A flat 9% discount rate stands in for a full WACC (no beta or bond yield is
# fetched); growth is the company's own revenue CAGR over the years shown, held between 0% and the cap.
ASSUMPTIONS = {"discount_rate": 0.09, "terminal_growth": 0.03, "growth_cap": 0.15, "years": 5}


# Every ratio for every year column: {key: [value or None per year]}.
def ratios(values: dict[str, list], n_years: int) -> dict[str, list]:
    return {key: [evaluate(formula, values, i) for i in range(n_years)] for key, _label, formula, _fmt in RATIOS}


# The newest value of a row (None if the latest year is missing).
def _latest(values: dict[str, list], key: str):
    series = values.get(key) or [None]
    return series[-1]


# Revenue CAGR from the oldest to the newest year shown, held between 0 and the growth cap.
def growth_assumption(values: dict[str, list]) -> float:
    rev = [v for v in values.get("revenue", []) if v]
    if len(rev) < 2 or rev[0] <= 0 or rev[-1] <= 0:
        return 0.0
    cagr = (rev[-1] / rev[0]) ** (1 / (len(rev) - 1)) - 1
    return min(max(cagr, 0.0), ASSUMPTIONS["growth_cap"])


# The inputs the DCF needs from the latest year, or the reason it cannot run.
def dcf_inputs(values: dict[str, list]) -> dict:
    oi, dna = _latest(values, "operating_income"), _latest(values, "dna")
    inputs = {"base_fcf": _latest(values, "fcf"), "total_debt": _latest(values, "total_debt"), "cash": _latest(values, "cash"),
              "short_term_investments": _latest(values, "short_term_investments") or 0.0,
              "diluted_shares": _latest(values, "diluted_shares"), "revenue": _latest(values, "revenue"),
              "ebitda": oi + dna if oi is not None and dna is not None else None, "eps_diluted": _latest(values, "eps_diluted"), "growth": growth_assumption(values),
              "discount_rate": ASSUMPTIONS["discount_rate"], "terminal_growth": ASSUMPTIONS["terminal_growth"]}
    missing = [k for k in ("base_fcf", "total_debt", "cash", "diluted_shares") if inputs[k] is None]
    if missing:
        inputs["skip"] = f"not enough data for a DCF (missing: {', '.join(missing)} for the latest year)"
    elif inputs["base_fcf"] <= 0:
        inputs["skip"] = "latest free cash flow is negative, so a cash-flow DCF says nothing useful"
    return inputs


# Discounted cash flow from `dcf_inputs`: per-year cash flows, terminal value, enterprise and equity value, value
# per share ($) and the multiples that value implies. Money in $ millions, shares in millions.
def dcf(i: dict) -> dict:
    g, r, tg, n = i["growth"], i["discount_rate"], i["terminal_growth"], ASSUMPTIONS["years"]
    fcf = [i["base_fcf"] * (1 + g) ** t for t in range(1, n + 1)]
    pv = [f / (1 + r) ** t for t, f in enumerate(fcf, start=1)]
    terminal = fcf[-1] * (1 + tg) / (r - tg)
    pv_terminal = terminal / (1 + r) ** n
    ev = sum(pv) + pv_terminal
    net_debt = i["total_debt"] - i["cash"] - i["short_term_investments"]
    equity = ev - net_debt
    per_share = equity / i["diluted_shares"]
    return {"fcf": fcf, "pv": pv, "terminal_value": terminal, "pv_terminal": pv_terminal, "enterprise_value": ev,
            "net_debt": net_debt, "equity_value": equity, "per_share": per_share,
            "ev_to_revenue": ev / i["revenue"] if i["revenue"] else None,
            "ev_to_ebitda": ev / i["ebitda"] if i["ebitda"] else None,
            "pe": per_share / i["eps_diluted"] if i["eps_diluted"] else None}
