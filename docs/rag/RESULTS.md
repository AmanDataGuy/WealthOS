# Filing Q&A retrieval: results

Every number on this page is copied from `backend/evals/results_rag.json`; the `<!-- src: -->` comment after it names
the JSON key. Command (from `backend/`, needs the two filings from `python evals/sets/fetch_filings.py`):
`python evals/sets/run_rag.py`. No API keys and no model calls: embeddings and the re-ranker run locally on the CPU.

## Set and setup

- 81 questions <!-- src: backend/evals/results_rag.json#set.questions --> on Apple's fiscal 2025 and NVIDIA's fiscal
  2026 10-K, 66 answerable <!-- src: backend/evals/results_rag.json#set.answerable -->, each labelled with every page
  that answers it (`backend/evals/sets/rag_golden.yaml`). The rest cannot be answered from the filings.
- Both filings are indexed into one workspace, so search also has to pick the right company.
- Embeddings: `BAAI/bge-small-en-v1.5` <!-- src: backend/evals/results_rag.json#embed_model -->, 384 dimensions
  <!-- src: backend/evals/results_rag.json#embed_dims -->. Re-ranker: `BAAI/bge-reranker-base`
  <!-- src: backend/evals/results_rag.json#rerank_model --> over the fused top 20, keeping the top 5.
- Chunks: structure-aware (cut at every Item, Part and Note, about 350 words) gives 135 and 207
  <!-- src: backend/evals/results_rag.json#chunk_counts.structure -->; fixed 350-word windows with a 50-word overlap
  give 107 and 171 <!-- src: backend/evals/results_rag.json#chunk_counts.fixed --> (Apple, NVIDIA).
- recall@5: a labelled page is inside one of the top 5 hits (a hit covers its pages, first to last). MRR: 1 / rank of
  the first such hit. Both on the 66 answerable questions.

## Retrieval

| Arm | recall@5 | MRR | p50 ms | p95 ms |
|---|---|---|---|---|
| Page-level BM25 (the app before this change) | 0.894 <!-- src: backend/evals/results_rag.json#arms.pages_bm25_before.recall_at_5 --> | 0.764 <!-- src: #arms.pages_bm25_before.mrr --> | 35.5 | 53.3 <!-- src: #arms.pages_bm25_before.p50_ms, p95_ms --> |
| BM25, structure chunks (app default) | **1.000** <!-- src: #arms.bm25/structure.recall_at_5 --> | 0.873 <!-- src: #arms.bm25/structure.mrr --> | 43.0 | 66.8 <!-- src: #arms.bm25/structure.p50_ms, p95_ms --> |
| BM25, fixed chunks | **1.000** <!-- src: #arms.bm25/fixed.recall_at_5 --> | **0.895** <!-- src: #arms.bm25/fixed.mrr --> | 39.0 | 65.2 <!-- src: #arms.bm25/fixed.p50_ms, p95_ms --> |
| Vector, structure | 0.773 <!-- src: #arms.vector/structure.recall_at_5 --> | 0.687 <!-- src: #arms.vector/structure.mrr --> | 45.3 | 58.8 <!-- src: #arms.vector/structure.p50_ms, p95_ms --> |
| Vector, fixed | 0.803 <!-- src: #arms.vector/fixed.recall_at_5 --> | 0.676 <!-- src: #arms.vector/fixed.mrr --> | 49.0 | 87.5 <!-- src: #arms.vector/fixed.p50_ms, p95_ms --> |
| Hybrid (RRF), structure | 0.939 <!-- src: #arms.hybrid/structure.recall_at_5 --> | 0.815 <!-- src: #arms.hybrid/structure.mrr --> | 43.6 | 74.7 <!-- src: #arms.hybrid/structure.p50_ms, p95_ms --> |
| Hybrid (RRF), fixed | 0.955 <!-- src: #arms.hybrid/fixed.recall_at_5 --> | 0.845 <!-- src: #arms.hybrid/fixed.mrr --> | 52.8 | 111.7 <!-- src: #arms.hybrid/fixed.p50_ms, p95_ms --> |
| Hybrid + re-rank, structure | 0.985 <!-- src: #arms.hybrid+rerank/structure.recall_at_5 --> | 0.814 <!-- src: #arms.hybrid+rerank/structure.mrr --> | 8404.6 | 10307.2 <!-- src: #arms.hybrid+rerank/structure.p50_ms, p95_ms --> |
| Hybrid + re-rank, fixed | 0.939 <!-- src: #arms.hybrid+rerank/fixed.recall_at_5 --> | 0.796 <!-- src: #arms.hybrid+rerank/fixed.mrr --> | 6635.8 | 9121.6 <!-- src: #arms.hybrid+rerank/fixed.p50_ms, p95_ms --> |

