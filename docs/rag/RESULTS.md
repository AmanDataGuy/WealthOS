# Filing Q&A: results

Every number on this page is copied from a committed result file in `backend/evals/`; the `<!-- src: -->` comment
after it names the file and JSON key. Commands run from `backend/` and need the two filings from
`python evals/sets/fetch_filings.py` (Apple fiscal 2025 and NVIDIA fiscal 2026 10-K, real SEC filings).

| File | What | Command |
|---|---|---|
| `results_rag.json` | Retrieval, easy set, every arm | `python evals/sets/run_rag.py` |
| `results_rag_hard.json` | Retrieval, hard set, every arm | `python evals/sets/run_rag.py --set hard` |
| `results_rag_pgvector.json`, `results_rag_pgvector_hard.json` | The same searches through Postgres + pgvector | `python evals/sets/run_rag_pgvector.py [--set evals/sets/rag_hard.yaml]` |
| `results_rag_answers.json` | Live answers and Ragas, easy set | `python evals/sets/run_rag.py --live --set easy --provider openrouter --arms ...` |
| `results_rag_answers_before_cite_fix.json` | The same live answers before the citation fix (section 4) | the same, at commit 57b11d0 |
| `results_rag_hard_answers.json` | Live answers, hard set | `python evals/sets/run_rag.py --live --set hard --arms ...` |

Retrieval needs no API key: embeddings (`BAAI/bge-small-en-v1.5`, 384 dimensions) and the re-ranker
(`BAAI/bge-reranker-base`) run locally on the CPU. <!-- src: results_rag.json#embed_model, #embed_dims, #rerank_model -->

## 1. The question sets

- **Easy** (`rag_golden.yaml`): 81 questions, 66 answerable <!-- src: results_rag.json#set.questions, #set.answerable -->.
  They mostly reuse the filing's own words ("What is the Epic Games lawsuit about?").
