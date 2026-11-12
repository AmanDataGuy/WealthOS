"""
# tools.py - the extra tools the assistant can use besides your own data

Your own data is answered by `engine.py` (metrics and checked SQL). These tools cover the rest:

| Tool | What it does | Where the numbers come from |
|---|---|---|
| `calc` | Arithmetic on numbers already found ("what is 12% of 4,50,000?") | Python, from a small allow-list of operations |
| `company_facts` | A US company's reported figures (revenue, net income, cash ...) | SEC EDGAR XBRL figures, with filing ids |

They are plain Python functions (no extra server processes). Text from filings is treated as data to quote,
never as instructions to follow.
"""
from __future__ import annotations

import ast
import operator
import os
import re
import threading
import time
from datetime import date
from functools import lru_cache

import httpx

# The only operations `calc` understands. Anything else (names, calls, attributes, imports) is refused.
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"round": round, "abs": abs, "min": min, "max": max}


# Safe arithmetic: "1,23,456 * 0.12" or "round((450000 - 380000) / 380000 * 100, 2)". Raises ValueError otherwise.
def calc(expression: str) -> float:
    # Work out one piece of the expression; anything but numbers and + - * / % ** round/abs/min/max is refused.
    def walk(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            if isinstance(node.op, ast.Pow) and (abs(walk(node.right)) > 100 or abs(walk(node.left)) > 1e18):
                raise ValueError("power too large")
            return _OPS[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](walk(node.operand))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
            return _FUNCS[node.func.id](*(walk(a) for a in node.args))
        raise ValueError("only numbers, + - * / ** %, brackets and round/abs/min/max are allowed")

    # "₹1,23,456" -> "123456". ponytail: commas between digits are digit grouping, so function
    # arguments must be written with ", " (comma + space): round(12.345, 2).
    text = re.sub(r"(?<=\d),(?=\d)", "", expression.replace("₹", ""))
    try:
        return float(walk(ast.parse(text, mode="eval").body))
    except (SyntaxError, ArithmeticError, TypeError) as e:  # 1/0, a result past float range, round(1, 2, 3)
        raise ValueError(f"cannot calculate '{expression}': {e}") from None


# Which SEC figures `company_facts` returns: our name -> (kind of number, XBRL tags tried in order).
# "year" = a full-year amount; "day" = a balance on the last day of the year.
SEC_FIELDS = {
    "total_revenue": ("year", ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                               "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
                               "SalesRevenueGoodsNet", "SalesRevenueServicesNet"]),
    "net_income": ("year", ["NetIncomeLoss", "ProfitLoss"]),
    "operating_income": ("year", ["OperatingIncomeLoss"]),
    "gross_profit": ("year", ["GrossProfit"]),
    "research_and_development": ("year", ["ResearchAndDevelopmentExpense",
                                          "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"]),
    "total_assets": ("day", ["Assets"]),
    "total_liabilities": ("day", ["Liabilities"]),
    "long_term_debt": ("day", ["LongTermDebtNoncurrent", "LongTermDebt", "LongTermDebtAndCapitalLeaseObligations"]),
    "cash_and_equivalents": ("day", ["CashAndCashEquivalentsAtCarryingValue",
                                     "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]),
    "operating_cash_flow": ("year", ["NetCashProvidedByUsedInOperatingActivities",
                                     "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]),
    "capital_expenditure": ("year", ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"]),
}
_ANNUAL_FORMS = {"10-K", "10-K405", "10-KT", "10-K/A", "10-KT/A", "20-F", "40-F"}
_sec_lock = threading.Lock()
_sec_last_call = 0.0


# Download one JSON page from SEC, waiting so we stay well under its 10 requests per second limit.
def _sec_get(url: str) -> dict:
    global _sec_last_call
    with _sec_lock:
        time.sleep(max(0.0, 0.15 - (time.monotonic() - _sec_last_call)))
        _sec_last_call = time.monotonic()
    agent = os.getenv("SEC_USER_AGENT", "WealthOS research@wealthos.app")
    resp = httpx.get(url, headers={"User-Agent": agent}, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    return resp.json()


# Ticker -> (10-digit company id, name) for every company SEC lists. Fetched once per run.
@lru_cache(maxsize=1)
def _sec_companies() -> dict:
    raw = _sec_get("https://www.sec.gov/files/company_tickers.json")
    return {c["ticker"].upper(): (str(c["cik_str"]).zfill(10), c["title"]) for c in raw.values()}


# Yearly values (newest first, last 10) for one figure, e.g. [{"value", "end", "filed", "tag", "accn"}].
# A year is picked by its real end date: the latest 10-K also repeats last year's numbers (same "fy"), and those
# must not pass as this year. If one end date is reported twice, the latest filing wins; the first tag that has
# a value for that year wins over later tags.
def _sec_annual_values(facts: dict, kind: str, tags: list[str]) -> list[dict]:
    best = {}  # period end date -> (tag position, filed, row)
    for position, tag in enumerate(tags):
        for taxonomy in ("us-gaap", "ifrs-full"):
            for e in facts.get("facts", {}).get(taxonomy, {}).get(tag, {}).get("units", {}).get("USD", []):
                if e.get("form") not in _ANNUAL_FORMS or e.get("val") is None or "end" not in e:
                    continue
                start = e.get("start")
                if kind == "year":
                    if not start or not 350 <= (date.fromisoformat(e["end"]) - date.fromisoformat(start)).days + 1 <= 380:
                        continue
                elif start:
                    continue
                row = {"value": float(e["val"]), "end": e["end"], "filed": e.get("filed", ""), "tag": f"{taxonomy}:{tag}",
                       "accn": e.get("accn", "")}
                rank = (-position, row["filed"])
                if e["end"] not in best or rank > best[e["end"]][0]:
                    best[e["end"]] = (rank, row)
    return sorted((row for _, row in best.values()), key=lambda r: r["end"], reverse=True)[:10]


# A US-listed company's annual figures from SEC filings, newest first (USD millions, each with its filing id).
# An unknown ticker or SEC being down gives {"error"} so the assistant can say so; only successes are cached.
def company_facts(ticker: str) -> dict:
    try:
        return _company_facts(ticker.upper())
    except Exception as e:
        return {"ticker": ticker, "error": f"no SEC figures for {ticker}: {e!r}"}


@lru_cache(maxsize=64)  # ponytail: in-process cache; filings change a few times a year. lru_cache never keeps a raise.
def _company_facts(ticker: str) -> dict:
    cik, title = _sec_companies()[ticker.replace(".", "-")]
    facts = _sec_get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
    rows = {}
    for name, (kind, tags) in SEC_FIELDS.items():
        values = _sec_annual_values(facts, kind, tags)
        if values:
            rows[name] = [{"value": round(v["value"] / 1e6, 2), "fiscal_year": int(v["end"][:4]), "period_end": v["end"],
                           "filed": v["filed"], "concept": v["tag"], "accn": v["accn"]} for v in values]
    return {"ticker": ticker, "cik": cik, "company_name": facts.get("entityName") or title,
            "unit": "millions_usd", "facts": rows}
