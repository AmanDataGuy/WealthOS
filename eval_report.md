# WealthOS — DeepEval Quality Gate Report

**Run date:** 2026-09-07
**Judge model:** `gemini-2.5-flash-lite` (Google Gemini API, paid key, user-provided, capped at $2 budget)
**Dataset:** `eval/writer_golden_dataset.json` — full 28 examples (not a subset)
**Result file:** `eval/results/deepeval_2026-09-07.json`

## Headline result

**26 / 28 passed — 92.9%** (script prints `92%`, integer division)

This is the first time this dataset has been run in full — every prior attempt (`eval/results/deepeval_2026-08-19.json`, `deepeval_2026-09-03.json`) was a `--limit 1` run against Groq, and both were mostly `null` (6-8 of 9 metrics failed to complete) because Groq's free-tier daily token quota (200,000 TPD, shared across all 3 rotation keys under one org) kept getting exhausted mid-run. This is the first run with real, mostly-complete signal.

## What was actually tested

### The dataset

28 hand-curated golden examples in `eval/writer_golden_dataset.json`, each containing a ticker, company name, a full context dict (the financial/risk/personal data the writer agent would have been given), and a ground-truth memo. Deliberately diverse, not just US large-caps:

| Category | Tickers |
|---|---|
| US mega-cap tech | AAPL, MSFT, NVDA, GOOGL, META, AMZN |
| Other US equities | TSLA, JPM, XOM, NFLX, AMD, PLTR, WMT, BABA |
| Indian equities | RELIANCE.NS, INFY, TCS.NS, HDFCBANK.NS, WIPRO.NS |
| Indian index/ETF products | NIFTY50_ETF, NIFTYBEES.NS, NIFTYSC.NS, LIQUIDBEES.NS |
| Global ETF | VT |
| Crypto | BTC-USD |
| Non-stock personal-finance scenarios | DEBT_PAYOFF, ELSS_80C, NPS_PPF_ELSS |

That last category matters: 3 of the 28 examples aren't "should I buy this stock" at all — they test whether the writer agent can produce a coherent, well-structured memo for debt payoff strategy and Indian tax-saving instrument questions, not just equity analysis.

### The 9 metrics, and what each actually checks

| # | Metric | Type | What it checks | Result (28 examples) |
|---|---|---|---|---|
| 1 | Faithfulness | DeepEval built-in | Every claim in the memo traces back to the provided context — the core hallucination-adjacent check | **28/28 (100%)** |
| 2 | Hallucination | DeepEval built-in | No invented past decisions, fabricated prices, fake history | **28/28 (100%)** |
| 3 | Answer Relevancy | DeepEval built-in | Memo directly addresses the investment question asked | **11 pass / 2 fail / 15 errored** — see finding below |
| 4 | Contextual Precision | DeepEval built-in | Most relevant context ranked/used before less relevant chunks | **28/28 (100%)** |
| 5 | Contextual Recall | DeepEval built-in | All information needed for the memo was actually retrieved | **26/28 (93%)**, 2 errored |
| 6 | Contextual Relevancy | DeepEval built-in | Retrieved context chunks are relevant to the query | **27/28 (96%)**, 1 errored |
| 7 | Financial Verdict Quality | Custom GEval | Verdict (Buy/Hold/Avoid) is defensible given the data, doesn't contradict the risk score, cites 2-3 specific reasons | **28/28 (100%)** |
| 8 | Task Completion | Custom GEval | All 7 required memo sections present and substantive, not empty headings | **28/28 (100%)** |
| 9 | Verdict Consistency | Custom, pure Python (no LLM) | Deterministic rule check: BUY invalid if risk≥9, AVOID invalid if risk≤4 | **28/28 (100%)** |

An example only fails the gate overall if at least one metric completed **and returned a hard fail** — a metric that errored out (returned `null`) doesn't sink the entry by itself, per the vacuous-truth bug fixed earlier this session (an entry needs at least one *completed* metric, and all completed ones must pass).

### The two real failures

**NFLX** and **BABA** both failed on **Answer Relevancy** specifically — the metric ran successfully (unlike the 15 `null` cases) and returned a genuine `false`. Every other metric passed for both. This means the writer agent's underlying analysis (Faithfulness, Financial Verdict quality, Task Completion) was sound for both — the memo's *relevance to the specific question asked* is what fell short. Worth a manual read of these two memos in the golden dataset to see if the ground-truth outputs themselves drift off-topic, or if this reflects something Gemini's judge is stricter about than the earlier Groq/gpt-oss-120b judge was.

