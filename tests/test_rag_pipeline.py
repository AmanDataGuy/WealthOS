# tests/test_rag_pipeline.py
"""
## RAG Pipeline Tests

Two layers of tests:

### Layer 1 — Sanity tests (always run in CI)
Basic assertions that the retrieval pipeline returns something useful.
No LLM calls. Fast (<10s per query).

### Layer 2 — RAGAS score tests (run after generating testset)
Loads the last RAGAS eval results from eval/results/ and asserts
each metric meets its threshold. No LLM calls — reads saved JSON.
Skipped automatically if no results file exists yet.

---

### Setup

    # Index at least one filing first:
    python rag/pipeline.py local data/filings/AAPL_10-K.pdf AAPL 10-K

    # Generate testset (one-time):
    python eval/ragas_eval.py --generate

    # Run full RAGAS eval (saves results to eval/results/):
    python eval/ragas_eval.py

    # Then run these tests:
    pytest tests/test_rag_pipeline.py -v
"""

import json
import asyncio
import pytest
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────────

MIN_SOURCES = 1   # minimum chunks expected back from hybrid search

# RAGAS pass thresholds — must match THRESHOLDS in eval/ragas_eval.py
RAGAS_THRESHOLDS = {
    "context_precision":     0.70,
    "context_recall":        0.60,
    "faithfulness":          0.75,
    "answer_relevancy":      0.70,
    "context_entity_recall": 0.60,
    "noise_sensitivity":     0.60,
    "answer_correctness":    0.60,
}

RESULTS_DIR = Path(__file__).parent.parent / "eval" / "results"

