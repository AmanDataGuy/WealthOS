# tests/test_research.py
"""Research without vectors. A small 10-K-like PDF is indexed by finx; questions find the right pages
(BM25 + section boost); an answer is kept only when its numbers are on the pages and it cites pages it was shown.
Fake LLM, no network."""
import asyncio

import fitz
import pytest

from app.data import engine
from app.ai import research
from app.ai import llm_client as llm

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


def test_rrf_fuses_rankings():
    assert research.rrf([["a", "b", "c"], ["c", "a"]]) == ["a", "c", "b"]  # a: 1/61+1/62, c: 1/63+1/61, b: 1/62
    assert research.rrf([[], ["x"]]) == ["x"] and research.rrf([]) == []


def test_retrieval_metrics():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals" / "sets"))
    import run_retrieval as rr
    ranked = [("f", 9), ("f", 3), ("f", 4), ("f", 1), ("f", 2), ("f", 5)]
    assert rr.recall_at(ranked, {("f", 4)}) == 1 and rr.recall_at(ranked, {("f", 5)}) == 0
    assert rr.reciprocal_rank(ranked, {("f", 4), ("f", 5)}) == 1 / 3 and rr.reciprocal_rank(ranked, {("g", 1)}) == 0.0
    assert rr.winner({"hybrid": {"recall_at_5": 0.8, "mrr": 0.9}, "bm25_boost": {"recall_at_5": 0.8, "mrr": 0.7}}) == "hybrid"
    assert rr.winner({"hybrid": {"recall_at_5": 0.7, "mrr": 0.9}, "bm25_boost": {"recall_at_5": 0.8, "mrr": 0.7}}) == "bm25_boost"


# A stand-in for main._vectors that records every statement and answers searches with page 4 of the Acme filing.
def _fake_store(monkeypatch, fail=False):
    from app import main
    seen = []

    async def fake(query, *args):
        seen.append((query.split()[0], args))
        if fail:
            raise OSError("connection refused")
        return [{"file_hash": "acme", "page": 4}]
    monkeypatch.setattr(main, "_vectors", fake)
    monkeypatch.setattr(research, "embed", lambda texts: [[0.0] * 384 for _ in texts])  # never download the model
    return seen


def test_hybrid_mode_stores_and_fuses_vectors(filing, tmp_path, monkeypatch):
    seen = _fake_store(monkeypatch)
    _fake(monkeypatch, "See [p. 4].")
    asyncio.run(research.answer("u", "What are the main risks?"))
    assert seen == []  # default (bm25) mode never touches the store

    monkeypatch.setenv("WEALTHOS_RESEARCH_SEARCH", "hybrid")
    research.add("u", "default", tmp_path / "acme_10k.pdf", "acme")
    kind, args = seen[0]
    assert kind == "INSERT" and args[:3] == ("u", "default", "acme") and args[3] == [1, 2, 3, 4, 5]
    out = asyncio.run(research.answer("u", "zebra quantum"))  # no keyword match: only the vector side finds page 4
    assert seen[-1][0] == "SELECT" and [c["page"] for c in out["citations"]] == [4]
    research.delete_doc("u", "default", "acme")
    assert seen[-1][0] == "DELETE"


def test_hybrid_falls_back_to_keywords_when_store_is_down(filing, monkeypatch, caplog):
    _fake_store(monkeypatch, fail=True)
    monkeypatch.setenv("WEALTHOS_RESEARCH_SEARCH", "hybrid")
    _fake(monkeypatch, "Acme relies on one Taiwan supplier for 70% of motor controllers [p. 3].")
    ok = asyncio.run(research.answer("u", "What are the main risks?"))
    assert ok["verified"] and ok["citations"][0]["page"] == 3
    assert "keyword-only" in caplog.text
