"""
# pipeline.py - PDF in, statements and page index out

`run(path)` does everything for one PDF, with no AI:
1. read the words, group them into rows and tables, build the page index,
2. find the financial statements and check that their numbers add up,
3. mark every row "verified" (proved by arithmetic) or "unverified", and collect warnings.
"""
from __future__ import annotations

import re
from pathlib import Path

from .check_numbers import verify
from .page_index import build_index
from .read_pdf import load_pages
from .statements import find_statements, merge
from .tables import group_lines, page_tables, split_row, strip_furniture

NO_TEXT_CHARS = 25  # a page with less text than this (and no table) is probably a scan


# True when the text looks like nonsense (broken font encoding): mostly one-letter words or words without vowels.
def _looks_garbled(pages: list[dict]) -> bool:
    words = re.findall(r"[A-Za-z]+", " ".join(p["text"] for p in pages))
    if sum(map(len, words)) < 200:
        return False
    longer = [w for w in words if len(w) >= 3]
    no_vowel = sum(1 for w in longer if not re.search(r"[aeiouyAEIOUY]", w)) / max(len(longer), 1)
    return sum(1 for w in words if len(w) == 1) / len(words) > 0.5 or no_vowel > 0.35


# Read one PDF into a dict: page index, statements (each row marked verified or not) and the arithmetic checks.
def extract(path: str) -> dict:
    pages = load_pages(path)
    pages_lines, pages_kinds, all_tables = [], [], []
    for pg in pages:
        lines = group_lines(pg)
        rows = [split_row(l) for l in lines]
        pages_lines.append(lines)
        pages_kinds.append([r.kind for r in rows])
        all_tables.extend(page_tables(strip_furniture(rows, pg.width, pg.height)))
    index = build_index(pages_lines, pages_kinds, [p.width for p in pages], [p.height for p in pages])
    statements = {k: merge(g) for k, g in find_statements(all_tables, {p["page"]: p for p in index["pages"]}).items()}
    ver = verify(statements)

    warnings = []
    table_pages = {t.page for t in all_tables}
    textless = [p["page"] for p in index["pages"] if len(p["text"].strip()) < NO_TEXT_CHARS and p["page"] not in table_pages]
    if textless:
        warnings.append(f"{len(textless)} pages have no text layer (scans) and were not read")
    if _looks_garbled(index["pages"]):
        warnings.append("the text looks garbled (broken font encoding): do not trust the extracted text or numbers")
    if not pages:
        warnings.append("the PDF has no pages")
    stmts_json = {}
    for kind, t in statements.items():
        d = t.to_dict()
        for row, ok in zip(d["rows"], ver["masks"][kind]):
            row["verified"] = ok
        same = next((j for j, u in statements.items() if u is t and j != kind and j in stmts_json), None)
        if same:  # one "operations and comprehensive income" statement listed under two names: counted once
            d["same_table_as"] = same
        stmts_json[kind] = d
    checks = [{k: v for k, v in c.items() if k not in ("refs", "rows")} for c in ver["checks"]]
    return {"source_file": Path(path).name, "page_count": len(pages), "doc_type": index["doc_type"],
            "pages": index["pages"], "statements": stmts_json, "checks": checks, "warnings": warnings}


# Give every row a status and count the values that are proved (the equity grid is never checked).
def _add_status(statements: dict) -> dict:
    counts = {"verified": 0, "unverified": 0}
    for kind, st in statements.items():
        for r in st["rows"]:
            proved = all(ok for v, ok in zip(r["values"], r["verified"]) if v is not None)
            r["status"] = "verified" if proved and any(v is not None for v in r["values"]) else "unverified"
            if kind != "equity" and "same_table_as" not in st:
                for v, ok in zip(r["values"], r["verified"]):
                    if v is not None:
                        counts["verified" if ok else "unverified"] += 1
    total = sum(counts.values())
    return {**counts, "total": total, "certified_pct": round(100 * counts["verified"] / total, 1) if total else None}


# The whole pipeline for one PDF: extract, add row statuses and the assurance counts, and list warnings.
def run(path: str) -> dict:
    res = extract(path)
    warnings = res["warnings"]
    res["assurance"] = _add_status(res["statements"])
    if res["doc_type"] in ("10-K", "10-Q") and len(res["statements"]) < 3:
        warnings.append(f"{res['doc_type']} but only {len(res['statements'])} primary statements were found")
    failed = [x for x in res["checks"] if not x["passed"]]
    if failed:
        warnings.append(f"{len(failed)} arithmetic checks failed: " + "; ".join(f"{x['check']} (col {x['column']})" for x in failed[:5]))
    return res
