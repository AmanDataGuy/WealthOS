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

# Evaluation questions with human-written ground truth answers.
#
# Ground truths written for tickers with strong Qdrant coverage (180+ chunks).
# Indian tickers (TCS, INFY) have ~8 chunks from yfinance HTML —
# context_recall will be low there by design; they're included to surface that gap.
#
# Qdrant chunk counts (approx): NVDA=287, GOOGL=260, MSFT=252, TSLA=282,
#                                AMZN=184, AAPL=181, Indian stocks ~8 each
EVAL_QUESTIONS = [
    # ── US tickers — well-indexed, expect reasonable scores ──────────────────
    {
        "question": "How does NVIDIA describe the competition risks in its annual filings?",
        "ticker": "NVDA",
        "ground_truth": (
            "NVIDIA cites competition from AMD, Intel, and custom AI chips designed by "
            "cloud hyperscalers (Google TPUs, Amazon Trainium, Microsoft Maia) as key "
            "competitive threats. It also notes export restrictions to China as a risk "
            "to its addressable market, and that customers may shift to alternative "
            "architectures if NVIDIA's high market share in data center GPUs erodes."
        ),
    },
    {
        "question": "What is Microsoft's Azure revenue growth and competitive positioning?",
        "ticker": "MSFT",
        "ground_truth": (
            "Microsoft describes Azure as its fastest-growing segment with cloud revenue "
            "growing over 20% year-over-year. Azure competes primarily with AWS and "
            "Google Cloud. Microsoft highlights hybrid cloud integration with enterprise "
            "software (Office 365, Dynamics) as a key differentiator and cites Azure "
            "OpenAI services as a major growth driver going forward."
        ),
    },
    {
        "question": "What risks does Apple cite related to China in its 10-K?",
        "ticker": "AAPL",
        "ground_truth": (
            "Apple identifies China as both a key manufacturing location and a major "
            "revenue market. Risks cited include trade tensions and tariffs, supply chain "
            "disruption from reliance on Chinese manufacturing partners, regulatory "
            "restrictions on app distribution in China, and competition from local "
            "smartphone brands including Huawei."
        ),
    },
    {
        "question": "What is Amazon's stated strategy for AWS margin expansion?",
        "ticker": "AMZN",
        "ground_truth": (
            "Amazon states AWS margin expansion is driven by custom silicon (Graviton "
            "processors, Trainium for AI training), economies of scale in infrastructure, "
            "and a shift toward higher-margin services like AI, databases, and security. "
            "AWS operating margin is significantly higher than the retail segment and is "
            "described as the primary profit engine for the company."
        ),
    },
    {
        "question": "What are the key risks in Google's advertising business per its 10-K?",
        "ticker": "GOOGL",
        "ground_truth": (
            "Google cites advertiser concentration risk, competition from Meta, Amazon, "
            "and TikTok for digital ad budgets, and regulatory antitrust scrutiny as key "
            "risks. The shift from desktop to mobile and the rise of AI-powered search "
            "alternatives are noted as structural risks to traditional search ad revenue."
        ),
    },
    # ── Indian tickers — thin coverage (~8 chunks), expect low recall ─────────
    {
        "question": "What is the revenue growth trend for TCS over the past 3 years?",
        "ticker": "TCS.NS",
        "ground_truth": (
            "TCS reported revenue growth of 8-12% annually in INR terms, driven by "
            "digital transformation deals. Growth slowed in FY2024 due to client budget "
            "caution in the US and Europe. BFSI and retail verticals were under pressure "
            "while manufacturing showed resilience."
        ),
    },
    {
        "question": "What is Infosys's AI strategy according to its management discussion?",
        "ticker": "INFY.NS",
        "ground_truth": (
            "Infosys describes its AI strategy through its Topaz platform for enterprise "
            "generative AI services. The company targets AI-led cost takeout deals and "
            "AI-augmented software delivery, with partnerships with major cloud providers "
            "and LLM vendors to deliver AI transformation programs."
        ),
    },
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
            "question":     q,
            "answer":       answer,
            "contexts":     chunks if chunks else ["(no context retrieved)"],
            "ground_truth": item["ground_truth"],
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
