# tests/test_chunking.py
"""Chunking a filing for search, on synthetic pages only: structure chunks never cross an Item or Note and keep their
pages, fixed windows overlap and cover all text, contents pages are skipped, the cover page gives company and period,
and finx turns "NOTE 7" headings into sections."""
import re

import fitz

from app.ai.chunking import doc_meta, fixed_chunks, structure_chunks
from app.data.finx.pipeline import extract

META = {"company": "Acme Robotics Inc.", "form": "10-K", "period": "FY2025"}
KEYS = {"chunk", "chunker", "company", "form", "period", "section", "page_start", "page_end", "headings", "text"}
COVER = {"page": 1, "section": None, "toc": False, "headings": "FORM 10-K",
         "text": "FORM 10-K\nANNUAL REPORT PURSUANT TO SECTION 13\nFor the fiscal year ended September 27, 2025\n"
                 "Acme Robotics Inc.\n(Exact name of registrant as specified in its charter)"}
TOC = {"page": 2, "section": None, "toc": True, "headings": "", "text": "Item 1. Business 4\nItem 7. MD&A 9\nNote 7. Debt 30"}


# A page of `n` numbered filler words, so every word can be traced.
def _words(tag: str, n: int) -> str:
    return " ".join(f"{tag}{i}" for i in range(n))


PAGES = [COVER, TOC,
         {"page": 3, "section": "Item 7", "sections_started": ["Item 1", "Item 7"], "toc": False,
          "headings": "Item 1. Business | Item 7. Management's Discussion and Analysis | Liquidity",
          "text": f"Item 1. Business\n{_words('biz', 40)}\nItem 7. Management's Discussion and Analysis\n"
                  f"Liquidity\n{_words('liq', 400)}"},
         {"page": 4, "section": "Item 7", "toc": False, "headings": "", "text": _words("more", 100)},
         {"page": 5, "section": "Note 7", "sections_started": ["Item 8", "Note 7"], "toc": False,
          "headings": "Item 8. Financial Statements | NOTE 7. DEBT",
          "text": f"Item 8. Financial Statements\n{_words('fin', 30)}\nNOTE 7. DEBT\n{_words('debt', 50)}"}]


def test_structure_chunks_follow_sections_and_pages():
    chunks = structure_chunks(PAGES, META)
    assert all(set(c) == KEYS and c["chunker"] == "structure" for c in chunks)
    assert [c["chunk"] for c in chunks] == list(range(len(chunks)))
    by_section = {c["section"]: c for c in chunks}
    assert list(by_section) == [None, "Item 1", "Item 7", "Item 8", "Note 7"]
    home = {"biz": "Item 1", "liq": "Item 7", "more": "Item 7", "fin": "Item 8", "debt": "Note 7"}
    for c in chunks:  # every filler word in a chunk belongs to the chunk's own section
        assert {home[t] for t in re.findall(r"\b(biz|liq|more|fin|debt)\d+", c["text"])} <= {c["section"]}
    item7 = [c for c in chunks if c["section"] == "Item 7"]
    assert item7[0]["page_start"] == 3 and item7[-1]["page_end"] == 4  # MD&A runs over two pages
    assert item7[0]["text"].startswith("Item 7\nItem 7. Management") and "liq0" in item7[0]["text"]
    assert any(c["headings"] == "Item 7 > Liquidity" for c in item7)
    assert by_section["Note 7"]["text"].startswith("Note 7\nNOTE 7. DEBT") and "fin0" not in by_section["Note 7"]["text"]
    assert all(len(c["text"].split()) <= 350 + 12 for c in chunks)  # headings ride along with their text
    assert not any("MD&A 9" in c["text"] for c in chunks)  # the contents page is skipped


def test_structure_chunks_without_sections_started():
    pages = [{k: v for k, v in p.items() if k != "sections_started"} for p in PAGES]
    note = [c for c in structure_chunks(pages, META) if c["section"] == "Note 7"]
    assert note and note[0]["text"].startswith("Note 7\nNOTE 7. DEBT") and "fin0" not in note[0]["text"]


def test_fixed_windows_overlap_and_cover():
    chunks = fixed_chunks(PAGES, META, words=100, overlap=20)
    assert all(set(c) == KEYS and c["chunker"] == "fixed" for c in chunks)
    allwords = "\n".join(p["text"] for p in PAGES if not p["toc"]).split()
    for a, b in zip(chunks, chunks[1:]):
        assert a["text"].split()[-20:] == b["text"].split()[:20]
    covered = [w for c in chunks[:1] for w in c["text"].split()] + [w for c in chunks[1:] for w in c["text"].split()[20:]]
    assert covered == allwords and not any("MD&A 9" in c["text"] for c in chunks)
    spans = [c for c in chunks if c["page_start"] != c["page_end"]]
    assert spans and all(c["section"] == next(p["section"] for p in PAGES if p["page"] == c["page_start"]) for c in chunks)
    two = next(c for c in chunks if "liq399" in c["text"] and "more0" in c["text"])
    assert (two["page_start"], two["page_end"]) == (3, 4)


def test_doc_meta_from_cover_page():
    assert doc_meta(PAGES, "10-K", "acme.pdf") == {"company": "Acme Robotics Inc.", "form": "10-K", "period": "FY2025"}
    plain = [{"text": "Quarterly report. Widget Holdings Inc. for the quarterly period ended June 28, 2025."}]
    assert doc_meta(plain, "10-Q", "w.pdf") == {"company": "Widget Holdings Inc.", "form": "10-Q", "period": "QE2025-06"}
    assert doc_meta([{"text": "nothing here"}], "other", "notes.pdf") == {"company": "notes.pdf", "form": "other", "period": ""}


def test_finx_tracks_note_sections(tmp_path):
    doc = fitz.open()
    for heading, body in [("FORM 10-K", "ANNUAL REPORT PURSUANT TO SECTION 13. Fiscal year ended December 31, 2025."),
                          ("Item 8. Financial Statements", "The statements follow."),
                          ("NOTE 7. DEBT", "We have a revolving credit line.")]:
        page = doc.new_page()
        page.insert_text((72, 90), heading, fontsize=16, fontname="hebo")
        page.insert_textbox(fitz.Rect(72, 110, 540, 700), body, fontsize=10)
    doc.save(tmp_path / "f.pdf")
    pages = extract(str(tmp_path / "f.pdf"))["pages"]
    assert pages[1]["sections_started"] == ["Item 8"] and pages[2]["sections_started"] == ["Note 7"]
    assert pages[2]["section"] == "Item 8"  # the section at the top of the page, before the note heading