Times include embedding the question; they were taken on a laptop CPU with other services running.

What this shows:
- **Chunking helped.** Moving BM25 from whole pages to chunks raised recall@5 from 0.894 to 1.000 and MRR from 0.764
  to 0.873 (structure) or 0.895 (fixed), on the same questions.
- **Vector search, hybrid search and re-ranking did not beat BM25** on this set. Vector-only finds the page for about
  three questions in four. Fusing it with BM25 loses recall against BM25 alone. Re-ranking wins some of that back on
  structure chunks and loses a little on fixed chunks, at 8.4 s and 6.6 s median per query on a CPU. A likely reason:
  questions about a 10-K use the filing's own terms ("segments", "Epic Games", "commercial paper"), which keyword
  ranking rewards. This set was not built to favour paraphrased questions, where embeddings usually help.
- Structure and fixed chunks tie on recall@5. Fixed is ahead on MRR by 0.022, about one question reaching rank 1 in
  66, inside run-to-run noise for a set this size. The app keeps structure chunks: one never crosses an Item or
  Note, so the section a citation names is right, while a fixed window can straddle two sections.

Caveat: the set is 66 answerable questions from two filings. One question moves recall@5 by about 1.5 points.

## Refusing unanswerable questions by retrieval score

Each arm's cut-off on its best top-5 score was chosen on a dev third of the questions (seed 20261115) and is reported
on the other 54 <!-- src: backend/evals/results_rag.json#refusal.test_questions --> (10 unanswerable, 44 answerable).

| Arm | Unanswerable refused | Answerable wrongly refused |
|---|---|---|
| BM25, structure | 2 of 10 <!-- src: #refusal.test.bm25/structure.unanswerable_refused --> | 0 of 44 <!-- src: #refusal.test.bm25/structure.answerable_refused --> |
| BM25, fixed | 1 of 10 <!-- src: #refusal.test.bm25/fixed.unanswerable_refused --> | 0 of 44 <!-- src: #refusal.test.bm25/fixed.answerable_refused --> |
| Vector, structure | 1 of 10 <!-- src: #refusal.test.vector/structure.unanswerable_refused --> | 1 of 44 <!-- src: #refusal.test.vector/structure.answerable_refused --> |
| Hybrid, structure | 5 of 10 <!-- src: #refusal.test.hybrid/structure.unanswerable_refused --> | 4 of 44 <!-- src: #refusal.test.hybrid/structure.answerable_refused --> |
| Hybrid + re-rank, structure | 2 of 10 <!-- src: #refusal.test.hybrid+rerank/structure.unanswerable_refused --> | 1 of 44 <!-- src: #refusal.test.hybrid+rerank/structure.answerable_refused --> |
| Hybrid + re-rank, fixed | 3 of 10 <!-- src: #refusal.test.hybrid+rerank/fixed.unanswerable_refused --> | 0 of 44 <!-- src: #refusal.test.hybrid+rerank/fixed.answerable_refused --> |

A retrieval score does not tell an unanswerable question from an answerable one: the search always finds text on the
same topic. The app therefore keeps only a low BM25 floor (`research.REFUSE_BM25`). BM25 scores grow with the number of
documents, so the tuned cut-off (4.643 for BM25 on structure chunks
<!-- src: backend/evals/results_rag.json#thresholds.bm25/structure.threshold -->) would refuse good questions in a
workspace with one small filing. Refusing what the filings do not say is left to the answer step, which is told to say
so, and is measured by the live run below.

## Not measured yet

- Answer accuracy, Ragas faithfulness and context precision, refusals in the answer step, number-check rejects,
  end-to-end latency and cost per question: `python evals/sets/run_rag.py --live` (Groq `openai/gpt-oss-120b`,
  spend capped at $1), written to `backend/evals/results_rag_answers.json`.
- pgvector: the eval ranks chunk vectors in memory (cosine over unit vectors, the order pgvector returns). The
  Postgres query path is covered by tests with a fake store only.

## CI

`backend/tests/test_rag_eval.py` re-runs the retrieval arms on the real filings and fails if any arm's recall@5 is
below the committed value. The re-ranked arms are left out of CI (over half an hour on a CPU); their numbers come from
the full local run. The workflow fetches the filings from SEC EDGAR on a cache miss (repo secret `SEC_USER_AGENT`) and
caches them with the models; without the secret those tests skip.
