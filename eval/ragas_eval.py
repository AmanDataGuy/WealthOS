#!/usr/bin/env python
# eval/ragas_eval.py
"""
## RAGAS Evaluation — WealthOS Phase 1

Measures retrieval quality for the filing RAG pipeline (Qdrant → Cohere → LLM).

---

### Metrics (7 total)

| Metric                  | What it checks                                              |
|-------------------------|-------------------------------------------------------------|
| `context_precision`     | Are retrieved chunks actually relevant to the question?     |
| `context_recall`        | Did retrieval miss information needed for the answer?       |
| `faithfulness`          | Does the answer use ONLY what was in the retrieved chunks?  |
| `answer_relevancy`      | Does the answer actually address the question asked?        |
| `context_entity_recall` | Are key entities (ticker, metric name) in retrieved text?   |
| `noise_sensitivity`     | Does the model get confused by irrelevant chunks mixed in?  |
| `answer_correctness`    | Does the generated answer match the reference answer?       |

---

### Two modes

| Mode           | Command                                  | When to use                      |
|----------------|------------------------------------------|----------------------------------|
| `--generate`   | `python eval/ragas_eval.py --generate`   | Once, after indexing new filings |
| eval (default) | `python eval/ragas_eval.py`              | Every time you change the RAG    |

---

### Usage

    # Step 1 — generate testset from Qdrant corpus (one-time, ~5 min)
    python eval/ragas_eval.py --generate --size 30

    # Step 2 — run full eval
    python eval/ragas_eval.py

    # Limit to specific tickers or cap questions to save cost
    python eval/ragas_eval.py --ticker AAPL NVDA --limit 10
"""

import sys
import os
import json
import asyncio
import argparse
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── Paths ──────────────────────────────────────────────────────────────────────

RESULTS_DIR  = Path(__file__).parent / "results"
TESTSET_PATH = Path(__file__).parent / "testset.json"
RESULTS_DIR.mkdir(exist_ok=True)

# ── Pass/fail thresholds ──────────────────────────────────────────────────────
# A metric below its threshold means the RAG pipeline needs work there.

THRESHOLDS = {
    "context_precision":     0.70,
    "context_recall":        0.60,
    "faithfulness":          0.75,
    "answer_relevancy":      0.70,
    "context_entity_recall": 0.60,
    "noise_sensitivity":     0.60,
    "answer_correctness":    0.60,
}


# ── LLM + Embeddings ──────────────────────────────────────────────────────────
#
# Judge LLM : Groq (free tier, no key cost)
# Embeddings: same all-MiniLM-L6-v2 model used by the Qdrant indexer,
#             so scores are comparable to what the retriever actually sees.

def _get_llm():
    from langchain_groq import ChatGroq
    from ragas.llms import LangchainLLMWrapper
    return LangchainLLMWrapper(ChatGroq(
        model="llama-3.3-70b-versatile",
        api_key=os.getenv("GROQ_API_KEY", ""),
        temperature=0,
    ))


def _get_embeddings():
    from langchain_huggingface import HuggingFaceEmbeddings
    from ragas.embeddings import LangchainEmbeddingsWrapper
    return LangchainEmbeddingsWrapper(
        HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
    )


# ── Pull docs from Qdrant ─────────────────────────────────────────────────────
#
# TestsetGenerator needs the raw filing chunks to generate questions FROM.
# We scroll the Qdrant collection and wrap each chunk as a LangChain Document.

