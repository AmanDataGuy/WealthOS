"""
# page_index.py - what is on each page

For every page it records:
- the page text (without repeating headers/footers and page numbers),
- the headings (by font size, bold or capitals),
- which section the page is in ("Item 7", "Part I Item 2" ...) and which sections start on it,
- whether it is the table of contents.

It also decides the document type (10-K, 10-Q, 8-K, 20-F, press release, other) from the first two pages.
"""
from __future__ import annotations

import re
from collections import Counter

from .tables import Line

ITEM = re.compile(r"^(?:PART\s+(?P<part>I{1,3}|IV)\b[.,:\s—–-]*)?ITEM\s+(?P<item>\d{1,2}[A-C]?)\s*[.:—–-]?\s*(?P<title>.*)$", re.I)
PART = re.compile(r"^PART\s+(I{1,3}|IV)\b\.?\s*(?:[—–-]\s*)?(.*)$", re.I)
NOTE = re.compile(r"^NOTE\s+(\d{1,2}|[A-Z])\b\s*[.:—–-]?\s*(.*)$", re.I)
ITEM_ORDER_10K = ["1", "1A", "1B", "1C", "2", "3", "4", "5", "6", "7", "7A", "8", "9", "9A", "9B", "9C", "10", "11", "12", "13", "14", "15", "16"]


# Decide the document type from the text of its first pages.
def doc_type(first_pages_text: str) -> str:
    t = first_pages_text.upper()
    for kind, pattern in [("10-K", r"FORM\s+10-K\b|ANNUAL REPORT PURSUANT TO SECTION 13"),
                          ("10-Q", r"FORM\s+10-Q\b|QUARTERLY REPORT PURSUANT TO SECTION 13"),
                          ("8-K", r"FORM\s+8-K\b|CURRENT REPORT PURSUANT TO SECTION 13"),
                          ("20-F", r"FORM\s+20-F\b"),
                          ("press_release", r"\bREPORTS?\b.{0,80}\b(RESULTS|EARNINGS)\b|PRESS RELEASE|FOR IMMEDIATE RELEASE")]:
        if re.search(pattern, t):
            return kind
    return "other"


# Text with every number replaced by "#", so "Page 4" and "Page 5" look the same.
def _shape(text: str) -> str:
    return re.sub(r"\d+", "#", text.lower()).strip()


# True when the line is in the top or bottom 9% of the page.
def _in_band(line: Line, height: float) -> bool:
    return line.bottom < 0.09 * height or line.top > 0.91 * height


# Running headers/footers: text in the top/bottom band that repeats on at least a quarter of the pages.
def furniture(pages_lines: list[list[Line]], heights: list[float]) -> set[str]:
    if len(pages_lines) < 4:
        return set()
    seen = Counter(t for lines, h in zip(pages_lines, heights) for t in {_shape(l.text) for l in lines if _in_band(l, h)})
    return {t for t, c in seen.items() if c >= max(3, 0.25 * len(pages_lines)) and t not in ("#", "")}


# The most common font size in the document (weighted by letters) = normal body text.
def body_size(pages_lines: list[list[Line]]) -> float:
    sizes: Counter = Counter()
    for lines in pages_lines:
        for l in lines:
            for w in l.words:
                sizes[round(w.size * 2) / 2] += len(w.text)
    return sizes.most_common(1)[0][0] if sizes else 10.0


# ('9B', 'OTHER INFORMATION') if the line starts a 10-K/10-Q Item, else None.
# Accepts "ITEM 1. BUSINESS." and run-in headings ("ITEM 2. PROPERTIES. GE Vernova is ..."); rejects a sentence that
# only happens to start with "Item 1A." (a real heading is upper-case, bold or bigger) and lists like "ITEM 10, 11 and 14."
def item_heading(line: Line, body: float) -> tuple[str, str] | None:
    text = line.text.strip()
    m = ITEM.match(text)
    if not m or not m.group("item"):
        return None
    first = text.split(None, 1)[0] if not m.group("part") else text.split()[2] if len(text.split()) > 2 else "item"
    strong = first.isupper() or line.bold or line.size >= body * 1.05
    title = (m.group("title") or "").strip()
    sentence = re.split(r"(?<=[A-Za-z\]])\.\s+(?=[A-Z][a-z])", title, maxsplit=1)[0].rstrip(". ")
    if not strong or len(sentence.split()) > 14:
        return None
    if re.match(r"^[,\s]*\d|^(,|and\b|or\b|through\b|to\b)", title, re.I) or not re.search(r"[A-Za-z]{3}", title):
        return None
    return m.group("item").upper(), sentence


