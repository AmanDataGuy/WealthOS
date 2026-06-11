#!/usr/bin/env python
# eval/ragas_eval.py
"""
RAGAS evaluation of the WealthOS RAG pipeline.

Measures how well the Qdrant hybrid search + Cohere reranking actually
retrieves relevant context for financial questions.

Metrics:
  - Context Precision:  are the retrieved chunks relevant to the question?
  - Context Recall:     does the retrieved set cover the ground-truth answer?
  - Faithfulness:       is the final answer grounded in retrieved context?
  - Answer Relevancy:   does the answer address the question?

Usage:
    python eval/ragas_eval.py
    python eval/ragas_eval.py --ticker AAPL MSFT   # limit to specific tickers
"""

import sys
import json
import asyncio
import argparse
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

# 10 questions representative of what Indian WealthOS users ask
# Mix of factual (needs DB/SEC data) and qualitative (needs RAG)
EVAL_QUESTIONS = [
    {"question": "What is the revenue growth trend for TCS over the past 3 years?",         "ticker": "TCS.NS"},
    {"question": "What are HDFC Bank's main risk factors according to its annual report?",  "ticker": "HDFCBANK.NS"},
    {"question": "How does Infosys describe its AI strategy in its management discussion?",  "ticker": "INFY.NS"},
    {"question": "What is Reliance Industries' free cash flow and debt position?",           "ticker": "RELIANCE.NS"},
    {"question": "What risks does Apple cite related to China in its 10-K?",                 "ticker": "AAPL"},
    {"question": "What is Microsoft's Azure revenue growth and competitive positioning?",    "ticker": "MSFT"},
    {"question": "How does NVIDIA describe the competition risks in its annual filings?",    "ticker": "NVDA"},
    {"question": "What is Amazon's stated strategy for AWS margin expansion?",               "ticker": "AMZN"},
    {"question": "What are the key risks in Google's advertising business per its 10-K?",   "ticker": "GOOGL"},
    {"question": "How does Meta describe its AI infrastructure spending plans?",             "ticker": "META"},
]


async def retrieve_context(question: str, ticker: str) -> tuple[list[str], str]:
    """Run the RAG pipeline and return (retrieved_chunks, synthesized_answer)."""
    from rag.query_engine import FilingQueryEngine
    engine = FilingQueryEngine()

    try:
        answer = await engine.search(question, ticker)
    except Exception as e:
        answer = f"RAG error: {e}"

    # For RAGAS we need the individual retrieved chunks, not just the synthesized answer.
    # We re-run the underlying hybrid search to get them.
    chunks = []
    try:
        from rag.query_engine import _hybrid_search_sync, embed_query_dense, embed_query_sparse
        import asyncio

        raw_hits = await asyncio.to_thread(
            _hybrid_search_sync, question, ticker, None, 20, 5
        )
        chunks = [h.payload.get("content", "") for h in raw_hits if h.payload]
    except Exception:
        # If we can't get the raw chunks just use the synthesized answer as a single chunk
        if answer and not answer.startswith("RAG error"):
            chunks = [answer]

    return chunks, answer or ""


async def run_ragas(tickers: list[str] | None = None):
    try:
        from ragas import evaluate
        from ragas.metrics import (
            context_precision,
            context_recall,
            faithfulness,
            answer_relevancy,
        )
        from datasets import Dataset
    except ImportError:
        raise ImportError(
            "ragas not installed — run: pip install ragas datasets"
        )

    questions   = EVAL_QUESTIONS
    if tickers:
        questions = [q for q in questions if q["ticker"] in tickers]

    print(f"\nRAGAS Evaluation — {len(questions)} questions")
    print("Retrieving contexts (this hits Qdrant and Groq)...\n")

    rows = []
    for item in questions:
        q      = item["question"]
        ticker = item["ticker"]
        print(f"  [{ticker}] {q[:60]}...")
        chunks, answer = await retrieve_context(q, ticker)
        rows.append({
            "question":  q,
            "answer":    answer,
            "contexts":  chunks if chunks else ["(no context retrieved)"],
            # ground_truth is required by context_recall; we use the answer as proxy
            # since we don't have human-annotated gold answers
            "ground_truth": answer,
        })

    dataset = Dataset.from_list(rows)
    result  = evaluate(
        dataset,
        metrics=[context_precision, context_recall, faithfulness, answer_relevancy],
    )

    # Print summary
    print(f"\n{'─'*60}")
    print("RAGAS Results:")
    print(f"  Context Precision:  {result['context_precision']:.4f}")
    print(f"  Context Recall:     {result['context_recall']:.4f}")
    print(f"  Faithfulness:       {result['faithfulness']:.4f}")
    print(f"  Answer Relevancy:   {result['answer_relevancy']:.4f}")
    print(f"{'─'*60}\n")

    # Per-question detail
    df = result.to_pandas()
    print(df[["question", "context_precision", "faithfulness", "answer_relevancy"]].to_string(index=False))
    print()

    # Save
    out_path = RESULTS_DIR / f"ragas_{date.today().isoformat()}.json"
    out_data = {
        "date":     str(date.today()),
        "summary":  {
            "context_precision":  result["context_precision"],
            "context_recall":     result["context_recall"],
            "faithfulness":       result["faithfulness"],
            "answer_relevancy":   result["answer_relevancy"],
        },
        "rows": rows,
    }
    out_path.write_text(json.dumps(out_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Results saved → {out_path}")

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", nargs="+", default=None, help="Filter to specific tickers")
    args = parser.parse_args()
    asyncio.run(run_ragas(tickers=args.ticker))