def fetch_qdrant_docs(tickers: list[str] | None = None, max_docs: int = 500) -> list:
    """
    Scroll the `wealthos_docs` collection and return LangChain Documents.

    Args:
        tickers:  Only return chunks for these tickers (cheaper, faster).
        max_docs: Hard cap to avoid pulling the entire corpus into memory.
    """
    from langchain_core.documents import Document
    from rag.query_engine import get_qdrant_client, COLLECTION_NAME

    client = get_qdrant_client()
    docs   = []
    offset = None

    while len(docs) < max_docs:
        points, offset = client.scroll(
            collection_name=COLLECTION_NAME,
            limit=100,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            ticker  = payload.get("ticker", "")
            content = payload.get("content", "")

            # Only level-2 (sentence-level) chunks — richer density for generation
            if payload.get("chunk_level") != 2:
                continue
            if tickers and ticker not in tickers:
                continue
            if len(content) < 100:
                continue

            docs.append(Document(
                page_content=content,
                metadata={
                    "ticker":  ticker,
                    "section": payload.get("section", ""),
                    "source":  payload.get("source", ""),
                },
            ))

        if offset is None or not points:
            break

    print(f"  Fetched {len(docs)} chunks from Qdrant (collection: {COLLECTION_NAME})")
    return docs


# ── TestsetGenerator ──────────────────────────────────────────────────────────
#
# RAGAS reads the corpus and auto-generates realistic Q&A pairs.
# This removes the need to hand-write 50 test questions.
# Output is saved to eval/testset.json and reused in all future eval runs.

def generate_testset(tickers: list[str] | None = None, size: int = 30) -> list[dict]:
    """
    Auto-generate Q&A eval pairs from the Qdrant filing corpus.
    Saves to eval/testset.json. Regenerate only when corpus changes.

    Args:
        tickers: Limit to specific tickers (e.g. ["AAPL", "NVDA"]).
        size:    Number of Q&A pairs to generate. 30 is a good start.
    """
    print(f"\n## Generating testset — {size} questions")
    print("Step 1/3: Fetching docs from Qdrant...")
    docs = fetch_qdrant_docs(tickers=tickers)

    if not docs:
        raise RuntimeError(
            "No docs found in Qdrant. Index filings first:\n"
            "  python rag/pipeline.py local data/filings/AAPL_10-K.pdf AAPL 10-K"
        )

    print(f"Step 2/3: Running TestsetGenerator on {len(docs)} chunks...")

    # Try RAGAS 0.2.x first, fall back to 0.1.x
    try:
        from ragas.testset import TestsetGenerator
        generator = TestsetGenerator(
            llm=_get_llm(),
            embedding_model=_get_embeddings(),
        )
        testset = generator.generate_with_langchain_docs(docs, testset_size=size)

    except (ImportError, TypeError):
        # RAGAS 0.1.x API
        from ragas.testset.generator import TestsetGenerator
        from ragas.testset.evolutions import simple, reasoning, multi_context
        from langchain_groq import ChatGroq
        from langchain_huggingface import HuggingFaceEmbeddings

        llm = ChatGroq(
            model="llama-3.3-70b-versatile",
            api_key=os.getenv("GROQ_API_KEY", ""),
            temperature=0,
        )
        emb = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

        generator = TestsetGenerator.from_langchain(
            generator_llm=llm,
            critic_llm=llm,
            embeddings=emb,
        )
        testset = generator.generate_with_langchain_docs(
            docs,
            test_size=size,
            distributions={simple: 0.5, reasoning: 0.25, multi_context: 0.25},
        )

    print("Step 3/3: Saving testset...")
    df   = testset.to_pandas()
    rows = df.to_dict(orient="records")

    # Normalise field names — RAGAS uses different names across versions
    normalised = []
    for r in rows:
        normalised.append({
            "question":     r.get("question") or r.get("user_input", ""),
            "ground_truth": r.get("ground_truth") or r.get("reference", ""),
            "metadata":     r.get("metadata", {}),
        })

    TESTSET_PATH.write_text(
        json.dumps(normalised, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Saved {len(normalised)} questions → {TESTSET_PATH}\n")
    return normalised


# ── Load saved testset ────────────────────────────────────────────────────────

def load_testset(tickers: list[str] | None = None, limit: int | None = None) -> list[dict]:
    """Load questions from eval/testset.json (created by --generate)."""
    if not TESTSET_PATH.exists():
        raise FileNotFoundError(
            f"{TESTSET_PATH} not found.\n"
            "Generate it first:\n"
            "  python eval/ragas_eval.py --generate"
        )
    rows = json.loads(TESTSET_PATH.read_text(encoding="utf-8"))
    if tickers:
        rows = [r for r in rows if r.get("metadata", {}).get("ticker") in tickers]
    if limit:
        rows = rows[:limit]
    return rows


# ── Retrieval ─────────────────────────────────────────────────────────────────
#
# For each question, we run the actual RAG pipeline and capture:
#   answer   — the synthesized LLM response
#   contexts — the raw chunks that were retrieved (what RAGAS scores against)

async def retrieve(question: str, ticker: str | None) -> tuple[list[str], str]:
    """Run one question through the filing RAG pipeline."""
    from rag.query_engine import FilingQueryEngine, _hybrid_search_sync

    engine = FilingQueryEngine()
    try:
        answer = await engine.search(question, ticker or "") or ""
    except Exception as e:
        answer = ""
        print(f"    ⚠️  search() failed: {e}")

    try:
        hits   = await asyncio.to_thread(_hybrid_search_sync, question, ticker or "", None, 20, 5)
        chunks = [h.get("content", "") for h in hits if h.get("content")]
    except Exception:
        chunks = [answer] if answer else ["(no context retrieved)"]

    return chunks, answer


# ── RAGAS metrics ──────────────────────────────────────────────────────────────
#
# Each metric is imported individually — if one is missing in an older RAGAS
# version we skip it gracefully instead of crashing the whole eval.

def _load_metrics() -> dict:
    """Return all available RAGAS metric instances keyed by name."""
    from ragas.metrics import context_precision, context_recall, faithfulness, answer_relevancy

    metrics = {
        "context_precision": context_precision,
        "context_recall":    context_recall,
        "faithfulness":      faithfulness,
        "answer_relevancy":  answer_relevancy,
    }

    optional = {
        "context_entity_recall": "ragas.metrics.context_entity_recall",
        "noise_sensitivity":     "ragas.metrics.noise_sensitivity",
        "answer_correctness":    "ragas.metrics.answer_correctness",
    }
    for name, import_path in optional.items():
        module, attr = import_path.rsplit(".", 1)
        try:
            import importlib
            metrics[name] = getattr(importlib.import_module(module), attr)
        except (ImportError, AttributeError):
            print(f"  ⚠️  {name} not available — update ragas: pip install -U ragas")

    return metrics


# ── Main eval loop ────────────────────────────────────────────────────────────

async def run_eval(tickers: list[str] | None = None, limit: int | None = None) -> dict:
    """
    Load testset → run RAG pipeline for each question → score with RAGAS.
    Returns summary: {metric_name: {score, threshold, passed}}.
    """
    try:
        from ragas import evaluate
        from datasets import Dataset
    except ImportError:
        raise ImportError("Run: pip install ragas datasets")

    rows = load_testset(tickers=tickers, limit=limit)
    print(f"\n## RAGAS Evaluation — {len(rows)} questions\n")

    # Run RAG pipeline for every question
    eval_rows = []
    for i, row in enumerate(rows):
        question = row["question"]
        ticker   = (row.get("metadata") or {}).get("ticker")
        print(f"  [{i+1}/{len(rows)}] [{ticker or '—'}] {question[:65]}...")

        chunks, answer = await retrieve(question, ticker)
        eval_rows.append({
            "question":     question,
            "answer":       answer,
            "contexts":     chunks,
            "ground_truth": row.get("ground_truth", ""),
        })

    # Score with RAGAS
    metrics = _load_metrics()
    print(f"\nScoring with {len(metrics)} metrics...\n")

    result = evaluate(
        Dataset.from_list(eval_rows),
        metrics=list(metrics.values()),
        llm=_get_llm(),
        embeddings=_get_embeddings(),
    )

    # Print results table
    print(f"\n{'─'*65}")
    print("## Results\n")
    summary  = {}
    all_pass = True

    for name, threshold in THRESHOLDS.items():
        if name not in result:
            continue
        score    = float(result[name])
        passed   = score >= threshold
        all_pass = all_pass and passed
        status   = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {name:<28}  {score:.4f}  (min {threshold})  {status}")
        summary[name] = {"score": round(score, 4), "threshold": threshold, "passed": passed}

    print(f"\n{'─'*65}")
    print(f"  Overall: {'✅ ALL PASS' if all_pass else '❌ SOME METRICS BELOW THRESHOLD'}\n")

    # Save full results for CI and historical comparison
    out = RESULTS_DIR / f"ragas_{date.today().isoformat()}.json"
    out.write_text(
        json.dumps({"date": str(date.today()), "summary": summary, "rows": eval_rows},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  Results saved → {out}\n")
    return summary


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAGAS evaluation for WealthOS RAG pipeline")
    parser.add_argument(
        "--generate", action="store_true",
        help="Pull docs from Qdrant and auto-generate eval/testset.json (run once per corpus change)",
    )
    parser.add_argument(
        "--size", type=int, default=30,
        help="Number of Q&A pairs to generate (default: 30)",
    )
    parser.add_argument(
        "--ticker", nargs="+", default=None,
        help="Limit to specific tickers, e.g. --ticker AAPL NVDA",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Cap number of questions during eval to save cost, e.g. --limit 10",
    )
    args = parser.parse_args()

    if args.generate:
        generate_testset(tickers=args.ticker, size=args.size)
    else:
        asyncio.run(run_eval(tickers=args.ticker, limit=args.limit))
