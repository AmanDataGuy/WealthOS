"""
# tieout.py - accounting checks that need no judge: the model is right only if the filings add up

- `CHECKS`: identities, each a difference that must be ~0 in every year ($2M tolerance, for rounding in the
  filings). Written to the Checks sheet as live formulas too.
- `run()`: each check over every year (a year with a missing row is skipped, not failed).
- `workbook_check()`: every formula cell in the saved workbook is re-calculated here from the cells it points
  at and must equal the value Python computed for it (so the sheet formulas and the Python code agree).
- `recalc()`: a small calculator for the workbook's own formulas (cell references, + - * / ^ and brackets);
  LibreOffice is not needed. Cells with IF(...) are display-only (PASS / FAIL labels) and are not re-calculated.

Used by `pipeline.py`, `workbook.py` and `evals/run_company.py`.
"""
from __future__ import annotations

import re

import openpyxl

from app.ai import tools
from app.company.statements import evaluate

TOLERANCE = 2.0  # $ millions
# (key, what it proves, difference that must be ~0)
CHECKS = [
    ("balance_sheet_balances", "Total assets = total liabilities + redeemable equity + total equity",
     "{total_assets} - ({total_liabilities} + {temporary_equity} + {total_equity})"),
    ("reported_totals_agree", "Total assets = total liabilities and equity, both as reported",
     "{total_assets} - {total_liabilities_and_equity}"),
    ("cash_flow_sums", "Operating + investing + financing + FX = reported net change in cash",
     "{net_change} - {net_change_reported}"),
    ("cash_roll_forward", "Cash at end - cash at start = reported net change in cash",
     "{cash_end} - {cash_start} - {net_change_reported}"),
    ("gross_profit_ties", "Revenue - cost of revenue = gross profit as reported",
     "{gross_profit} - {gross_profit_reported}"),
]
_CELL = re.compile(r"(?:'([^']+)'!)?\b([A-Z]{1,2})(\d+)\b")


# Re-calculate one cell of a workbook opened with formulas, following references; blank cells count as 0.
def recalc(wb, sheet: str, coord: str, memo: dict | None = None) -> float:
    memo = {} if memo is None else memo
    if (sheet, coord) in memo:
        return memo[(sheet, coord)]
    v = wb[sheet][coord].value
    if isinstance(v, str) and v.startswith("="):
        if re.search(r"[A-Z]\(", v):  # a function call such as IF(
            raise ValueError(f"{sheet}!{coord} uses a function")
        expr = _CELL.sub(lambda m: f"({recalc(wb, m.group(1) or sheet, m.group(2) + m.group(3), memo)!r})", v[1:])
        v = tools.calc(expr.replace("^", "**"))
    elif v is None:
        v = 0.0
    memo[(sheet, coord)] = float(v)
    return memo[(sheet, coord)]


# Run every accounting check over every year column.
def run(values: dict[str, list], years: list[str]) -> list[dict]:
    out = []
    for key, what, formula in CHECKS:
        deltas = [evaluate(formula, values, i) for i in range(len(years))]
        tested = [d for d in deltas if d is not None]
        failed = [d for d in tested if abs(d) > TOLERANCE]
        out.append({"check": key, "description": what, "status": "skipped" if not tested else "fail" if failed else "pass",
                    "years_tested": len(tested), "years_passed": len(tested) - len(failed),
                    "max_abs_delta": round(max((abs(d) for d in tested), default=0.0), 3),
                    "deltas": dict(zip(years, [None if d is None else round(d, 3) for d in deltas]))})
    return out


# Re-calculate every formula cell of the saved workbook and compare with the Python value recorded for it.
# `expected` is {(sheet, cell): python value}. Counts are cells, not years.
def workbook_check(path, expected: dict) -> dict:
    wb, memo, bad = openpyxl.load_workbook(path), {}, []
    for (sheet, coord), want in expected.items():
        got = recalc(wb, sheet, coord, memo)
        if abs(got - want) > 1e-6 * max(1.0, abs(want)):
            bad.append(f"{sheet}!{coord}: workbook {got:.6g} vs python {want:.6g}")
    return {"check": "workbook_recalculates", "description": "Every formula cell in the workbook equals the Python value",
            "status": "fail" if bad else "pass" if expected else "skipped", "years_tested": len(expected),
            "years_passed": len(expected) - len(bad), "max_abs_delta": 0.0, "deltas": {}, "failures": bad[:10]}