- **Hard** (`rag_hard.yaml`): 51 questions, 39 answerable <!-- src: results_rag_hard.json#set.questions, #set.answerable -->:
  paraphrased (wording that avoids the filing's terms), conceptual ("How exposed is NVIDIA to China?"), multi-section
  (the answer needs two Items or Notes) and near-miss unanswerable (the topic is in the filing, the specific answer is not).
- Each answerable question is labelled with every page that answers it. recall@5: a labelled page is inside one of the
  top 5 hits. MRR: 1 / rank of the first such hit. "All parts": for multi-section questions, one page of every
  required part is in the top 5.

## 2. Retrieval

recall@5 / MRR on the answerable questions. Structure chunks cut at every Item, Part and Note; fixed chunks are
350-word windows with a 50-word overlap.

| Arm | Easy, structure | Easy, fixed | Hard, structure | Hard, fixed |
|---|---|---|---|---|
| Page-level BM25 (the app before) | 0.894 / 0.764 | | 0.538 / 0.390 | |
| BM25 | **1.000** / 0.873 | **1.000** / **0.895** | 0.615 / 0.428 | 0.615 / 0.434 |
| Vector only | 0.773 / 0.687 | 0.803 / 0.676 | 0.692 / 0.445 | 0.667 / **0.556** |
| Hybrid (RRF) | 0.939 / 0.815 | 0.955 / 0.845 | 0.615 / 0.493 | 0.641 / 0.525 |
| Hybrid + re-rank | 0.985 / 0.814 | 0.939 / 0.796 | 0.667 / 0.526 | **0.769** / 0.549 |

<!-- src: results_rag.json#arms.<arm>.recall_at_5, #arms.<arm>.mrr; results_rag_hard.json#arms.<arm>.recall_at_5, #arms.<arm>.mrr -->

Hard set by kind, recall@5 <!-- src: results_rag_hard.json#arms.<arm>.recall_at_5_by_kind, #arms.<arm>.all_parts_at_5 -->:

| Arm | Paraphrase | Conceptual | Multi-section (any part) | Multi-section (all parts) |
|---|---|---|---|---|
| BM25, structure | 0.176 | 0.9 | 1.0 | 0.333 |
| Vector, structure | 0.471 | 0.9 | 0.833 | 0.333 |
| Hybrid + re-rank, fixed | 0.529 | 0.9 | 1.0 | **0.583** |
| Page-level BM25 | 0.118 | 0.8 | 0.917 | 0.25 |

Median time per search on a laptop CPU: BM25 38 to 43 ms, vector and hybrid 44 to 56 ms (question embedding
included), hybrid + re-rank 6.1 to 8.4 s <!-- src: results_rag.json#arms.<arm>.p50_ms; results_rag_hard.json#arms.<arm>.p50_ms -->.

What this shows:
- **Chunking helps on both sets.** BM25 over chunks beats BM25 over whole pages (easy 0.894 to 1.000, hard 0.538 to
  0.615) and sends the model less text: the top 5 holds about 1,470 words for structure chunks against about 3,510
  for pages on the hard set <!-- src: results_rag_hard.json#arms.bm25/structure.top5_words_mean, #arms.pages_bm25_before.top5_words_mean -->.
- **On the filing's own words, BM25 wins.** Vector, hybrid and re-ranking do not beat it on the easy set.
- **On paraphrased questions, BM25 fails.** It finds the page for 0.176 of them; embeddings reach about half. On the
  hard set, hybrid + re-rank over fixed chunks is best at 0.769 recall@5 and finds both parts of a multi-section
  answer most often (0.583), at about 150 times the search time.
- Small sets: one question moves easy recall@5 by 1.5 points and hard by 2.6. Differences of a question or two are noise.

The app keeps **BM25 over structure chunks** as its default: best on the easy set, fastest, and it sends the least
text. Hybrid + re-rank is the setting to turn on (`WEALTHOS_RESEARCH_SEARCH=hybrid+rerank+fixed`) where users ask in
their own words and a few seconds of CPU per question is acceptable, or once a GPU is available.

## 3. pgvector

The eval ranks chunk vectors in memory. `run_rag_pgvector.py` runs the same searches through the app's real path:
both filings embedded and stored in Postgres 16 + pgvector 0.8.7 with an HNSW cosine index, and `research.search`
asking Postgres. <!-- src: results_rag_pgvector.json#pgvector_version, #index -->

- **Same results.** The top 5 and top 20 were identical to the in-memory ranking for every question: 81 of 81 on the
  easy set, 51 of 51 on the hard set, for vector and hybrid on both chunkers, so recall@5 and MRR match exactly.
  <!-- src: results_rag_pgvector.json#arms.<arm>.agreement.top20_exact_order; results_rag_pgvector_hard.json#arms.<arm>.agreement.top20_exact_order -->
- **Latency.** The pgvector query alone takes 12.6 ms median (p95 14.4); a full vector search takes 94.8 ms median,
  of which about 35 ms is opening a new connection per call and most of the rest is embedding the question.
  <!-- src: results_rag_pgvector.json#arms.vector/structure.latency_ms -->
- **Indexing** both filings: 87.4 s, of which 81.7 s is embedding on the CPU and 0.71 s is the insert.
  <!-- src: results_rag_pgvector.json#index_time -->

## 4. Answers (live, easy set)

`gpt-oss-120b` on OpenRouter answered all 81 easy questions with three setups; Ragas faithfulness and context
precision were judged by the same model. <!-- src: results_rag_answers.json#answer_model, #judge.model -->

| Setup | Fact accuracy | Unanswerable refused | Answerable wrongly refused | Number-check rejects | Ragas faithfulness | Ragas context precision |
|---|---|---|---|---|---|---|
| BM25, structure | 0.515 | 15 of 15 | 0 | 6 | 0.967 | 0.862 |
| BM25 + relevance check | 0.508 | 15 of 15 | 1 | 6 | 0.980 | 0.866 |
| Hybrid + re-rank, structure | 0.485 | 15 of 15 | 4 | 8 | 0.953 | 0.862 |

<!-- src: results_rag_answers.json#summary.<setup>.answer_accuracy, .refused_unanswerable, .wrongly_refused_answerable, .number_check_rejects, .ragas_faithfulness, .ragas_context_precision -->

- Fact accuracy is the share of each question's labelled key facts that appear in the answer, so a correct answer in
  other words scores low; read it as a comparison between setups, not an absolute.
- **Ragas covers about half the answers.** The OpenRouter account ran out of credit during judging; 31 to 32 answers
  per setup were judged and the rest are recorded as judge errors, never scored as 0.
  <!-- src: results_rag_answers.json#summary.<setup>.judged, .judge_errors -->
- **Latency and cost** come from the first live run (`results_rag_answers_before_cite_fix.json`), which made the same
  model calls; the run above replayed them from the cache. Per question: BM25 3.40 s median, 10.74 s p95, $0.00017
  billed; with the relevance check 2.94 s and 6.71 s, $0.00014; hybrid + re-rank 9.19 s and 14.30 s, $0.00015.
  <!-- src: results_rag_answers_before_cite_fix.json#summary.<setup>.p50_seconds, .p95_seconds, .usd_per_question_billed -->
- Most remaining number-check rejects are false: the model writes a table value with its unit ("13,308 million")
  where the table prints "13,308" under "(in millions)", and the shared number check does not apply the table's scale.
  A few are right to reject (a citation in a made-up format, a page the model was never shown).

## 5. Refusing what the filings do not say

**By retrieval score.** Each arm's cut-off was tuned on a dev third and reported on the rest. On the easy set the
best arm refused 5 of 10 held-out unanswerable questions <!-- src: results_rag.json#refusal.test.hybrid/structure.unanswerable_refused -->;
on the hard set's near-miss questions every arm refused 0 of 8 <!-- src: results_rag_hard.json#refusal.test.<arm>.unanswerable_refused -->.
A retrieval score cannot tell a near miss from an answer. The app keeps only a low BM25 floor.

**By the answer step.** Telling the model to reply exactly "Not found in the filings." when the passages do not
answer, and counting that reply, refused all 15 easy unanswerable questions with no relevance check.

**By a relevance check before answering** (one structured call: do these passages contain the answer?). On the easy
set it added nothing (15 of 15 either way) and wrongly refused one answerable question. The hard set's near misses
were the real test, and the check lost there too: see section 6.

## 6. Answers (live, hard set)

Groq's free tier served the same `gpt-oss-120b` model for this run after the OpenRouter credit ran out. Its daily
limit stopped the run after 45 of the 51 questions, answered with both setups; the 6 questions left (h46 to h51) are
all near-miss unanswerable ones. Ragas was not run on this set. <!-- src: results_rag_hard_answers.json#answer_model, #stopped, #summary.<setup>.questions -->

| Setup | Unanswerable refused | Answerable wrongly refused | Fact accuracy | Number-check rejects | p50 / p95 s | USD / question (list price) |
|---|---|---|---|---|---|---|
| BM25, structure | 6 of 6 | 20 of 39 | 0.068 | 3 | 1.98 / 3.22 | 0.000385 |
| BM25 + relevance check | 6 of 6 | 28 of 39 | 0.043 | 3 | 2.82 / 3.83 | 0.000358 |

<!-- src: results_rag_hard_answers.json#summary.<setup>.refused_unanswerable, .unanswerable, .wrongly_refused_answerable,
     .answerable, .answer_accuracy, .number_check_rejects, .p50_seconds, .p95_seconds, .usd_per_question_list -->

- **The relevance check does not help.** The answer step alone refused every near miss it saw, and the check refused
  8 more answerable questions. It stays off (`research.answer(check=False)` is the default).
- **Missed retrieval shows up as a refusal, not a wrong answer.** On paraphrased questions BM25 often misses the page
  (section 2), and the model then says "Not found in the filings." instead of guessing: 20 of 39 answerable hard
  questions were refused. That is the safe failure; the fix is better retrieval for this kind of question (hybrid +
  re-rank), not a different answer step.
- Groq's free tier bills $0; the USD column is tokens at Groq's list price.
- Under Groq's rate limits one relevance-check call got no reply and that question was answered without a verdict
  (the check fails open); the offline replay in CI reproduces every recorded outcome, including that one.

## 7. CI

`backend/tests/test_rag_eval.py` re-runs every retrieval arm except the re-ranked ones on both sets and fails if any
recall@5 drops below the committed value, and replays the recorded answers (except re-ranked ones) with no network,
failing if any answer changes whether it was refused or passed the number check. Re-ranked arms are left out because
CPU re-ranking takes over half an hour for the sets. The workflow downloads the filings from SEC EDGAR on a cache miss
(repo secret `SEC_USER_AGENT`) and caches them with the models; without the secret these tests skip.
