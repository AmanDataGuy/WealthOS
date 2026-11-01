"""
# ingest - turn any file a user uploads into clean tables

One door for every file: `parse_file(path, password)`.

| File | What we do | Module |
|---|---|---|
| `.csv` / `.xlsx` | Find the table, recognise it (broker trades, bank transactions, holdings) by its column names | `tabular.py` |
| `.pdf` + password | Try it as a CAS statement (CAMS / KFintech / NSDL / CDSL): mutual funds, shares, transactions | `cas.py` |
| `.pdf` | Read financial statements (10-K, annual report) with finx, each number with a verified/unverified status | `filings.py` |

Every result is an `ExtractedTable`: a shape (what kind of data it is), rows with our standard column names,
where it came from (file, sheet, page, row) and warnings from the sanity checks in `checks.py`.
No AI anywhere in this package: numbers are read, never guessed.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

Shape = Literal["transactions", "trades", "holdings", "prices", "statements", "unknown"]


# Where a table came from, so every number can be traced back to the user's own file.
class SourceRef(BaseModel):
    file: str
    content_hash: str
    page: int | None = None
    sheet: str | None = None


# One clean table: its kind, its rows (each row also keeps its original row / page number), and warnings.
class ExtractedTable(BaseModel):
    shape: Shape
    columns: list[str]
    rows: list[dict]
    source: SourceRef
    status: Literal["parsed", "needs_mapping"]
    warnings: list[str] = []
    profile: dict = {}  # for "unknown" tables: what each column looks like, to help the user map it


# sha256 of the file bytes: the same file always gets the same id (used as its data fingerprint).
def file_hash(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# Read one uploaded file into tables, run the sanity checks on each, and return them.
def parse_file(path: str | Path, password: str | None = None, filename: str | None = None) -> list[ExtractedTable]:
    from . import cas, checks, filings, tabular

    path = Path(path)
    src = SourceRef(file=filename or path.name, content_hash=file_hash(path))
    suffix = path.suffix.lower()
    if suffix in (".csv", ".xlsx"):
        tables = tabular.read_tables(path, src)
    elif suffix == ".pdf":
        tables = (cas.read_cas(path, password, src) if password else None) or filings.read_filing(path, src)
    else:
        raise ValueError(f"Unsupported file type '{suffix}'. Upload .csv, .xlsx or .pdf.")
    for t in tables:
        t.warnings += checks.run(t)
    return tables