# Heading level of a line: 1 = Item/Part, 2 = big text or "Note 4", 3 = bold/capitals/run-in heading, None = not a heading.
def heading_level(line: Line, body: float, is_data: bool) -> int | None:
    text = line.text.strip()
    if not re.search(r"[A-Za-z]", text) or len(text) < 3:
        return None
    words = text.split()
    if item_heading(line, body) or (PART.match(text) and len(words) <= 8 and (line.bold or words[0].isupper())):
        return 1
    if is_data or text.endswith(","):
        return None
    # run-in heading: a capitalised phrase that opens a paragraph ("COMPETITION. We believe ..."), bold or all capitals
    m = re.match(r"^([A-Z][A-Za-z0-9 ,&'’/()-]{3,70}?)\.\s+[A-Z“\"]", text)
    if m and len(words) > 16:
        letters = re.sub(r"[^A-Za-z]", "", m.group(1))
        if (letters.isupper() or line.bold) and len(m.group(1).split()) <= 10 and len(letters) >= 4:
            return 3
    if len(words) > 16:
        return None
    if NOTE.match(text) and len(words) <= 14:
        return 2
    letters = re.sub(r"[^A-Za-z]", "", text)
    if line.size >= body * 1.15 and len(words) <= 14:
        return 2 if line.size >= body * 1.4 else 3
    caps = len(letters) >= 4 and letters.isupper() and len(words) <= 12
    if (line.bold or caps) and len(words) <= 12 and not text.endswith(".") and not text.startswith("("):
        return 3
    return None


# "Item 7" or, in a 10-Q, "Part I Item 2"; None before any Item.
def _section(dtype: str, part: str | None, item: str | None) -> str | None:
    if item is None:
        return None
    return f"Part {part} Item {item}" if dtype == "10-Q" and part else f"Item {item}"


# Build the page index: text, headings, sections, table-of-contents flag for every page.
def build_index(pages_lines: list[list[Line]], pages_rows_kinds: list[list[str]], widths: list[float], heights: list[float]) -> dict:
    furn = furniture(pages_lines, heights)
    body = body_size(pages_lines)
    dtype = doc_type(" ".join(l.text for lines in pages_lines[:2] for l in lines))
    pages = []
    part, item = None, None
    for idx, (lines, kinds, w, h) in enumerate(zip(pages_lines, pages_rows_kinds, widths, heights)):
        # a table-of-contents page has several "Item 1. Business ... 4" lines that end in a page number
        is_toc = sum(1 for l in lines if ITEM.match(l.text.strip()) and re.search(r"\s\d{1,3}\s*$", l.text.strip())) >= 4
        running, body_lines = [], []
        for l, kind in zip(lines, kinds):
            if _shape(l.text) in furn and _in_band(l, h):
                running.append(l.text)  # repeating header/footer
            elif not (re.fullmatch(r"\d{1,4}", l.text.strip()) and (_in_band(l, h) or abs((l.x0 + l.x1) / 2 - w / 2) < 0.12 * w)):
                body_lines.append((l, kind))  # (a lone page number is dropped)
        for r in running:  # a running header such as "PART II Item 8" states the section directly
            m = re.search(r"\bPART\s+(I{1,3}|IV)\b", r, re.I)
            if m:
                part = m.group(1).upper()
            m = re.search(r"\bITEM\s+(\d{1,2}[A-C]?)\b", r, re.I)
            if m:
                item = m.group(1).upper()
        section_at_start = _section(dtype, part, item)
        headings, started = [], []
        for l, kind in body_lines:
            text = l.text.strip()
            lvl = heading_level(l, body, kind == "data")
            if lvl is None:
                continue
            if not is_toc and kind != "data":  # an Item line with a page number after it is a contents entry
                mp = PART.match(text)
                if mp:
                    part = mp.group(1).upper()
                ih = item_heading(l, body)
                if ih:
                    mi = ITEM.match(text)
                    if mi.group("part"):
                        part = mi.group("part").upper()
                    # 10-K Items come in order; an Item that jumps backwards is a cross-reference, not a new section
                    if dtype != "10-K" or item is None or (ih[0] in ITEM_ORDER_10K and item in ITEM_ORDER_10K
                                                           and ITEM_ORDER_10K.index(ih[0]) >= ITEM_ORDER_10K.index(item)):
                        item = ih[0]
                        started.append(_section(dtype, part, item))
            headings.append({"text": text, "y": round(l.top, 1)})
        pages.append({"page": idx + 1, "section": section_at_start, "sections_started": started, "is_toc": is_toc,
                      "headings": headings, "text": "\n".join(l.text for l, _ in body_lines)})
    return {"doc_type": dtype, "pages": pages}
