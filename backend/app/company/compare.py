"""
# compare.py - two to six US-listed companies side by side, from their SEC filings

One row per company: the latest fiscal year's revenue, growth, margins, returns and leverage (the same formulas as
the company workbook, `analysis.RATIOS`), the latest quarter's revenue growth and operating margin (`quarters.py`),
and, when the user types a share price, P/E on the latest fiscal year's diluted EPS. No AI step; every row names the
filing its revenue came from. A company that cannot be modelled (ambiguous name, bank, foreign filer) gets an
`error` instead of a row, so the other companies still compare.

Used by `POST /company/{user_id}/compare`.
"""
from __future__ import annotations

from app.company import analysis, quarters, sec, statements

MAX_COMPANIES = 6
_PCT = {key for key, _label, _formula, fmt in analysis.RATIOS if fmt == "pct"}


# The comparison row of one company.
def row(query: str, price: float | None = None) -> dict:
    company = sec.resolve(query)
    if "candidates" in company:
        names = ", ".join(c["ticker"] for c in company["candidates"][:5]) or "none found"
        raise ValueError(f"'{query}' is not one company (candidates: {names}); type the ticker")
    sec.filings(company["cik"])  # refuses banks, insurers, REITs and companies without a 10-K
    facts = sec.company_facts(company["cik"])
    st = statements.build(facts)
    values, ratios = st["values"], analysis.ratios(st["values"], len(st["years"]))
    last = (quarters.series(facts, 1) or [{}])[-1]
    eps = values["eps_diluted"][-1]
    out = {"ticker": company["ticker"], "name": company["name"], "fiscal_year_end": st["years"][-1],
           "revenue_musd": values["revenue"][-1], "net_income_musd": values["net_income"][-1], "eps_diluted": eps,
           "source": st["sources"]["revenue"][-1],
           "latest_quarter_end": last.get("end"), "quarter_revenue_yoy_pct": last.get("revenue_yoy_pct"),
           "quarter_operating_margin_pct": last.get("operating_margin_pct"),
           "price": price, "pe": round(price / eps, 2) if price and eps and eps > 0 else None}
    for key, series in ratios.items():
        v = series[-1]
        out[key] = None if v is None else round(100 * v, 2) if key in _PCT else round(v, 2)
    return out


# Every company's row, in the order asked; failures become {"ticker", "error"} rows.
def compare(queries: list[str], prices: dict[str, float] | None = None) -> dict:
    prices = {k.upper(): v for k, v in (prices or {}).items()}
    rows = []
    for q in queries[:MAX_COMPANIES]:
        try:
            rows.append(row(q, prices.get(q.strip().upper())))
        except (ValueError, LookupError, KeyError) as e:
            rows.append({"ticker": q.strip().upper(), "error": str(e)})
    pct = [k for k, *_ in analysis.RATIOS if k in _PCT]
    return {"rows": rows, "percent_columns": pct + ["quarter_revenue_yoy_pct", "quarter_operating_margin_pct"],
            "note": "Latest fiscal year from each company's 10-K; fiscal years end on different dates. P/E uses the "
                    "price you typed and the latest fiscal year's diluted EPS."}