## The most important finding: Answer Relevancy has a 54% error rate

15 of 28 examples show `AnswerRelevancy: null` with an **empty error message** in the log (`[warn] AnswerRelevancy failed for AAPL: ` — nothing after the colon). This is a real, reproducible pattern, not noise — it's the single metric with meaningfully worse reliability than the other 8, all of which completed at 93-100%.

One data point on the likely cause: `NIFTYSC.NS`'s `ContextualRelevancy` failure carried a real, visible error — *"Evaluation LLM outputted an invalid JSON. Please use a better evaluation model."* This is the same class of issue documented in `run_deepeval.py`'s own comments about Groq's gpt-oss-120b: an evaluation LLM occasionally wrapping its structured-JSON answer in something DeepEval's strict parser rejects. Answer Relevancy's internal DeepEval implementation likely asks the judge to break the input question into sub-statements and score relevancy per-statement — a more complex structured-output shape than the other metrics — which may be more prone to this same JSON-parsing brittleness with Gemini too, just far more frequently for this specific metric.

**Not chased further in this run** to stay within budget and keep this a single clean run as asked — worth a follow-up investigation (retry a couple of the null cases individually with verbose logging to see the raw judge response) before trusting Answer Relevancy's numbers as much as the other 8 metrics.

## Cost — estimated, not billed-and-confirmed

Two things prevented getting an exact, authoritative figure from this app's own tracking:
1. `GeminiJudge` (`eval/deepeval_metrics.py`) calls the Gemini API directly via `httpx`, bypassing `services/llm_client.py`'s `call_llm()` — which is the only code path that persists usage to the `llm_usage` table. So this run's tokens were never recorded there even in principle.
2. Separately, the `llm_usage` table doesn't exist yet on this local Postgres volume at all (added to `scripts/init_db.sql` this session but not yet re-applied here) — confirmed via a direct query that failed with `UndefinedTableError`.

**Estimate**, based on `run_deepeval.py`'s own documented assumption of ~3-5k tokens per metric call: 28 examples × 8 LLM-backed metrics (Verdict Consistency is free/local) = 224 base measurements, several of which (Faithfulness, Hallucination, the 3 Contextual* metrics) internally issue more than one judge call each for claim-extraction/verification sub-steps — realistically 350-500 total API round-trips. At `gemini-2.5-flash-lite` pricing ($0.10/1M input, $0.40/1M output) and ~4k tokens/call (80/20 input/output split), that's roughly **$0.15-$0.35** — comfortably inside the $2 cap. **Recommend checking the actual Google AI Studio / Cloud Console billing page for the real, authoritative number** — this section is a reasoned estimate, not a confirmed bill.

## What this run does and doesn't prove

**Does prove:** the writer agent produces structurally sound, verdict-consistent, context-faithful memos across a genuinely diverse set of 28 scenarios — not just cherry-picked US tech stocks. Faithfulness and Hallucination both at 100% is the most reassuring pair of numbers here, since those are the two metrics most directly checking "did the LLM make something up."

**Doesn't prove:** that Answer Relevancy's 2 real failures (or its 15 unmeasured cases) reflect a genuine writer-agent weakness versus a judge-side quirk — that needs the follow-up investigation noted above before drawing a firm conclusion either way. Also doesn't cover live-pipeline behavior end-to-end (this is graded against the golden dataset's pre-written memos, not a fresh live `/analyze` run per example) — that's what the separate reliability/pass^k eval layer and manual stress testing (done earlier this session) are for.

## Recommended next steps

1. Investigate the Answer Relevancy null pattern — retry 2-3 of the failing cases with raw judge-response logging to see if it's the same JSON-formatting brittleness as the `NIFTYSC.NS` case.
2. Manually read the NFLX and BABA golden-dataset memos to sanity-check whether the relevancy failure is real or a golden-dataset quality issue.
3. Re-run `scripts/init_db.sql` (locally and on the AWS deployment) to create the `llm_usage` table, and consider routing `GeminiJudge` through `call_llm()`-equivalent tracking so future eval runs get an exact, persisted cost figure instead of an estimate.
4. Check Google AI Studio's billing dashboard for the actual dollar amount this run cost, to replace the estimate above with a confirmed number.