# Test queries — cover different question types across well-indexed tickers
TEST_QUERIES = [
    ("What are the main risk factors for the company?",              "AAPL"),
    ("What is the total revenue for the latest fiscal year?",        "AAPL"),
    ("How does the company describe its competitive landscape?",     "AAPL"),
    ("What is the company's free cash flow and capital allocation?", "AAPL"),
    ("What segments does the company operate in?",                   "AAPL"),
    ("What risks did management highlight in this filing?",          "MSFT"),
    ("What is Azure revenue growth and competitive positioning?",    "MSFT"),
    ("What is total revenue for the latest fiscal year?",            "GOOGL"),
    ("How does the company describe its cloud business?",            "GOOGL"),
    ("What are the key risks in the advertising business?",          "GOOGL"),
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _latest_ragas_results() -> dict | None:
    """Return summary from the most recent RAGAS results file, or None."""
    files = sorted(RESULTS_DIR.glob("ragas_*.json"), reverse=True)
    if not files:
        return None
    return json.loads(files[0].read_text(encoding="utf-8")).get("summary", {})


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def engine():
    """Shared FilingQueryEngine instance for all tests in this module."""
    from rag.query_engine import FilingQueryEngine
    return FilingQueryEngine()


# ══════════════════════════════════════════════════════════════════════════════
# Layer 0 — Earnings call transcript extraction (deterministic, no network/LLM)
#
# Fixture markdown mirrors the real structure of a scraped fool.com transcript
# page (verified live 2026-08-19): site chrome, then a metadata section, then
# "## Full Conference Call Transcript", then the real dialogue, then a
# "## Read Next" related-content footer.
# ══════════════════════════════════════════════════════════════════════════════

_FOOL_PAGE_FIXTURE = """[Accessibility Menu](https://www.fool.com/#)

[Arrow-Thin-Down\\\\\\nNVDA\\\\\\n$219.99](https://www.fool.com/quote/nasdaq/nvda/)

## DATE
2026-05-20

## CALL PARTICIPANTS
Jensen Huang — CEO

## TAKEAWAYS
- Revenue up 85% year over year.

## Full Conference Call Transcript
**Operator:** Good afternoon, welcome to the earnings call.

**Jensen Huang:** Thank you. We delivered an exceptional quarter with record revenue.

**Operator:** This concludes today's conference call. You may now disconnect.

## Read Next
### Stocks Mentioned
[Nvidia Stock Quote](https://www.fool.com/quote/nasdaq/nvda/)

## Premium Investing Services
Sign up for Stock Advisor today.
"""


def test_extract_transcript_body_isolates_real_content():
    """Extraction must return only the dialogue, not chrome/nav or the footer."""
    from rag.earnings_indexer import _extract_transcript_body
    body = _extract_transcript_body(_FOOL_PAGE_FIXTURE)

    assert "Jensen Huang:" in body
    assert "record revenue" in body
    # Chrome/nav content before the transcript marker must be excluded
    assert "Accessibility Menu" not in body
    assert "TAKEAWAYS" not in body
    # Footer boilerplate after "## Read Next" must be excluded
    assert "Stocks Mentioned" not in body
    assert "Premium Investing Services" not in body


def test_extract_transcript_body_falls_back_when_markers_missing():
    """If the page structure changes and markers aren't found, return the raw text rather than empty."""
    from rag.earnings_indexer import _extract_transcript_body
    raw = "Some unexpected page content with no known markers."
    assert _extract_transcript_body(raw) == raw


# ══════════════════════════════════════════════════════════════════════════════
# Layer 1 — Sanity tests (no LLM calls, always run in CI)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@pytest.mark.parametrize("question,ticker", TEST_QUERIES)
async def test_rag_returns_answer(engine, question, ticker):
    """Pipeline must return a non-empty answer for every test query."""
    answer = await engine.search(question, ticker)
    assert answer, f"Empty answer for [{ticker}] '{question}'"
    assert "RAG error" not in answer, f"RAG error for [{ticker}]: {answer}"


@pytest.mark.asyncio
@pytest.mark.parametrize("question,ticker", TEST_QUERIES)
async def test_rag_retrieves_chunks(question, ticker):
    """Hybrid search must return at least one source chunk."""
    from rag.query_engine import _hybrid_search_sync
    hits = await asyncio.to_thread(_hybrid_search_sync, question, ticker, None, 10, 5)
    assert len(hits) >= MIN_SOURCES, (
        f"Got {len(hits)} chunks for [{ticker}] '{question}', expected >= {MIN_SOURCES}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("question,ticker", TEST_QUERIES)
async def test_chunks_have_content(question, ticker):
    """Every retrieved chunk must have non-empty content."""
    from rag.query_engine import _hybrid_search_sync
    hits = await asyncio.to_thread(_hybrid_search_sync, question, ticker, None, 10, 5)
    for hit in hits:
        assert hit.get("content"), f"Empty content in a chunk for [{ticker}]"


@pytest.mark.asyncio
async def test_ticker_filter_is_respected():
    """Chunks returned for AAPL must not contain chunks from other tickers."""
    from rag.query_engine import _hybrid_search_sync
    hits = await asyncio.to_thread(_hybrid_search_sync, "What is total revenue?", "AAPL", None, 20, 5)
    for hit in hits:
        assert hit.get("ticker") == "AAPL", (
            f"Got chunk from ticker={hit.get('ticker')} when filtering for AAPL"
        )


@pytest.mark.asyncio
async def test_unknown_ticker_fails_gracefully():
    """Querying an un-indexed ticker must return empty results, not crash."""
    from rag.query_engine import _hybrid_search_sync
    hits = await asyncio.to_thread(_hybrid_search_sync, "What is revenue?", "FAKE_XYZ_999", None, 10, 5)
    assert hits == [], f"Expected [] for unknown ticker, got {len(hits)} hits"


# ══════════════════════════════════════════════════════════════════════════════
# Layer 2 — RAGAS score tests
#
# These load the saved JSON from the last `python eval/ragas_eval.py` run.
# No LLM calls — just reads a file and asserts thresholds.
# Automatically skipped if no results file exists yet.
# ══════════════════════════════════════════════════════════════════════════════

_ragas_skip = pytest.mark.skipif(
    _latest_ragas_results() is None,
    reason=(
        "No RAGAS results found. Run:\n"
        "  python eval/ragas_eval.py --generate\n"
        "  python eval/ragas_eval.py"
    ),
)


@pytest.fixture(scope="module")
def ragas_results():
    return _latest_ragas_results() or {}


@_ragas_skip
def test_ragas_context_precision(ragas_results):
    """Retrieved chunks must be relevant to the question (≥ 0.70)."""
    m = "context_precision"
    if m not in ragas_results:
        pytest.skip(f"{m} not in results")
    assert ragas_results[m]["score"] >= RAGAS_THRESHOLDS[m], (
        f"{m}: {ragas_results[m]['score']:.4f} < {RAGAS_THRESHOLDS[m]}"
    )


@_ragas_skip
def test_ragas_context_recall(ragas_results):
    """Retrieval must not miss important information (≥ 0.60)."""
    m = "context_recall"
    if m not in ragas_results:
        pytest.skip(f"{m} not in results")
    assert ragas_results[m]["score"] >= RAGAS_THRESHOLDS[m], (
        f"{m}: {ragas_results[m]['score']:.4f} < {RAGAS_THRESHOLDS[m]}"
    )


@_ragas_skip
def test_ragas_faithfulness(ragas_results):
    """Answer must stay grounded in retrieved chunks only (≥ 0.75)."""
    m = "faithfulness"
    if m not in ragas_results:
        pytest.skip(f"{m} not in results")
    assert ragas_results[m]["score"] >= RAGAS_THRESHOLDS[m], (
        f"{m}: {ragas_results[m]['score']:.4f} < {RAGAS_THRESHOLDS[m]}"
    )


@_ragas_skip
def test_ragas_answer_relevancy(ragas_results):
    """Answer must actually address the question asked (≥ 0.70)."""
    m = "answer_relevancy"
    if m not in ragas_results:
        pytest.skip(f"{m} not in results")
    assert ragas_results[m]["score"] >= RAGAS_THRESHOLDS[m], (
        f"{m}: {ragas_results[m]['score']:.4f} < {RAGAS_THRESHOLDS[m]}"
    )


@_ragas_skip
def test_ragas_context_entity_recall(ragas_results):
    """Key entities (ticker, metric names) must appear in retrieved chunks (≥ 0.60)."""
    m = "context_entity_recall"
    if m not in ragas_results:
        pytest.skip(f"{m} not in results")
    assert ragas_results[m]["score"] >= RAGAS_THRESHOLDS[m], (
        f"{m}: {ragas_results[m]['score']:.4f} < {RAGAS_THRESHOLDS[m]}"
    )


@_ragas_skip
def test_ragas_noise_sensitivity(ragas_results):
    """Model must stay accurate when irrelevant chunks are mixed in (≥ 0.60)."""
    m = "noise_sensitivity"
    if m not in ragas_results:
        pytest.skip(f"{m} not in results")
    assert ragas_results[m]["score"] >= RAGAS_THRESHOLDS[m], (
        f"{m}: {ragas_results[m]['score']:.4f} < {RAGAS_THRESHOLDS[m]}"
    )


@_ragas_skip
def test_ragas_answer_correctness(ragas_results):
    """Generated answer must match the reference answer (≥ 0.60)."""
    m = "answer_correctness"
    if m not in ragas_results:
        pytest.skip(f"{m} not in results")
    assert ragas_results[m]["score"] >= RAGAS_THRESHOLDS[m], (
        f"{m}: {ragas_results[m]['score']:.4f} < {RAGAS_THRESHOLDS[m]}"
    )
