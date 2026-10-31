"""
# check_numbers.py - prove the extracted numbers add up

A number is marked **verified** only if an arithmetic check it takes part in passes:
- **subtotals**: every "Total ..." / result row equals the rows directly above it
  (e.g. Operating income = Gross profit - expenses; Total assets = Total current assets + the other assets),
- **balance sheet**: total assets = total liabilities and equity (= liabilities + equity),
- **cash flow**: operating + investing + financing + FX = net change; beginning cash + change = ending cash,
- **across statements**: net income on the income statement = net income on the cash flow (same period).

Printed numbers are rounded, so small differences are allowed - half of the smallest printed step per number,
at most 3 steps (1 step when the rows do not start at a section boundary). A "Net ..." row that adds up to nothing
is treated as an ordinary line, not a failed total; an ordinary row that equals the sum of the rows above is verified too.
Equity statements are grids (components x movements) and are not checked.
"""
from __future__ import annotations

import re

from .tables import Table

# Rows each check needs, found by their label (first or last match, see concept_rows).
CONCEPTS = {
    "income_statement": {"net_income": r"^net (income|earnings|loss|income \(loss\)|\(loss\) income)( attributable to [a-z .,&]+)?$"},
    "balance_sheet": {
        "total_assets": r"^total assets$",
        "total_liabilities": r"^total liabilities$",
        "total_equity": r"^total (stockholders|shareholders|share ?owners)['’]? equity$|^total equity$",
        "total_le": r"^total liabilities,? (and|&) (stockholders|shareholders|share ?owners|equity)",
    },
    "cash_flow": {
        # section totals start "Net cash ..." / "Cash provided by ..." (not "Cash paid for operating leases")
        "operating": r"^(net cash|cash (provided|used|generated|flows?|from))\b.*\boperat(ing|ions)\b",
        "investing": r"^(net cash|cash (provided|used|generated|flows?|from))\b.*\binvesting\b",
        "financing": r"^(net cash|cash (provided|used|generated|flows?|from))\b.*\bfinancing\b",
        "fx": r"(effect|impact) of (foreign )?(currency )?exchange rate|foreign currency effect|effect of currency",
        "net_change": r"^net (increase|decrease|change|\(decrease\) increase|increase \(decrease\)|decrease \(increase\)).*\bcash\b|^(increase|decrease|change) in cash",
        "begin": r"\bcash\b.*\bbeginning of (the )?(period|year)",
        "net_income": r"^(consolidated )?net (income|earnings|loss|income \(loss\)|\(loss\) income)$",
    },
}
# Labels of rows that are totals or results of the rows above them.
RESULT = re.compile(
    r"^((total )?(current|non-?current|long-term|noncurrent) (assets|liabilities)$|"
    r"total|subtotal|net\b|gross (profit|margin)|operating (income|loss|profit)|income from operations|"
    r"(income|earnings|loss|\(loss\) income|income \(loss\)|profit) (before|from continuing)|(other )?comprehensive (income|loss)|"
    r"(income|earnings) \(loss\)$|cash (provided by|from|used in)|ebitda)"
)
MINUS = re.compile(r"\b(cost|costs|expense|expenses|provision|tax|taxes|less:|charges|impairment)\b")  # rows usually subtracted


# Label in lower case with plain quotes and single spaces, for matching.
def _norm(label: str) -> str:
    return re.sub(r"\s+", " ", label.lower().replace("’", "'").replace("‘", "'")).strip(" :")


