"""
# chunking.py - cuts an indexed filing into chunks for search, with the metadata each chunk is filtered by

- Pure functions, no database, no model. Input: research.py's page dicts ({page, section, toc, headings, text}, plus
  finx's `sections_started` when present) and `doc_meta`'s {company, form, period}.
- `structure_chunks` starts a chunk at every Item / Part / Note and packs paragraphs inside a section;
  `fixed_chunks` is the baseline: word windows with an overlap, blind to structure.
- Every chunk is {chunk, chunker, company, form, period, section, page_start, page_end, headings, text}.
  Used by research.py at upload and by the retrieval eval.
"""
from __future__ import annotations

import bisect
import re

from app.data.finx.page_index import ITEM, NOTE, PART

MONTHS = "january february march april may june july august september october november december".split()
DATE = r"([A-Z][a-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})"
COMPANY = re.compile(r"\b([A-Z][\w&.,'-]*(?:[ \t]+[A-Z][\w&.,'-]*){0,5}[ \t]+"
                     r"(?:Inc|Corp|Corporation|Company|Ltd|Limited|plc|PLC|LLC|N\.V|S\.A|AG|SE|Holdings)\b\.?)")


# Company, form and fiscal period from the cover page; the company falls back to `fallback` (the file name).
def doc_meta(pages: list[dict], doc_type: str, fallback: str) -> dict:
    text = "\n".join(p.get("text", "") for p in pages[:3])
    m = re.search(r"([^\n]*\S[^\n]*)\n[^\n]*exact name of registrant", text, re.I) or COMPANY.search(text)
    company = m.group(1).strip().rstrip(",") if m else fallback
    period = ""
    if m := re.search(r"fiscal\s+year\s+ended\s+" + DATE, text, re.I):
        period = f"FY{m.group(3)}"
    elif (m := re.search(r"quarterly\s+period\s+ended\s+" + DATE, text, re.I)) and m.group(1).lower() in MONTHS:
        period = f"QE{m.group(3)}-{MONTHS.index(m.group(1).lower()) + 1:02d}"  # quarter ending, e.g. QE2025-06
    return {"company": company, "form": doc_type, "period": period}


# One chunk dict in the shared shape.
def _chunk(n: int, chunker: str, meta: dict, section, pages: list[int], headings: str, text: str) -> dict:
    return {"chunk": n, "chunker": chunker, "company": meta["company"], "form": meta["form"], "period": meta["period"],
            "section": section, "page_start": min(pages), "page_end": max(pages), "headings": headings, "text": text}


# "Item 7" / "Note 4" for a line that opens one, "Part" for a bare Part line, else None.
def _label(line: str) -> str | None:
    if (m := ITEM.match(line)) and m.group("item"):
        return f"Item {m.group('item').upper()}"
    if m := NOTE.match(line):
        return f"Note {m.group(1).upper()}"
    return "Part" if PART.match(line) else None


# Split a paragraph longer than `limit` words at sentence ends, and a sentence still too long at word counts.
def _fit(paragraph: str, limit: int) -> list[str]:
    if len(paragraph.split()) <= limit:
        return [paragraph]
    out, buf = [], []
    for s in re.split(r"(?<=[.!?])\s+", paragraph):
        words = s.split()
        while len(words) > limit:
            out.append(" ".join(words[:limit]))
            words = words[limit:]
        if buf and len(" ".join(buf).split()) + len(words) > limit:
            out.append(" ".join(buf))
            buf = []
        if words:
            buf.append(" ".join(words))
    return out + ([" ".join(buf)] if buf else [])


# Chunks that follow the filing's structure: a new chunk at every Item / Part / Note, paragraphs packed to max_words.
def structure_chunks(pages: list[dict], meta: dict, max_words: int = 350) -> list[dict]:
    chunks, buf = [], []  # buf: (page, paragraph) of the open chunk
    state = {"section": None, "sub": None, "path": "", "body": False}  # body: buf has more than headings

    def flush():
        if buf:
            body = "\n".join(t for _, t in buf)
            path = state["path"]
            chunks.append(_chunk(len(chunks), "structure", meta, state["chunk_section"], [p for p, _ in buf], path,
                                 f"{path}\n{body}" if path else body))
            buf.clear()
            state["body"] = False

    def add(page: int, text: str, head: bool = False):
        for piece in _fit(text, max_words):
            # a heading stays with the text under it, so only a chunk with body text is closed when full
            if state["body"] and sum(len(t.split()) for _, t in buf) + len(piece.split()) > max_words:
                flush()
            if not buf:  # the chunk is labelled with the heading path where it starts
                state["path"] = " > ".join(filter(None, [state["section"], state["sub"]]))
                state["chunk_section"] = state["section"]
            buf.append((page, piece))
            state["body"] = state["body"] or not head

    for p in pages:
        if p.get("toc"):
            continue
        heads = set(filter(None, (p.get("headings") or "").split(" | ")))
        started = list(p.get("sections_started")
                       or ([p["section"]] if p.get("section") and p["section"] != state["section"] else []))
        lines = [l.strip() for l in (p.get("text") or "").split("\n")]
        # place each section that starts on this page at its heading line; if one cannot be placed, the page starts it
        at, todo = {}, list(started)
        for i, l in enumerate(lines):
            lab = _label(l)
            if todo and lab and lab != "Part" and todo[0].endswith(lab):
                at[i] = todo.pop(0)
        if todo:
            at = {0: started[-1]}
        para = []
        for i, l in enumerate(lines):
            head = i in at or l in heads
            if (not l or head) and para:  # a blank line or a heading ends the paragraph
                add(p["page"], " ".join(para))
                para = []
            if i in at or (head and _label(l) == "Part"):
                flush()
                if i in at:
                    state["section"], state["sub"] = at[i], None
            elif head:
                state["sub"] = l
            if head and l:
                add(p["page"], l, head=True)
            elif l:
                para.append(l)
        if para:
            add(p["page"], " ".join(para))
    flush()
    return chunks


# Baseline chunks: windows of `words` words, `overlap` shared with the next, over all non-contents text.
def fixed_chunks(pages: list[dict], meta: dict, words: int = 350, overlap: int = 50) -> list[dict]:
    pages = [p for p in pages if not p.get("toc")]
    starts, parts, at = [], [], 0
    for p in pages:
        starts.append(at)
        parts.append(p.get("text") or "")
        at += len(parts[-1]) + 1
    full = "\n".join(parts)
    toks = list(re.finditer(r"\S+", full))
    page_of = lambda pos: pages[bisect.bisect_right(starts, pos) - 1]
    chunks = []
    for i in range(0, len(toks), words - overlap):
        w = toks[i:i + words]
        first, last = page_of(w[0].start()), page_of(w[-1].end() - 1)
        chunks.append(_chunk(len(chunks), "fixed", meta, first.get("section"), [first["page"], last["page"]],
                             first.get("section") or "", full[w[0].start():w[-1].end()]))
        if i + words >= len(toks):
            break
    return chunks
