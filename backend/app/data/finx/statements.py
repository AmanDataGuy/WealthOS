"""
# statements.py - find the financial statements

**find_statements**: among all tables, pick the income statement, comprehensive income, balance sheet,
   cash flow and equity statement. Each table gets points for a matching title ("CONSOLIDATED BALANCE SHEETS"),
   typical rows ("Total assets"), size, and being in Item 8; the best table above a threshold wins.
   A statement that continues onto the next page is joined into one.
"""
from __future__ import annotations

import re

from .tables import Table

OWNERS = r"(stockholders|shareholders|share ?owners|shareowners)['’]?"
TITLES = {
    "income_statement": re.compile(
        r"\b(income statements?|statements? of (consolidated )?(operations|income|earnings)|statements? of (consolidated )?(net )?(income|earnings|operations) and comprehensive)\b", re.I),
    "comprehensive_income": re.compile(r"\bcomprehensive (income|loss|earnings)\b", re.I),
    "balance_sheet": re.compile(r"\b(balance sheets?|statements? of (consolidated )?financial (position|condition)|statement of financial position)\b", re.I),
    "cash_flow": re.compile(r"\b(cash flows? statements?|statements? of (consolidated )?cash flows?)\b", re.I),
    "equity": re.compile(rf"\b(statements? of (consolidated )?(changes in )?{OWNERS} equity|(stockholders|shareholders)['’]? equity statements?|statements? of (consolidated )?changes in equity|statements? of equity)\b", re.I),
}
SIGNALS = {  # rows that typically appear in each statement
    "income_statement": [r"^(total )?(net )?(revenues?|sales)\b", r"^net (income|earnings|loss)\b", r"(income|earnings|loss) before (provision for )?income taxes", r"per share|^basic|^diluted"],
    "comprehensive_income": [r"comprehensive (income|loss)", r"foreign currency translation|other comprehensive"],
    "balance_sheet": [r"^total assets$", r"^total (current )?liabilities\b", r"total (stockholders|shareholders|share ?owners)['’]? equity|^total equity", r"^cash and cash equivalents"],
    "cash_flow": [r"operating activities", r"investing activities", r"financing activities", r"(end|beginning) of (period|year)"],
    "equity": [r"^balances?,? (at |as of )?(beginning|end|[a-z]+ \d)|^balance at|^balances? as of", r"dividends", r"repurchase|common stock"],
}
LAST_ROW = {  # the row that ends a statement; without it the statement continues on the next page
    "balance_sheet": re.compile(r"^total liabilities,? (and|&) (stockholders|shareholders|share ?owners|equity|members|partners)", re.I),
    "cash_flow": re.compile(r"(end|close) of (the )?(period|year)", re.I),
    "income_statement": re.compile(r"per share|^diluted", re.I),
}
CORE = ("income_statement", "balance_sheet", "cash_flow")


# Title, column headings and headings just above the table.
def _context_text(t: Table, page_headings: list[dict]) -> str:
    near = [h["text"] for h in page_headings if h["y"] <= t.top + 2 and t.top - h["y"] < 260]
    return " ".join([t.title] + t.header_lines + near[-4:])


# How strongly a table looks like a given statement.
def score(t: Table, kind: str, page_headings: list[dict], section: str | None) -> float:
    ctx = _context_text(t, page_headings)
    s = 4.0 if TITLES[kind].search(ctx) else 0.0
    # "statements of operations and comprehensive income" is an income statement, not a separate one
    if kind == "comprehensive_income" and TITLES["income_statement"].search(ctx) and not re.search(r"statements? of comprehensive", ctx, re.I):
        s -= 3
    labels = [r.label.lower() for r in t.rows]
    s += 1.5 * sum(1 for pat in SIGNALS[kind] if any(re.search(pat, l, re.I) for l in labels))
    s += 1 if len(t.rows) >= 8 else 0
    s += 1 if section in ("Item 8", "Part I Item 1") else -1.5 if section in ("Item 7", "Part I Item 2", "Item 6") else 0
    return s


# Pick the best table for each statement; join a statement that runs onto the next page(s).
def find_statements(tables: list[Table], pages_meta: dict[int, dict]) -> dict[str, list[Table]]:
    found: dict[str, list[Table]] = {}
    for kind in (*CORE, "comprehensive_income", "equity"):
        if kind == "comprehensive_income" and "income_statement" in found:
            inc = found["income_statement"][0]
            if re.search(r"and comprehensive (income|loss)", _context_text(inc, pages_meta.get(inc.page, {}).get("headings", [])), re.I):
                found[kind] = found["income_statement"]  # one combined statement serves as both
                continue
        cluster = [g[0].page for k, g in found.items() if k in CORE]
        best, best_s = None, 0.0
        for t in tables:
            meta = pages_meta.get(t.page, {})
            if meta.get("is_toc"):
                continue
            s = score(t, kind, meta.get("headings", []), meta.get("section"))
            if kind not in CORE and cluster:  # the main statements sit together; a similar table in the notes is not one
                lo, hi = min(cluster), max(cluster)
                s += 2 if lo - 1 <= t.page <= hi + 3 else (-2 if t.page > hi + 6 or t.page < lo - 6 else 0)
            if s > best_s:
                best, best_s = t, s
        if best is None or best_s < 6:
            continue
        group, end, idx = [best], LAST_ROW.get(kind), tables.index(best)
        while end and not any(end.search(r.label) for g in group for r in g.rows) and idx + 1 < len(tables) and len(group) < 3:
            nxt = tables[idx + 1]
            other = any(TITLES[k].search(_context_text(nxt, pages_meta.get(nxt.page, {}).get("headings", []))) for k in TITLES if k != kind)
            if nxt.page not in (group[-1].page, group[-1].page + 1) or other or len(nxt.columns) != len(best.columns):
                break
            group.append(nxt)
            idx += 1
        for g in group:
            g.kind = g.kind or kind
        found[kind] = group
    return found


# Join the pieces of a multi-page statement into one table.
def merge(group: list[Table]) -> Table:
    if len(group) == 1:
        return group[0]
    head = group[0]
    return Table(head.page, head.top, group[-1].bottom, head.title, head.unit or next((g.unit for g in group if g.unit), None),
                 head.scale or next((g.scale for g in group if g.scale), None), head.columns, [r for g in group for r in g.rows],
                 head.header_lines, [g.page for g in group], head.kind, [f for g in group for f in g.flags])