# Row number of each concept in a statement (section totals: the last match, after "continuing operations" parts).
def concept_rows(t: Table, kind: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for name, pat in CONCEPTS.get(kind, {}).items():
        hits = [i for i, r in enumerate(t.rows) if re.search(pat, _norm(r.label))]
        if hits and name in ("operating", "investing", "financing"):
            out[name] = ([i for i in hits if not re.search(r"continuing|discontinued", _norm(t.rows[i].label))] or hits)[-1]
        elif hits:
            out[name] = hits[0]
    return out


# The smallest step the printed numbers can show: 1 for whole numbers, 0.1 for one decimal, ...
def _ulp(table: Table, refs: list[tuple[int, int]]) -> float:
    d = 0
    for i, col in refs:
        r = table.rows[i]
        if col < len(r.raw):
            m = re.search(r"\.(\d+)", r.raw[col] or "")
            d = max(d, len(m.group(1)) if m else 0)
    return 10.0 ** -d


# True if a and b differ by no more than honest rounding: half a unit per printed number, capped (3 units by default).
def _close(a: float, b: float, n_terms: int, ulp: float = 1.0, cap: float = 3.0) -> bool:
    return abs(a - b) <= min(0.5 * (n_terms + 1), cap) * ulp + 1e-9


# Sign conventions a printed total may follow: plain sum, first minus the rest, or expense rows subtracted.
def _sign_patterns(run: list[tuple[int, float]], labels: list[str]):
    yield "sum", [1] * len(run)
    yield "first_minus_rest", [1] + [-1] * (len(run) - 1)
    signs = [-1 if MINUS.search(labels[j]) else 1 for j, _ in run]
    if any(s < 0 for s in signs):
        yield "by_label", signs


# Subtotal check: every total/result row must equal a run of the still-open rows directly above it.
# A matched run "closes" into its total (so Total assets = Total current assets + other assets), and a total must use the
# same sign convention in every period column - a column that needs a different one is what a flipped sign looks like.
def subtotal_checks(t: Table) -> list[dict]:
    results = []
    labels = [_norm(r.label) for r in t.rows]
    for col in range(len(t.columns)):
        items: list[tuple[int, float]] = []  # rows still open: (row number, value)
        for i, r in enumerate(t.rows):
            v = r.values[col] if col < len(r.values) else None
            if v is None:
                continue
            is_result = bool(RESULT.search(labels[i]))
            if items:
                hit = None
                for k in range(2, len(items) + 1):
                    run = items[-k:]
                    if all(x == 0 for _, x in run):
                        continue
                    # a run starting at a natural boundary gets the normal rounding allowance; any other start only a
                    # 1-unit allowance and at most 10 rows, because arbitrary starts invite coincidental matches
                    j0 = run[0][0]
                    aligned = (k == len(items) or j0 == 0 or t.rows[j0].section != t.rows[j0 - 1].section
                               or bool(RESULT.search(labels[j0 - 1])) or bool(RESULT.search(labels[j0])))
                    if (not aligned and k > 10) or (not is_result and k > 6):
                        continue
                    ulp = _ulp(t, [(j, col) for j, _ in run] + [(i, col)])
                    for name, signs in _sign_patterns(run, labels):
                        if (is_result or name == "sum") and _close(sum(s * x for s, (_, x) in zip(signs, run)), v, k, ulp, 3.0 if aligned else 1.0):
                            hit = (run, name)
                            break
                    if hit:
                        break
                if hit is None and is_result and labels[i].startswith(("total", "subtotal")):
                    # a total of a single line ("Total equity" over one "Retained earnings" row) equals that line
                    if _close(items[-1][1], v, 0, _ulp(t, [(items[-1][0], col), (i, col)]), 1.0):
                        hit = ([items[-1]], "single_component")
                # a "Net ..." row that sums nothing is usually a plain line ("Net sales"), not a failed total
                if hit or labels[i].startswith(("total", "subtotal")):
                    results.append({"check": "subtotal", "column": col, "row": i, "label": r.label, "passed": hit is not None,
                                    "pattern": hit[1] if hit else None, "rows": [j for j, _ in hit[0]] + [i] if hit else [i]})
                if hit and is_result:  # only totals close their rows; a plain row that equals a sum must not hide them
                    del items[-len(hit[0]):]
            items.append((i, v))
    by_row: dict[int, list[dict]] = {}
    for r in results:
        by_row.setdefault(r["row"], []).append(r)
    for rs in by_row.values():  # the same total must follow one sign convention in every column
        if len({r["pattern"] for r in rs if r["passed"]}) > 1:
            for r in rs:
                if r["passed"]:
                    r["passed"], r["pattern"] = False, "inconsistent_sign_convention"
    return results


# Accounting identities: balance sheet balances, cash rolls forward, net income ties between statements.
def core_checks(st: dict[str, Table]) -> list[dict]:
    res = []
    bs, cf, inc = st.get("balance_sheet"), st.get("cash_flow"), st.get("income_statement")

    # Value of row i in column col, or None.
    def val(t, i, col):
        return t.rows[i].values[col] if t and i is not None and col < len(t.rows[i].values) else None

    # Record a check that compares a with b.
    def add(name, a, b, col, refs, n=2):
        if a is None or b is None:
            return
        ulp = 1.0
        for ref in refs:
            kind, i = ref[0], ref[1]
            if i is not None and kind in st:
                ulp = min(ulp, _ulp(st[kind], [(i, ref[2] if len(ref) > 2 else col)]))
        res.append({"check": name, "column": col, "passed": _close(a, b, n, ulp), "lhs": a, "rhs": b, "refs": refs})

    if bs:
        c = concept_rows(bs, "balance_sheet")
        for col in range(len(bs.columns)):
            ta, tle = val(bs, c.get("total_assets"), col), val(bs, c.get("total_le"), col)
            tl, te = val(bs, c.get("total_liabilities"), col), val(bs, c.get("total_equity"), col)
            add("assets_eq_liabilities_and_equity", ta, tle, col, [("balance_sheet", c.get("total_assets")), ("balance_sheet", c.get("total_le"))])
            if tl is not None and te is not None and tle is not None:
                add("liabilities_plus_equity_eq_total", tl + te, tle, col,
                    [("balance_sheet", c.get("total_liabilities")), ("balance_sheet", c.get("total_equity")), ("balance_sheet", c.get("total_le"))])
    if cf:
        c = concept_rows(cf, "cash_flow")
        cash_rows = [i for i, r in enumerate(cf.rows) if re.search(r"\bcash\b", _norm(r.label)) and not re.search(r"paid|supplemental|interest|taxes", _norm(r.label))]
        chg_rows = [i for i in cash_rows if re.search(r"^(net )?(\(?(increase|decrease)\)? ?)+(\(?(increase|decrease)\)? ?)*(\bin\b|\bof\b)?.*\bcash\b|^(net )?change in cash", _norm(cf.rows[i].label))]
        for col in range(len(cf.columns)):
            v = lambda i: val(cf, i, col)  # noqa: E731
            op, inv, fin = v(c.get("operating")), v(c.get("investing")), v(c.get("financing"))
            if None in (op, inv, fin):
                continue
            base = op + inv + fin + (v(c.get("fx")) or 0.0)
            core_refs = [("cash_flow", c.get(k)) for k in ("operating", "investing", "financing", "fx") if c.get(k) is not None]
            # several "net change" rows can exist (before / after cash held for sale): any one that matches
            cands = [i for i in chg_rows if v(i) is not None] or ([c["net_change"]] if "net_change" in c else [])
            hit = next((i for i in cands if _close(base, v(i), 4, _ulp(cf, [(i, col)]))), None)
            chg_i = hit if hit is not None else (cands[0] if cands else None)
            if chg_i is None:
                continue
            add("cash_rollforward", base, v(chg_i), col, core_refs + [("cash_flow", chg_i)], 4)
            beg_i = c.get("begin")
            if beg_i is None or v(beg_i) is None:
                continue
            # simplest case first: beginning cash + any "net change" row = some later cash row
            direct = next(((ci, ei) for ci in cands for ei in cash_rows if ei > beg_i and ei != ci and v(ei) is not None
                           and _close(v(beg_i) + v(ci), v(ei), 2, _ulp(cf, [(beg_i, col), (ci, col), (ei, col)]))), None)
            if direct:
                add("cash_begin_plus_change_eq_end", v(beg_i) + v(direct[0]), v(direct[1]), col,
                    [("cash_flow", beg_i), ("cash_flow", direct[0]), ("cash_flow", direct[1])])
                continue
            # otherwise allow adjustment rows in between (e.g. "Less: cash held for sale")
            lo = min(beg_i, chg_i)
            tail = [i for i in cash_rows if i > max(beg_i, chg_i)] or [i for i in cash_rows if i > lo]
            best = None
            for end_i in [i for i in tail if v(i) is not None]:
                adj = sum((-abs(v(i)) if _norm(cf.rows[i].label).startswith("less") else v(i))
                          for i in range(lo + 1, end_i) if i not in (beg_i, chg_i) and v(i) is not None and i in cash_rows)
                a = next((a for a in (0.0, adj, -adj) if _close(v(beg_i) + v(chg_i) + a, v(end_i), 3, _ulp(cf, [(beg_i, col), (chg_i, col), (end_i, col)]))), None)
                if a is not None:
                    best = (end_i, a)
                    break
            if best:
                used = [("cash_flow", beg_i), ("cash_flow", chg_i), ("cash_flow", best[0])]
                used += [("cash_flow", i) for i in range(lo + 1, best[0]) if i in cash_rows and i not in (beg_i, chg_i)] if best[1] else []
                add("cash_begin_plus_change_eq_end", v(beg_i) + v(chg_i) + best[1], v(best[0]), col, used)
            elif tail:
                add("cash_begin_plus_change_eq_end", v(beg_i) + v(chg_i), v(tail[-1]), col, [("cash_flow", beg_i), ("cash_flow", chg_i), ("cash_flow", tail[-1])])
    if inc and cf:
        ci, cc = concept_rows(inc, "income_statement"), concept_rows(cf, "cash_flow")
        for col, ccol in _match_periods(inc, cf):  # like periods only: a 10-Q has 3- and 6-month columns
            a, b = val(inc, ci.get("net_income"), col), val(cf, cc.get("net_income"), ccol)
            if a is not None and b is not None:
                res.append({"check": "net_income_ties_to_cash_flow", "column": col, "passed": abs(a - b) <= 1e-9, "lhs": a, "rhs": b,
                            "refs": [("income_statement", ci.get("net_income"), col), ("cash_flow", cc.get("net_income"), ccol)]})
    return res


# Column pairs of two statements that cover the same period (same year and, when both say, same number of months).
def _match_periods(a: Table, b: Table) -> list[tuple[int, int]]:
    pairs = []
    for i, ca in enumerate(a.columns):
        for j, cb in enumerate(b.columns):
            pa, pb = ca.period, cb.period
            if not pa.get("year") or pa.get("year") != pb.get("year") or pa.get("change") or pb.get("change"):
                continue
            if pa.get("months") and pb.get("months") and pa["months"] != pb["months"]:
                continue
            pairs.append((i, j))
            break
    return pairs


# Run all checks; returns the check results and, per statement, which values are verified ([row][column]).
def verify(st: dict[str, Table]) -> dict:
    checks = []
    masks = {k: [[False] * len(t.columns) for _ in t.rows] for k, t in st.items()}
    done: dict[int, str] = {}
    for kind, t in st.items():
        if kind == "equity":
            continue
        if id(t) in done:  # one combined statement listed under two names: check it once
            masks[kind] = masks[done[id(t)]]
            continue
        done[id(t)] = kind
        for r in subtotal_checks(t):
            r["statement"] = kind
            checks.append(r)
            if r["passed"]:
                for i in r["rows"]:
                    masks[kind][i][r["column"]] = True
    for r in core_checks(st):
        checks.append(r)
        if r["passed"]:
            for ref in r["refs"]:
                if ref[1] is not None:
                    masks[ref[0]][ref[1]][ref[2] if len(ref) > 2 else r["column"]] = True
    return {"checks": checks, "masks": masks}
