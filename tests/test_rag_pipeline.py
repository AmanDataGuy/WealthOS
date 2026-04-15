# tests/test_rag_pipeline.py
# Phase 2 RAG tests
# Run AFTER indexing at least one real filing.
#
# Quick start:
#   1. Index a filing first:
#      python rag/pipeline.py local data/filings/AAPL_10-K.pdf AAPL 10-K
#   2. Then run tests:
#      pytest tests/test_rag_pipeline.py -v

import asyncio
import pytest
from rag.query_engine import FilingQueryEngine

# ── Test queries per ticker ───────────────────────────────────────────────────
TEST_QUERIES = [
    ("What is the company's total revenue for the latest fiscal year?", "AAPL"),
    ("What risks did management highlight in this filing?",             "AAPL"),
    ("Describe the company's debt and long-term obligations.",          "AAPL"),
    ("What is the capital expenditure guidance?",                       "AAPL"),
    ("What segments does the company operate in?",                      "AAPL"),
    ("What is the company's total revenue for the latest fiscal year?", "MSFT"),
    ("What risks did management highlight in this filing?",             "MSFT"),
    ("What is the company's total revenue for the latest fiscal year?", "GOOGL"),
    ("Describe the company's cloud business revenue.",                  "GOOGL"),
    ("What is the company's headcount or employee count?",              "GOOGL"),
]

MINIMUM_SCORE    = 0.5   # retrieval relevance threshold
MINIMUM_SOURCES  = 1     # at least 1 source chunk returned


@pytest.fixture(scope="module")
def engine():
    return FilingQueryEngine()


@pytest.mark.asyncio
@pytest.mark.parametrize("question,ticker", TEST_QUERIES)
async def test_rag_returns_answer(engine, question, ticker):
    result = await engine.query(question, ticker=ticker)

    assert result["answer"], f"Empty answer for [{ticker}] '{question}'"
    assert "could not find" not in result["answer"].lower() or len(result["sources"]) > 0, \
        f"No sources and no answer for [{ticker}] '{question}'"


@pytest.mark.asyncio
@pytest.mark.parametrize("question,ticker", TEST_QUERIES)
async def test_rag_has_sources(engine, question, ticker):
    result = await engine.query(question, ticker=ticker)

    assert len(result["sources"]) >= MINIMUM_SOURCES, \
        f"Expected >= {MINIMUM_SOURCES} sources, got {len(result['sources'])} for [{ticker}]"


@pytest.mark.asyncio
@pytest.mark.parametrize("question,ticker", TEST_QUERIES)
async def test_rag_source_scores(engine, question, ticker):
    result = await engine.query(question, ticker=ticker)

    if result["sources"]:
        top_score = result["sources"][0]["score"]
        assert top_score >= MINIMUM_SCORE, \
            f"Top score {top_score} below threshold {MINIMUM_SCORE} for [{ticker}] '{question}'"


@pytest.mark.asyncio
async def test_rag_ticker_filter(engine):
    """Ensure ticker filter works — AAPL results shouldn't contain MSFT chunks."""
    result = await engine.query("What is total revenue?", ticker="AAPL")
    for source in result["sources"]:
        assert source["ticker"] == "AAPL", \
            f"Got source from {source['ticker']} when filtering for AAPL"


@pytest.mark.asyncio
async def test_rag_no_ticker_filter(engine):
    """Query without ticker filter should still return results."""
    result = await engine.query("What is total revenue?", ticker=None)
    assert result["answer"]
    assert len(result["sources"]) > 0


@pytest.mark.asyncio
async def test_rag_empty_db_graceful(engine):
    """Query for a ticker that hasn't been indexed should fail gracefully."""
    result = await engine.query("What is revenue?", ticker="FAKE_TICKER_XYZ")
    assert "No relevant documents found" in result["answer"] or result["sources"] == []