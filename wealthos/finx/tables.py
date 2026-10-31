"""
# tables.py - turn the words on a page into rows and tables

1. **Lines**: words at the same height form one line.
2. **Rows**: each line is split into a text label on the left and numbers on the right
   ("Total revenue ... 331,839 281,724" -> label "Total revenue", values 331839 and 281724).
   Brackets mean negative, a dash means zero.
3. **Tables**: consecutive rows with numbers form a table. Numbers whose right edges line up form a column.
   The text above the columns gives each column's period ("2026", "Three months ended June 30");
   text such as "(In millions)" gives the unit.

Indian reports work too: Indian digit grouping ("1,23,456"), rupee signs ("₹1,234", "Rs. 1,234", "INR 1,234")
and lakh / crore units ("(₹ in crore)" = x 1,00,00,000, "(Rs. in lakhs)" = x 1,00,000).

Along the way it drops printed page numbers, joins labels that wrap onto a second line and remembers the section
heading above each row ("Current assets:"). Pure geometry and text rules - no AI models.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from .read_pdf import PageData, Word

CURRENCY = {"$", "€", "£", "US$", "¥", "C$", "A$", "₹", "Rs", "Rs.", "INR"}  # currency signs printed on their own
DASHES = {"—", "–", "-", "--", "−", "—%"}  # a dash in a number column means zero
# Digit groups: Western "1,234,567", Indian "12,34,567" (lakh/crore grouping), or no commas.
NUMBER = re.compile(r"^\(?(?:[-−–]?[$€£₹]?|[$€£₹][-−–])\(?(\d{1,3}(,\d{3})+|\d{1,2}(,\d{2})+,\d{3}|\d+)(\.\d+)?\)?%?\)?-?$|^\(?(?:[-−–]?[$€£₹]?|[$€£₹][-−–])\.\d+\)?%?\)?-?$")  # also -1,234 / –1,234 / 1,234- / $-5
RUPEE_PREFIX = re.compile(r"^(Rs\.?|INR)\s*", re.I)  # "Rs. 1,234" / "INR 1,234" glued in one piece
YEAR_CELL = re.compile(r"^(19|20)\d\d$")  # a cell that is just a year, e.g. "2026"
YEAR = re.compile(r"(?<!\d)(19|20)\d\d(?!\d)")  # a year anywhere, also in "FY2026"
UNIT = re.compile(
    r"\bin\s+(thousands|millions|billions)\b"                                # (In millions) / $ in thousands
    r"|\((?:[A-Za-z$€£]{1,4}\s+)?(thousands|millions|billions)\)"  # (GBP thousands) / (thousands)
    r"|\b(?:USD|GBP|EUR|JPY|CAD|AUD|CHF|NZD|INR|CNY|US\$)\s+(thousands|millions|billions)\b"  # USD millions
    r"|[$€£]\s?(thousands|millions|billions)\b"
    r"|\bin\s+(?:₹|rs\.?|inr)?\s*(lakhs?|lacs?|crores?|cr)\b"                    # (₹ in crore) / Amount in ₹ Crores / (Rs. in lakhs)
    r"|\((?:₹|rs\.?|inr)\s*(lakhs?|lacs?|crores?|cr)\)",                         # (₹ crore)
    re.I,
)
SCALE = {"thousands": 1e3, "millions": 1e6, "billions": 1e9, "lakh": 1e5, "crore": 1e7}
UNIT_ALIAS = {"lakhs": "lakh", "lac": "lakh", "lacs": "lakh", "crores": "crore", "cr": "crore"}  # spellings -> one name
MONTHS = re.compile(r"\b(three|six|nine|twelve)\s+months\b|\b(year|fiscal year|quarter)s?\s+ended\b", re.I)
DATE = re.compile(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}\b", re.I)
CHANGE = re.compile(r"(%|percent|change|v\s?%|year-on-year|yoy|bps|variance)", re.I)  # a "% change" column, not a period


# A line of text: words that sit at the same height.
@dataclass
class Line:
    words: list[Word]
    page: int

    # The words joined with spaces.
    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    # Top edge of the line.
    @property
    def top(self) -> float:
        return min(w.y0 for w in self.words)

    # Bottom edge of the line.
    @property
    def bottom(self) -> float:
        return max(w.y1 for w in self.words)

    # Left edge of the line.
    @property
    def x0(self) -> float:
        return self.words[0].x0

    # Right edge of the line.
    @property
    def x1(self) -> float:
        return max(w.x1 for w in self.words)

    # Typical (median) font size of the line.
    @property
    def size(self) -> float:
        return statistics.median(w.size for w in self.words)

    # True when at least 80% of the letters are bold.
    @property
    def bold(self) -> bool:
        return sum(len(w.text) for w in self.words if w.bold) >= 0.8 * sum(len(w.text) for w in self.words)


# One number on a line: its position, the text as printed, and its value.
@dataclass
class Cell:
    x0: float
    x1: float  # right edge of the digits (a closing bracket or % sign is not counted): columns line up on this
    raw: str
    value: float | None


# A line split into label + numbers. kind: "data" (has numbers), "header" (only years), "label" (only text), "empty".
@dataclass
class Row:
    page: int
    top: float
    bottom: float
    x0: float  # left edge of the label (or of the first number when there is no label)
    label: str
    cells: list[Cell]
    kind: str
    line: Line | None = field(default=None, repr=False)


# One column of a table: where its numbers end, its heading text and the period it covers.
@dataclass
class Column:
    x1: float
    left: float
    header: str = ""
    period: dict = field(default_factory=dict)


# One table row: label, one value per column (None = empty), the printed text of each value, indent and section.
@dataclass
class TRow:
    label: str
    values: list[float | None]
    raw: list[str]
    indent: int
    page: int
    section: str = ""  # the heading above it, e.g. "Current assets:"


# A table found on a page (or spread over consecutive pages once statements are merged).
@dataclass
class Table:
    page: int
    top: float
    bottom: float
    title: str
    unit: str | None
    scale: float | None
    columns: list[Column]
    rows: list[TRow]
    header_lines: list[str] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)
    kind: str | None = None  # income_statement, balance_sheet, ... (set when it is recognised as a statement)
    flags: list[str] = field(default_factory=list)

    # The table as plain JSON-ready data.
    def to_dict(self) -> dict:
        return {"kind": self.kind, "title": self.title, "pages": self.pages or [self.page], "unit": self.unit, "scale": self.scale,
                "columns": [{"header": c.header, "period": c.period} for c in self.columns],
                "rows": [{"label": r.label, "section": r.section, "indent": r.indent, "page": r.page, "values": r.values, "raw": r.raw}
                         for r in self.rows],
                "flags": self.flags}


# ---------------------------------------------------------------------------------------------------- words -> rows
# Group a page's words into lines: a word joins the current line if its centre is at (nearly) the same height.
def group_lines(page: PageData) -> list[Line]:
    words = sorted(page.words, key=lambda w: (w.yc, w.x0))
    if not words:
        return []
    heights = [w.y1 - w.y0 for w in words if w.y1 > w.y0]
    tol = max(1.5, 0.4 * (statistics.median(heights) if heights else 8))
    groups, ys = [[words[0]]], [words[0].yc]
    for w in words[1:]:
        if abs(w.yc - ys[-1]) <= tol:
            groups[-1].append(w)
            ys[-1] = statistics.fmean(x.yc for x in groups[-1])
        else:
            groups.append([w])
            ys.append(w.yc)
    return [Line(sorted(g, key=lambda w: w.x0), page.no) for g in groups]


# Read a printed number: "(4,901)" -> -4901, "12.5%" -> 12.5, "$64,696" -> 64696, "₹1,23,456" -> 123456; else None.
def parse_number(tok: str) -> float | None:
    t = RUPEE_PREFIX.sub("", tok.strip())
    if not NUMBER.match(t):
        return None
    v = float(re.sub(r"[^\d.]", "", t))
    return -v if "(" in t or t.lstrip("($€£₹").startswith(("-", "−", "–")) or t.endswith("-") else v


# True for a number or a dash (a dash in a number column means zero).
def is_numeric_token(t: str) -> bool:
    return t in DASHES or parse_number(t) is not None


# Split a line into its text label and the numbers on its right; also decide the row's kind.
def split_row(line: Line, gap_label: float = 5.0) -> Row:
    toks: list[list] = []  # [x0, x1, text]
    for w in line.words:
        t = w.text
        if t in CURRENCY:
            continue
        # a stray ")" "%" "bps" belongs to the number before it: "(39) %", "(4,901 )"
        if t in (")", "%", ")%", "%)", "pts", "bps") and toks and is_numeric_token(toks[-1][2].rstrip(")%")):
            if t not in ("pts", "bps"):
                toks[-1][2] += t
                toks[-1][1] = w.x1
            continue
        if len(t) > 1 and t[0] in "$€£₹" and is_numeric_token(t[1:]):
            t = t[1:]  # "$1,234" glued to its currency sign
        if (toks and toks[-1][2] in ("-", "−", "–") and parse_number(t) is not None
                and not t.startswith(("-", "(")) and w.x0 - toks[-1][1] < 2.0
                and not (len(toks) > 1 and is_numeric_token(toks[-2][2]))):  # "3 - 40" is a range, not -40
            toks[-1][1], toks[-1][2] = w.x1, "-" + t  # a minus sign printed as its own piece, touching the number
            continue
        toks.append([w.x0, w.x1, t])
    n = i = len(toks)
    while i > 0 and is_numeric_token(toks[i - 1][2]):
        i -= 1
    while 0 < i < n and toks[i][0] - toks[i - 1][1] < gap_label:
        i += 1  # numbers that touch the words belong to the label ("Level 1", "Note 4", "5,000 shares")
    label = " ".join(t[2] for t in toks[:i]).strip()
    cells = []
    for x0, x1, t in toks[i:]:
        suffix = len(t) - len(t.rstrip(")%"))
        cells.append(Cell(x0, x1 - suffix * (x1 - x0) / max(len(t), 1), t, 0.0 if t in DASHES else parse_number(t)))
    header_label = len(label) < 60 or re.search(r"\b(in (thousands|millions|billions|lakhs?|crores?)|years? ended|months ended|as of|fiscal)\b", label, re.I)
    if cells and all(YEAR_CELL.match(c.raw) for c in cells) and header_label:
        kind = "header"
    else:
        kind = "data" if cells else "label" if label else "empty"
    return Row(line.page, line.top, line.bottom, toks[0][0] if toks else line.x0, label, cells, kind, line)


# Drop printed page numbers: a lone small number in a margin, or centred among the first/last rows of the page.
def strip_furniture(rows: list[Row], width: float, height: float) -> list[Row]:
    out = []
    for i, r in enumerate(rows):
        if r.kind in ("data", "header") and not r.label and len(r.cells) == 1 and r.cells[0].raw.isdigit() and len(r.cells[0].raw) <= 4:
            c = r.cells[0]
            centred = abs((c.x0 + c.x1) / 2 - width / 2) < 0.12 * width
            if r.top > 0.9 * height or r.bottom < 0.07 * height or (centred and (i < 4 or i >= len(rows) - 2)):
                continue
        out.append(r)
    return out


# ---------------------------------------------------------------------------------------------------- units and periods
# Find the unit ("millions" -> 1e6) in the first text that states one.
def find_unit(texts: list[str]) -> tuple[str | None, float | None]:
    for t in texts:
        m = UNIT.search(t)
        if m:
            word = next(g for g in m.groups() if g).lower()
            word = UNIT_ALIAS.get(word, word)
            return word, SCALE[word]
    return None, None


# Read a column heading into a period: year, number of months, date, or "this is a % change column".
def parse_period(header: str) -> dict:
    p: dict = {}
    yrs = re.findall(r"(?<!\d)(?:19|20)\d\d(?!\d)", header)
    if yrs:
        p["year"] = int(yrs[-1])
    m = MONTHS.search(header)
    if m:
        word = (m.group(1) or m.group(2) or "").lower()
        p["months"] = {"three": 3, "six": 6, "nine": 9, "twelve": 12, "year": 12, "fiscal year": 12, "quarter": 3}.get(word)
    d = DATE.search(header)
    if d:
        p["date"] = d.group(0)
    if CHANGE.search(header) and "year" not in p:
        p["change"] = True
    return p


# ---------------------------------------------------------------------------------------------------- rows -> tables
# Columns: group the right edges of all numbers; edges within 9 points of each other are one column.
def _cluster_columns(rows: list[Row]) -> list[Column]:
    edges = sorted((c.x1, c.x0) for r in rows for c in r.cells)
    if not edges:
        return []
    groups = [[edges[0]]]
    for e in edges[1:]:
        if e[0] - groups[-1][-1][0] <= 9:
            groups[-1].append(e)
        else:
            groups.append([e])
    make = lambda gs: [Column(statistics.median(x for x, _ in g), min(x0 for _, x0 in g)) for g in gs]
    return make([g for g in groups if len(g) >= (1 if len(rows) < 4 else 2)]) or make(groups)


# The column whose right edge is closest to x1.
def _nearest(cols: list[Column], x1: float) -> int:
    return min(range(len(cols)), key=lambda i: abs(cols[i].x1 - x1))


# Column headings: heading phrases (words split at big gaps) go to every column they sit above.
def _header_text(cols: list[Column], header_rows: list[Row]) -> list[str]:
    parts: list[list[str]] = [[] for _ in cols]
    for r in header_rows:
        phrases: list[list] = []
        for w in (r.line.words if r.line else []):
            if phrases and w.x0 - phrases[-1][1] < 6:
                phrases[-1][1] = w.x1
                phrases[-1][2] += " " + w.text
            else:
                phrases.append([w.x0, w.x1, w.text])
        for x0, x1, text in phrases:
            if x1 < cols[0].left - 25:
                continue  # text left of the numbers, such as "Year Ended June 30,", is not a column heading
            for i in [i for i, c in enumerate(cols) if x0 <= c.x1 + 4 and x1 >= c.left - 4] or [_nearest(cols, x1)]:
                parts[i].append(text)
    return [" ".join(p) for p in parts]


# Rows that are column headings: walk up from the first number row until a units line, a title or prose.
def _header_rows(context: list[Row], cols: list[Column]) -> list[Row]:
    header_rows: list[Row] = []
    for r in reversed(context):
        if not r.line:
            continue
        if r.kind == "header":
            header_rows.insert(0, r)  # a year row (it may carry the units text too): keep it and keep going
            continue
        if UNIT.search(r.line.text) or len(r.line.words) > 14:
            # "(In millions) June 30, 2026 December 31, 2025": units and dates on the same line
            if YEAR.search(r.line.text) and r.line.x1 >= cols[0].left - 25 and len(r.line.words) <= 20:
                header_rows.insert(0, r)
            break
        in_cols = r.line.x1 >= cols[0].left - 25
        over_cols = r.line.x0 >= cols[0].left - 40  # column headings sit above the numbers, titles start at the margin
        if in_cols and r.kind in ("label", "data") and (over_cols or len(r.line.words) <= 4):
            header_rows.insert(0, r)
        elif not (r.kind == "label" and len(r.line.words) <= 6):
            break  # short text such as "Year Ended June 30," is skipped; anything else ends the headings
    return header_rows


# Build one table from its rows and the rows just above it (title, units, column headings).
def build_table(rows: list[Row], context: list[Row], page: int) -> Table | None:
    data = [r for r in rows if r.kind == "data"]
    if len(data) < 2:
        return None
    cols = _cluster_columns(data)
    if not cols:
        return None
    header_rows = _header_rows(context, cols)
    stub_text = [r.label for r in header_rows if r.label] or [r.label for r in context[-3:] if r.label]
    for c, h in zip(cols, _header_text(cols, header_rows)):
        c.header = h
        c.period = parse_period(h + " " + " ".join(s for s in stub_text[-3:] if not YEAR.search(s)))
    unit, scale = find_unit([r.line.text for r in context if r.line] + [r.label for r in rows[:3]])
    title = next((r.label for r in reversed(context)
                  if r.kind == "label" and len(r.label) > 3 and not UNIT.search(r.label) and not YEAR.search(r.label)
                  and not re.search(r"\b(years?|months|quarters?|weeks) ended\b|^as of\b", r.label, re.I)), "")
    rows = join_wrapped(rows)
    base = min([r.x0 for r in rows if r.label] or [0])
    out: list[TRow] = []
    flags: list[str] = []
    section, section_x0 = "", 0.0
    pending: Row | None = None  # a text-only row: either a section heading or the start of the next label
    for r in rows:
        if r.kind == "label":
            if pending is not None:
                section, section_x0 = pending.label, pending.x0
            pending = r
            continue
        if r.kind != "data":
            continue
        label = r.label
        if pending is not None:
            # a pending text row is a heading, unless the number row continues it in lower case ("assets, and other")
            if not pending.label.endswith(":") and r.label and r.label[0].islower() and not pending.label.isupper():
                label = pending.label + " " + r.label
            else:
                section, section_x0 = pending.label, pending.x0
            pending = None
        if section and r.label and r.x0 <= section_x0 + 1 and not r.label.lower().startswith("total"):
            section = ""  # a heading only covers rows indented under it (and its own "Total ..." line)
        values: list[float | None] = [None] * len(cols)
        raw = [""] * len(cols)
        for c in r.cells:
            i = _nearest(cols, c.x1)
            if abs(cols[i].x1 - c.x1) > 14:
                flags.append(f"p{r.page}: cell '{c.raw}' far from any column")
            if raw[i]:
                flags.append(f"p{r.page}: two cells in one column on row '{label[:40]}'")
            values[i], raw[i] = c.value, c.raw
        out.append(TRow(label, values, raw, int(round((r.x0 - base) / 7)) if r.label else 0, r.page, section))
    return Table(page, rows[0].top, rows[-1].bottom, title, unit, scale, cols, out,
                 [r.line.text for r in header_rows if r.line], [page], None, flags)


# Join a label that wraps onto a second line: the lines touch (no gap), the first does not end with ":", and the
# second starts in lower case or with a digit, or the first ends with a joining word ("and", "of" ...) or a comma.
def join_wrapped(rows: list[Row]) -> list[Row]:
    out: list[Row] = []
    i = 0
    while i < len(rows):
        r = rows[i]
        while (r.kind == "label" and not r.label.endswith(":") and i + 1 < len(rows)
               and rows[i + 1].kind in ("label", "data") and rows[i + 1].label
               and rows[i + 1].top - r.bottom < 0.3 * max(r.bottom - r.top, 6.0)
               and (rows[i + 1].label[0].islower() or rows[i + 1].label[0].isdigit()
                    or re.search(r"(\b(and|or|of|to|for|the|in|on|by|with|from|as|at|net)|[,(\-–/])$", r.label, re.I))):
            nxt = rows[i + 1]
            r = Row(nxt.page, r.top, nxt.bottom, min(r.x0, nxt.x0), r.label + " " + nxt.label, nxt.cells, nxt.kind, nxt.line)
            i += 1
        out.append(r)
        i += 1
    return out


# Title, units and period lines stay above a table as context; they are never pulled into it as a heading.
def _is_context_line(label: str) -> bool:
    return bool(UNIT.search(label) or YEAR.search(label) or re.search(
        r"\bstatements?\b|\bbalance sheets?\b|\b(years?|months|quarters?) ended\b|\(unaudited\)", label, re.I))


# Split one page's rows into tables: a table is a run of number rows; prose, a big gap or an empty line ends it.
def page_tables(rows: list[Row]) -> list[Table]:
    tables: list[Table] = []
    i, n, last_end = 0, len(rows), 0
    while i < n:
        if rows[i].kind != "data":
            i += 1
            continue
        start = i
        while (start > last_end and rows[start - 1].kind == "label" and len(rows[start - 1].label) < 90
               and rows[i].top - rows[start - 1].bottom < 40 and not _is_context_line(rows[start - 1].label)):
            start -= 1  # pull section headings like "Current assets:" into the table
        j = i
        lh = max(8.0, rows[i].bottom - rows[i].top)
        while j + 1 < n:
            nxt = rows[j + 1]
            if nxt.top - rows[j].bottom > 3.2 * lh or nxt.kind == "empty":
                break
            if nxt.kind == "label" and len(nxt.label) > 110:
                # prose ends the table; one long label followed straight away by numbers does not
                after = rows[j + 2] if j + 2 < n else None
                if not (after and after.kind == "data" and after.top - nxt.bottom < 1.5 * lh):
                    break
            j += 1
        while j > i and rows[j].kind == "label":
            j -= 1  # trailing text rows belong to whatever follows
        t = build_table(rows[start:j + 1], rows[max(last_end, start - 8):start], rows[i].page)
        if t:
            tables.append(t)
        last_end = i = j + 1
    return tables
