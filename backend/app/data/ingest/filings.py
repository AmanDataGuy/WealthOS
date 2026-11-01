"""
# filings.py - read financial statements out of a PDF (10-K, 10-Q, annual report, results)

Uses finx (in this repo) - deterministic, no AI. finx finds the income statement, balance sheet, cash flow,
comprehensive income and equity statements and checks that their numbers add up.

Each number becomes one `statements` row:
statement, label ("Revenue"), period (year + months), value as printed, scale (x 1e6 for "in millions",
x 1e7 for "₹ in crore"), status (verified when the arithmetic proves it, otherwise unverified) and the page it was printed on.

`unverified` means "could not be proven by arithmetic" - show it as needing a check, never as fine.
"""
from __future__ import annotations

from pathlib import Path

from . import ExtractedTable, SourceRef


# Read a filing PDF into one statements table (empty, with a warning, when no statements are found).
def read_filing(path: Path, src: SourceRef) -> list[ExtractedTable]:
    from app.data.finx.pipeline import run

    res = run(str(path))
    rows = []
    for kind, st in res["statements"].items():
        if st.get("same_table_as"):
            continue  # one printed table that counts as two statements: keep it once
        for r in st["rows"]:
            for col, value in zip(st["columns"], r["values"]):
                if value is not None:
                    rows.append({"statement": kind, "label": r["label"], "section": r.get("section") or None,
                                 "year": col["period"].get("year"), "months": col["period"].get("months"),
                                 "value": value, "unit": st.get("unit"), "scale": st.get("scale"),
                                 "status": r.get("status"), "page": r.get("page")})
    warnings = list(res["warnings"])
    if not rows:
        warnings.append("no financial statements found. If this is a bank statement, download it as CSV or Excel "
                        "from net banking and upload that instead.")
    return [ExtractedTable(shape="statements", columns=list(rows[0]) if rows else [], rows=rows, source=src,
                           status="parsed", warnings=warnings)]
