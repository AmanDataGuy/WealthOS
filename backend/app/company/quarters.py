"""
# quarters.py - a company's recent fiscal quarters from SEC XBRL facts

SEC files the 3-month figure of a quarter and the 6- or 9-month year-to-date figure under the **same concept**, so a
quarter is picked by the length of its period (80-100 days), never by the `frame` tag. Q4 has no 10-Q: its amounts are
derived as full year (10-K) minus Q1, Q2 and Q3, and listed in the quarter's `derived`. Q4 diluted EPS is left blank,
because EPS does not subtract (each quarter has its own share count).

- `series(facts)`: the last 8 quarters, oldest first, each with revenue, costs, profits ($M), diluted EPS ($),
  margins, year-on-year growth, inventory and receivable days, and the filing it came from.
- `ytd_check(facts)`: the eval. Q1 + Q2 + Q3 (each from its own 3-month fact) must equal the 9-month figure the Q3
  10-Q reports separately; returns how many fiscal years were tested and passed.

Used by `compare.py`, `review.py`, `tieout.py` and `evals/run_company.py`.
"""
from __future__ import annotations

from datetime import date

from app.company.statements import LINES, _tags

QUARTER_DAYS, NINE_MONTH_DAYS, YEAR_DAYS = (80, 100), (260, 285), (350, 380)
_FORMS = {"10-Q", "10-Q/A", "10-K", "10-K/A", "10-KT"}
_LINE = {key: (label, tags, unit) for rows in LINES.values() for key, label, kind, tags, unit in rows if kind != "formula"}
AMOUNTS = ["revenue", "cost_of_revenue", "gross_profit_reported", "operating_income", "net_income"]
BALANCES = ["inventory", "receivables"]
_UNIT = {"usd": ("USD", 1e6), "per_share": ("USD/shares", 1.0)}


def _days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days + 1


# One figure per period end for a statement line: facts whose period length is in `span` (None = a balance on one
# day). The line's first listed tag wins over later ones; among the same tag, the newest filing wins (restatements).
def _pick(facts: dict, key: str, span: tuple[int, int] | None) -> dict[str, dict]:
    label, tags, unit = _LINE[key]
    xbrl_unit, scale = _UNIT[unit]
    best = {}
    for position, tag in enumerate(_tags(facts, label, tags)):
        for e in facts.get("facts", {}).get("us-gaap", {}).get(tag, {}).get("units", {}).get(xbrl_unit, []):
            if e.get("form") not in _FORMS or e.get("val") is None or "end" not in e:
                continue
            start = e.get("start")
            if (span is None) != (start is None) or (span and not span[0] <= _days(start, e["end"]) <= span[1]):
                continue
            row = {"value": float(e["val"]) / scale, "start": start, "end": e["end"], "filed": e.get("filed", ""),
                   "accn": e.get("accn", ""), "form": e["form"], "tag": tag}
            rank = (-position, row["filed"])
            if e["end"] not in best or rank > best[e["end"]][0]:
                best[e["end"]] = (rank, row)
    return {end: row for end, (_, row) in best.items()}


# The three reported quarters inside one fiscal year (start..end), or None when any is missing.
def _quarters_in(q: dict[str, dict], start: str, end: str) -> list[dict] | None:
    inside = sorted((r for r in q.values() if start <= r["start"] and r["end"] < end), key=lambda r: r["end"])
    return inside if len(inside) == 3 else None


# Q4 amounts as full year minus the three reported quarters, for every fiscal year that has all four figures.
def _with_q4(facts: dict, key: str) -> dict[str, dict]:
    q = _pick(facts, key, QUARTER_DAYS)
    for fy in _pick(facts, key, YEAR_DAYS).values():
        three = _quarters_in(q, fy["start"], fy["end"])
        if fy["end"] not in q and three:
            q[fy["end"]] = {**fy, "value": fy["value"] - sum(r["value"] for r in three), "start": three[-1]["end"],
                            "derived": True}
    return q


def _pct(a, b):
    return None if a is None or not b else round(100 * a / b, 2)


# The last `n` quarters, oldest first, with margins, year-on-year growth and working-capital days.
def series(facts: dict, n: int = 8) -> list[dict]:
    lines = {key: _with_q4(facts, key) for key in AMOUNTS}
    lines["eps_diluted"] = _pick(facts, "eps_diluted", QUARTER_DAYS)
    balances = {key: _pick(facts, key, None) for key in BALANCES}
    ends = sorted(lines["revenue"])
    out = []
    for end in ends:
        get = lambda key: (lines[key].get(end) or {}).get("value")  # noqa: E731
        rev, cost = get("revenue"), get("cost_of_revenue")
        gross = rev - cost if rev is not None and cost is not None else get("gross_profit_reported")
        src = lines["revenue"][end]
        year_ago = next((e for e in ends if 350 <= _days(e, end) <= 380), None)
        inv, rec = ((balances[k].get(end) or {}).get("value") for k in BALANCES)
        out.append({
            "end": end, "filed": src["filed"], "accn": src["accn"], "form": src["form"],
            "derived": [k for k in AMOUNTS if (lines[k].get(end) or {}).get("derived")],
            "revenue": rev, "gross_profit": gross, "operating_income": get("operating_income"),
            "net_income": get("net_income"), "eps_diluted": get("eps_diluted"),
            "gross_margin_pct": _pct(gross, rev), "operating_margin_pct": _pct(get("operating_income"), rev),
            "net_margin_pct": _pct(get("net_income"), rev),
            "revenue_yoy_pct": _pct(rev - lines["revenue"][year_ago]["value"], lines["revenue"][year_ago]["value"])
            if year_ago and rev is not None else None,
            "inventory_days": round(inv / cost * 91, 1) if inv is not None and cost else None,
            "receivable_days": round(rec / rev * 91, 1) if rec is not None and rev else None,
        })
    return out[-n:]


# The eval: does Q1 + Q2 + Q3 (3-month facts) equal the separately reported 9-month figure? Per line, the fiscal
# years tested and passed (within 0.5% or $1M) and the largest difference in $M.
def ytd_check(facts: dict, keys: tuple[str, ...] = ("revenue", "net_income")) -> dict:
    out = {}
    for key in keys:
        q = _pick(facts, key, QUARTER_DAYS)
        tested = passed = 0
        worst = 0.0
        for ytd in _pick(facts, key, NINE_MONTH_DAYS).values():
            inside = [r for r in q.values() if ytd["start"] <= r["start"] and r["end"] <= ytd["end"]]
            if len(inside) != 3:
                continue
            diff = abs(sum(r["value"] for r in inside) - ytd["value"])
            tested += 1
            passed += diff <= max(1.0, 0.005 * abs(ytd["value"]))
            worst = max(worst, diff)
        out[key] = {"tested": tested, "passed": passed, "max_diff_musd": round(worst, 3)}
    return out
