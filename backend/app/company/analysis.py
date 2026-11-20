"""
# analysis.py - ratios and a small DCF valuation, as pure functions over the statements

- `RATIOS`: growth, margins, returns, liquidity, leverage and cash conversion, each one formula text over statement
  rows. `ratios()` evaluates them in Python; `workbook.py` writes the very same text as Excel formulas. A ratio whose
  rows are missing is left blank.
- `dcf()`: five years of free cash flow growing at a fixed rate, a Gordon-growth terminal value, discounted at one
  flat rate. The assumptions are explicit inputs (`ASSUMPTIONS`), shown in blue on the Valuation sheet so a user can
  change them; there is no market data (no share price, beta or bond yield is fetched). It also returns a terminal
  value cross-check (implied exit EV / EBITDA, terminal share of EV) and a value-per-share grid over discount rate x
  terminal growth (`RATE_STEPS` x `GROWTH_STEPS`).
- `flags()`: up to five value-trap warnings, fixed rules over the rows and ratios above, each with the numbers that
  set it off. Warnings to look into, never a verdict.

Used by `pipeline.py`, `workbook.py`, `tieout.py` and `compare.py`.
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
# Sensitivity grid: steps added to the discount rate (rows) and to terminal growth (columns).
RATE_STEPS = (-0.02, -0.01, 0.0, 0.01, 0.02)
GROWTH_STEPS = (-0.01, -0.005, 0.0, 0.005, 0.01)

# Value-trap thresholds, compared from the first to the latest year shown.
REVENUE_FALL = -0.05  # revenue down 5% or more: a shrinking business, not one soft year's noise
MARGIN_DROP = -0.03  # operating margin down 3 percentage points or more: pricing power or costs slipping
DEBT_RISE = 0.5  # debt to equity up 0.5x or more: growth or buybacks paid for with borrowing
CASH_BACKING_MIN = 0.8  # operating cash flow under 80% of a positive net income: profit not turning into cash


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
# per share ($), the multiples that value implies, the terminal-value cross-check and the sensitivity grid (skipped
# when `grid` is False). Money in $ millions, shares in millions.
def dcf(i: dict, grid: bool = True) -> dict:
    g, r, tg, n = i["growth"], i["discount_rate"], i["terminal_growth"], ASSUMPTIONS["years"]
    fcf = [i["base_fcf"] * (1 + g) ** t for t in range(1, n + 1)]
    pv = [f / (1 + r) ** t for t, f in enumerate(fcf, start=1)]
    terminal = fcf[-1] * (1 + tg) / (r - tg)
    pv_terminal = terminal / (1 + r) ** n
    ev = sum(pv) + pv_terminal
    net_debt = i["total_debt"] - i["cash"] - i["short_term_investments"]
    equity = ev - net_debt
    per_share = equity / i["diluted_shares"]
    ebitda_n = i["ebitda"] * (1 + g) ** n if i["ebitda"] else None  # latest EBITDA grown like free cash flow
    # A grid cell is None where growth is at or above the rate (1e-9 absorbs float noise such as 0.04 - 0.01): the
    # Gordon formula has no meaning there.
    sens = [[dcf(i | {"discount_rate": r + dr, "terminal_growth": tg + dg}, grid=False)["per_share"]
             if r + dr - (tg + dg) > 1e-9 else None for dg in GROWTH_STEPS] for dr in RATE_STEPS] if grid else None
    return {"fcf": fcf, "pv": pv, "terminal_value": terminal, "pv_terminal": pv_terminal, "enterprise_value": ev,
            "net_debt": net_debt, "equity_value": equity, "per_share": per_share,
            "ev_to_revenue": ev / i["revenue"] if i["revenue"] else None,
            "ev_to_ebitda": ev / i["ebitda"] if i["ebitda"] else None,
            "pe": per_share / i["eps_diluted"] if i["eps_diluted"] else None,
            "exit_ev_to_ebitda": terminal / ebitda_n if ebitda_n else None, "terminal_share": pv_terminal / ev,
            "sensitivity": sens}


# First and latest non-blank value of a series, or None when fewer than two years have one.
def _span(series: list) -> tuple | None:
    known = [v for v in series or [] if v is not None]
    return (known[0], known[-1]) if len(known) >= 2 else None


# Value-trap warnings: [{"flag", "warning"}] for each rule that fires; the warning text carries the triggering numbers.
def flags(values: dict[str, list], ratio: dict[str, list]) -> list[dict]:
    out = []
    rev, ni, fcf = _span(values.get("revenue")), _span(values.get("net_income")), _span(values.get("fcf"))
    margin, de = _span(ratio.get("operating_margin")), _span(ratio.get("debt_to_equity"))
    if rev and rev[0] > 0 and rev[1] / rev[0] - 1 <= REVENUE_FALL:
        out.append(("revenue_falling", f"Revenue fell {1 - rev[1] / rev[0]:.1%} over the years shown "
                    f"(${rev[0]:,.0f}M to ${rev[1]:,.0f}M)."))
    if margin and margin[1] - margin[0] <= MARGIN_DROP:
        out.append(("margin_shrinking", f"Operating margin fell from {margin[0]:.1%} to {margin[1]:.1%} over the years shown."))
    if de and de[1] - de[0] >= DEBT_RISE:
        out.append(("debt_rising", f"Debt to equity rose from {de[0]:.2f}x to {de[1]:.2f}x over the years shown."))
    if fcf and ni and ni[1] > ni[0] and (fcf[1] < 0 or fcf[1] < fcf[0]):
        out.append(("fcf_vs_profit", f"Net income rose (${ni[0]:,.0f}M to ${ni[1]:,.0f}M) while free cash flow "
                    f"{'is negative' if fcf[1] < 0 else 'fell'} (${fcf[0]:,.0f}M to ${fcf[1]:,.0f}M)."))
    cfo, n = (values.get("cfo") or [None])[-1], (values.get("net_income") or [None])[-1]
    if cfo is not None and n and n > 0 and cfo / n < CASH_BACKING_MIN:
        out.append(("profit_not_cash", f"Latest operating cash flow is {cfo / n:.0%} of net income "
                    f"(${cfo:,.0f}M against ${n:,.0f}M)."))
    return [{"flag": k, "warning": w} for k, w in out]
