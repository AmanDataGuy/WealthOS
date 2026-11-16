# tests/test_research.py
"""Research over chunks. Hand-built chunks for two filings test search (filters, BM25, fusion, re-ranking), the
refusal by code and the number check against cited chunks; a small 10-K-like PDF tests indexing end to end.
Fake LLM, fake embedder and re-ranker: no network, no database, no model download."""
import asyncio
import json

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
ACME = {"company": "Acme Robotics Inc.", "form": "10-K", "period": "FY2025"}
BETA = {"company": "Beta Foods Corp", "form": "10-Q", "period": "FY2024"}
ACME_CHUNKS = [("Item 1", 2, 2, "Item 1. Business > Acme makes warehouse robots in three segments."),
               ("Item 1A", 3, 3, "Item 1A. Risk Factors > We depend on a single supplier in Taiwan for 70% of motor controllers."),
               ("Item 3", 4, 4, "Item 3. Legal Proceedings > A competitor filed a patent lawsuit against us in Delaware."),
               ("Item 7", 5, 6, "Item 7. MD&A > Revenue grew 12% to $5,000 million. Liquidity is strong.")]
BETA_CHUNKS = [("Item 1A", 7, 7, "Item 1A. Risk Factors > We depend on a single supplier of wheat for 40% of flour."),
               ("Item 2", 8, 8, "Item 2. MD&A > Revenue fell 3% to $900 million.")]


# Write one indexed document the way `research.add` does, with the same chunks under both chunkers.
def _doc(doc_id, meta, rows):
    chunks = [meta | {"chunk": n, "section": s, "page_start": a, "page_end": b, "headings": "", "text": t}
              for n, (s, a, b, t) in enumerate(rows)]
    doc = {"doc_id": doc_id, "file": f"{doc_id}.pdf", "doc_type": meta["form"], "meta": meta, "pages": [],
           "chunks": {c: [x | {"chunker": c} for x in chunks] for c in research.CHUNKERS}}
    folder = research._folder("u", "default")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{doc_id}.json").write_text(json.dumps(doc), encoding="utf-8")


