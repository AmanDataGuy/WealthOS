# tests/test_research.py
"""Research without vectors. A small 10-K-like PDF is indexed by finx; questions find the right pages
(BM25 + section boost); an answer is kept only when its numbers are on the pages and it cites pages it was shown.
Fake LLM, no network."""
import asyncio

import fitz
import pytest

from wealthos import engine
from wealthos import research
from wealthos.services import llm_client as llm

PAGES = [("FORM 10-K", "ANNUAL REPORT PURSUANT TO SECTION 13 OR 15(d). Acme Robotics Inc. Fiscal year ended December 31, 2025."),
         ("Item 1. Business", "Acme makes warehouse robots. We have three segments: Logistics, Retail and Healthcare. We employ 4,200 people."),
         ("Item 1A. Risk Factors", "We depend on a single supplier in Taiwan for 70% of our motor controllers. A disruption could "
                                   "halt production. Tariffs on imported parts could raise our costs."),
         ("Item 3. Legal Proceedings", "In March 2025 a competitor filed a patent lawsuit against us in Delaware."),
         ("Item 7. Management's Discussion and Analysis", "Revenue grew 12% to $5,000 million, driven by the Logistics "
                                                          "segment. Liquidity remains strong with $800 million in cash.")]


@pytest.fixture()
def filing(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DATA_ROOT", tmp_path / "users")
    doc = fitz.open()
    for heading, body in PAGES:
        page = doc.new_page()
        page.insert_text((72, 90), heading, fontsize=16, fontname="hebo")
        page.insert_textbox(fitz.Rect(72, 110, 540, 700), body, fontsize=10)
    doc.save(tmp_path / "acme_10k.pdf")
    return research.add("u", "default", tmp_path / "acme_10k.pdf", "acme")


def _fake(monkeypatch, text):
    async def fake(system, user, **kw):
        return text
    monkeypatch.setattr(llm, "call_llm", fake)


def test_pages_found_by_section_and_words(filing):
    assert filing == {"doc_id": "acme", "file": "acme_10k.pdf", "doc_type": "10-K", "pages": 5}
    top = lambda q: research.find_pages("u", "default", q)[0]["page"]
    assert top("What are the main risks?") == 3 and top("Is there any lawsuit?") == 4
    assert top("How did revenue do?") == 5 and top("How many segments are there?") == 2
    assert research.find_pages("u", "other_ws", "risks") == []  # another workspace sees nothing


def test_answer_checked(filing, monkeypatch):
    _fake(monkeypatch, "Acme relies on one Taiwan supplier for 70% of motor controllers [p. 3].")
    ok = asyncio.run(research.answer("u", "What are the main risks?"))
    assert ok["verified"] and ok["citations"][0]["page"] == 3 and "Taiwan" in ok["citations"][0]["excerpt"]

    _fake(monkeypatch, "Acme relies on one Taiwan supplier for 85% of motor controllers [p. 3].")  # number not on the page
    bad = asyncio.run(research.answer("u", "What are the main risks?"))
    assert not bad["verified"] and "85" not in bad["text"] and "85" in bad["warnings"][0]

    _fake(monkeypatch, "See the auditor's letter [p. 9].")  # cites a page it was never shown
    stray = asyncio.run(research.answer("u", "What are the main risks?"))
    assert not stray["verified"] and "[9]" in stray["warnings"][0]
    research.delete_doc("u", "default", "acme")
    assert research.list_docs("u") == []
