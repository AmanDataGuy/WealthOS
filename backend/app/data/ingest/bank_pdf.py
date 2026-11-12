"""
# bank_pdf.py - read a bank statement PDF (HDFC / ICICI / SBI style) into a transactions table

Most Indian banks email statements as PDFs. This turns the words on each page back into the grid a CSV export
would have, then hands that grid to `tabular.py`, so a PDF statement is recognised, cleaned and checked exactly
like a CSV one.

1. **Detect.** A page among the first two has a line whose cells are a bank-statement header (a date, a
   narration / particulars / description and a debit-credit or amount column, using `tabular.SHAPES`). An annual
   report never has such a line, so it still goes to `filings.py`.
2. **Columns.** The header cells set the column edges; every word below goes to the column it sits under.
3. **Rows.** Lines with no date and no number that sit right under a row are wrapped narration and are joined to
   it. A header repeated on a later page is dropped; so is anything above it (bank name, address).
4. **Clean.** `tabular._read_grid` recognises the shape and cleans dates and amounts; `checks.py` then runs the
   running-balance check on it like on any other statement.

Used by `ingest.parse_file` for password-less PDFs. Scanned (image-only) PDFs have no words and are not read here.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.data.finx.read_pdf import load_pages
from app.data.finx.tables import Line, group_lines

from . import ExtractedTable, SourceRef
from .tabular import SHAPES, _num, _read_grid, match_partial, missing_required

Cell = tuple[float, float, str]  # left edge, right edge, text


# Split a line into cells: words closer than about half a letter height belong to the same cell.
def _cells(line: Line) -> list[Cell]:
    out: list[list] = []
    for w in line.words:
        if out and w.x0 - out[-1][1] < 0.6 * (w.y1 - w.y0):
            out[-1][1], out[-1][2] = w.x1, out[-1][2] + " " + w.text
        else:
            out.append([w.x0, w.x1, w.text])
    return [tuple(c) for c in out]


# True when these cells name every must-have column of a bank statement.
def _is_header(cells: list[Cell]) -> bool:
    spec = SHAPES["transactions"]
    return len(cells) >= 3 and not missing_required(match_partial([c[2] for c in cells], spec), spec)


# Read a PDF bank statement into one transactions table; None when the PDF does not look like one.
def read_statement(path: Path, src: SourceRef) -> list[ExtractedTable] | None:
    header, edges, grid, last = None, [], [], None
    for page in load_pages(str(path)):
        lines = group_lines(page)
        at = next((i for i, ln in enumerate(lines) if _is_header(_cells(ln))), None)
        if header is None:
            if at is None:
                if page.no >= 2:
                    return None  # no statement header on the first two pages
                continue
            header = _cells(lines[at])
            edges = [(a[1] + b[0]) / 2 for a, b in zip(header, header[1:])]  # column borders: halfway between headers
            grid.append([c[2] for c in header])
        if at is not None:
            lines = lines[at + 1:]  # drop the (repeated) header and everything printed above it
        last = None
        for ln in lines:
            row = [""] * len(header)
            for w in ln.words:
                col = sum((w.x0 + w.x1) / 2 > e for e in edges)
                row[col] = (row[col] + " " + w.text).strip()
            wrapped = (last is not None and not row[0] and not any(_num(c) is not None for c in row if c)
                       and ln.top - last.bottom < (last.bottom - last.top))
            if wrapped:
                grid[-1] = [(a + " " + b).strip() for a, b in zip(grid[-1], row)]
            else:
                grid.append(row)
            last = ln
    if header is None:
        return None
    table = _read_grid(pd.DataFrame(grid), src)
    return [table] if table else None