@pytest.fixture()
def two_docs(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DATA_ROOT", tmp_path / "users")
    monkeypatch.delenv("WEALTHOS_RESEARCH_SEARCH", raising=False)
    _doc("acme", ACME, ACME_CHUNKS)
    _doc("beta", BETA, BETA_CHUNKS)


@pytest.fixture()
def filing(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DATA_ROOT", tmp_path / "users")
    monkeypatch.delenv("WEALTHOS_RESEARCH_SEARCH", raising=False)
    doc = fitz.open()
    for heading, body in PAGES:
        page = doc.new_page()
        page.insert_text((72, 90), heading, fontsize=16, fontname="hebo")
        page.insert_textbox(fitz.Rect(72, 110, 540, 700), body, fontsize=10)
    doc.save(tmp_path / "acme_10k.pdf")
    return research.add("u", "default", tmp_path / "acme_10k.pdf", "acme")


# A fake model that returns `text` and counts its calls.
def _fake(monkeypatch, text):
    calls = []

    async def fake(system, user, **kw):
        calls.append(user)
        return text
    monkeypatch.setattr(llm, "call_llm", fake)
    return calls


def _search(q, **kw):
    return asyncio.run(research.search("u", "default", q, **kw))


def _answer(q, **kw):
    return asyncio.run(research.answer("u", q, **kw))


def test_rrf_fuses_rankings():
    assert research.rrf([["a", "b", "c"], ["c", "a"]]) == ["a", "c", "b"]  # a: 1/61+1/62, c: 1/63+1/61, b: 1/62
    assert research.rrf([[], ["x"]]) == ["x"] and research.rrf([]) == []


def test_retrieval_metrics():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals" / "sets"))
    import run_rag as rr
    hit = lambda a, b, f="f": {"file": f, "page_start": a, "page_end": b}  # noqa: E731
    hits = [hit(9, 9), hit(3, 3), hit(4, 6), hit(1, 1), hit(2, 2), hit(7, 7)]
    assert rr.recall_at(hits, {("f", 5)}) == 1 and rr.recall_at(hits, {("f", 7)}) == 0  # 5 is inside pages 4-6
    assert rr.reciprocal_rank(hits, {("f", 5), ("f", 7)}) == 1 / 3 and rr.reciprocal_rank(hits, {("g", 9)}) == 0.0
    # no hits (None) is always refused; 0.15 and 0.6 both get 5 of 6 right, and the lower one wins the tie
    scores, answerable = [0.1, 0.3, 0.2, 0.9, 1.5, None], [False, False, True, True, True, False]
    assert rr.tune(scores, answerable) == 0.15
    assert rr.refusals(scores, answerable, 0.6) == {"unanswerable_refused": 3, "unanswerable": 3,
                                                    "answerable_refused": 1, "answerable": 3}
    assert rr.fact_share(["$1,234 million", "Ninth Circuit"], "It was $1, 234 million at the ninth  circuit.") == 1.0
    dev, test = rr.split(rr.load_set())
    assert len(dev) == 27 and len(test) == 54 and not set(dev) & set(test)


def test_filters_from_question_and_caller(two_docs):
    assert {h["doc_id"] for h in _search("What does Acme depend on a supplier for?")} == {"acme"}
    assert {h["doc_id"] for h in _search("Beta supplier risks")} == {"beta"}
    assert {h["doc_id"] for h in _search("supplier risks in 2024")} == {"beta"}  # 2024 is Beta's period
    assert {h["doc_id"] for h in _search("supplier risks in the 10-Q")} == {"beta"}
    assert {h["doc_id"] for h in _search("supplier risks in 2019")} == {"acme", "beta"}  # no filing's year: no filter
    assert {h["doc_id"] for h in _search("Acme supplier", filters={"company": "Beta"})} == {"beta"}  # caller wins
    assert research.question_filters("Acme vs Beta", [ACME, BETA]) == {}  # two companies: no company filter


def test_bm25_section_boost_and_fusion(two_docs):
    assert [h["page_start"] for h in _search("main risks", filters={"company": "Acme"})][:1] == [3]
    dense = [("acme", 2), ("acme", 0)]  # a precomputed vector ranking: no Postgres
    assert [h["chunk"] for h in _search("zebra", mode="vector", dense=dense)] == [2, 0]
    fused = _search("lawsuit Delaware", mode="hybrid", dense=[("acme", 0), ("acme", 2)])
    assert [h["chunk"] for h in fused][:2] == [2, 0]  # in both lists beats in one


def test_rerank_orders_and_scores(two_docs, monkeypatch):
    monkeypatch.setattr(research, "rerank_scores", lambda q, texts: [float("Delaware" in t) for t in texts])
    hits = _search("supplier lawsuit", filters={"company": "Acme"}, rerank=True)
    assert hits[0]["page_start"] == 4 and hits[0]["scored_by"] == "rerank" and hits[0]["score"] == 1.0


def test_refusal_below_threshold_makes_no_model_call(two_docs, monkeypatch):
    calls = _fake(monkeypatch, "anything [p. 3]")
    out = _answer("zebra quantum")
    assert out == {"text": research.NOT_FOUND, "citations": [], "verified": True,
                   "warnings": ["refused: no passage scored high enough"]} and calls == []
    monkeypatch.setattr(research, "rerank_scores", lambda q, texts: [research.REFUSE_RERANK - 1] * len(texts))
    assert _answer("supplier", rerank=True)["text"] == research.NOT_FOUND and calls == []


def test_relevance_check_refuses_before_answering(two_docs, monkeypatch):
    replies = ['{"answerable": false}', '{"answerable": true}', "Acme relies on one Taiwan supplier [p. 3]."]
    calls = []

    async def fake(system, user, **kw):
        calls.append(system)
        return replies.pop(0)
    monkeypatch.setattr(llm, "call_llm", fake)
    no = _answer("What are Acme's main risks?", check=True)
    assert no["text"] == research.NOT_FOUND and "relevance check" in no["warnings"][0] and len(calls) == 1
    yes = _answer("What are Acme's main risks?", check=True)
    assert yes["verified"] and "Taiwan" in yes["text"] and calls[-1] == research.ANSWERER


def test_full_width_citations_are_read(two_docs, monkeypatch):
    _fake(monkeypatch, "Acme relies on one Taiwan supplier for 70% of motor controllers【p. 3】.")
    out = _answer("What are Acme's main risks?")
    assert out["verified"] and out["citations"][0]["page"] == 3


def test_model_saying_not_found_is_a_refusal(two_docs, monkeypatch):
    _fake(monkeypatch, "Not found in the filings")
    out = _answer("What are Acme's main risks?")
    assert out["text"] == research.NOT_FOUND and "answer step" in out["warnings"][0]


def test_above_threshold_calls_the_model(two_docs, monkeypatch):
    calls = _fake(monkeypatch, "Acme relies on one Taiwan supplier for 70% of motor controllers [p. 3].")
    ok = _answer("What are Acme's main risks?")
    assert len(calls) == 1 and "=== acme.pdf [p. 3] (Item 1A)" in calls[0] and "beta" not in calls[0].lower()
    assert ok["verified"] and ok["citations"][0]["page"] == 3 and "Taiwan" in ok["citations"][0]["excerpt"]


def test_numbers_must_be_in_cited_chunks(two_docs, monkeypatch):
    q = "Acme supplier risk and revenue"
    _fake(monkeypatch, "Revenue grew 12% [p. 3].")  # 12% is only in the shown, uncited pp. 5-6 chunk
    bad = _answer(q)
    assert not bad["verified"] and "(12)" in bad["warnings"][0] and "12" not in bad["text"]
    _fake(monkeypatch, "Revenue grew 12% [pp. 5-6].")
    assert _answer(q)["verified"]
    _fake(monkeypatch, "Acme relies on one Taiwan supplier for 85% of motor controllers [p. 3].")
    assert not _answer(q)["verified"]
    _fake(monkeypatch, "See the auditor's letter [p. 9].")  # cites a page it was never shown
    stray = _answer(q)
    assert not stray["verified"] and "(9)" in stray["warnings"][0]


# A stand-in for main._vectors that records every statement and answers searches with chunk 2 of Acme.
def _fake_store(monkeypatch, fail=False):
    from app import main
    seen = []

    async def fake(query, *args):
        seen.append((query.split()[0], args))
        if fail:
            raise OSError("connection refused")
        return [{"doc_id": "acme", "chunk": 2}]
    monkeypatch.setattr(main, "_vectors", fake)
    monkeypatch.setattr(research, "embed", lambda texts: [[0.0] * 384 for _ in texts])  # never download the model
    return seen


def test_vector_search_sends_filters_to_sql(two_docs, monkeypatch):
    seen = _fake_store(monkeypatch)
    hits = _search("lawsuit in 2025", mode="hybrid")
    kind, args = seen[-1]
    assert kind == "SELECT" and args[2] == "structure" and args[4:7] == (None, None, "2025")
    assert hits[0]["chunk"] == 2


def test_no_database_still_answers_with_bm25(two_docs, monkeypatch, caplog):
    _fake_store(monkeypatch, fail=True)
    monkeypatch.setenv("WEALTHOS_RESEARCH_SEARCH", "hybrid")
    _fake(monkeypatch, "Acme relies on one Taiwan supplier for 70% of motor controllers [p. 3].")
    ok = _answer("What are Acme's main risks?")
    assert ok["verified"] and ok["citations"][0]["page"] == 3
    assert "keyword-only" in caplog.text


def test_settings_from_env(monkeypatch):
    monkeypatch.delenv("WEALTHOS_RESEARCH_SEARCH", raising=False)
    assert research.settings() == ("structure", "bm25", False)
    monkeypatch.setenv("WEALTHOS_RESEARCH_SEARCH", "hybrid+rerank+fixed")
    assert research.settings() == ("fixed", "hybrid", True)


def test_index_pdf_end_to_end(filing, tmp_path, monkeypatch):
    assert filing == {"doc_id": "acme", "file": "acme_10k.pdf", "doc_type": "10-K", "pages": 5}
    doc = research._docs("u", "default")[0]
    assert set(doc["chunks"]) == {"structure", "fixed"} and doc["meta"]["form"] == "10-K"
    assert all("sections_started" in p for p in doc["pages"])  # the chunker needs it to cut at a mid-page Note
    top = lambda q: _search(q)[0]["page_start"]
    assert top("What are the main risks?") == 3 and top("Is there any lawsuit?") == 4
    assert top("How did revenue do?") == 5
    assert _search("risks", filters=None) and asyncio.run(research.search("u", "other_ws", "risks")) == []

    seen = _fake_store(monkeypatch)
    monkeypatch.setenv("WEALTHOS_RESEARCH_SEARCH", "hybrid")
    research.add("u", "default", tmp_path / "acme_10k.pdf", "acme")
    (k1, _), (k2, args) = seen[:2]
    assert (k1, k2) == ("DELETE", "INSERT") and args[:3] == ("u", "default", "acme")
    assert set(args[3]) == {"structure", "fixed"}  # both chunk sets embedded
    research.delete_doc("u", "default", "acme")
    assert seen[-1][0] == "DELETE" and research.list_docs("u") == []


def test_index_saved_before_chunking_still_answers(filing, monkeypatch):
    monkeypatch.delenv("WEALTHOS_RESEARCH_SEARCH", raising=False)
    path = research._folder("u", "default") / "acme.json"
    old = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({k: old[k] for k in ("doc_id", "file", "doc_type", "pages")}), encoding="utf-8")
    assert _search("Is there any lawsuit?")[0]["page_start"] == 4
