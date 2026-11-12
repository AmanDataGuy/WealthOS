# Evaluation results

Every number on this page is copied from a committed result file; the `<!-- src: file#key -->` comment after it
names the file and the JSON key. All data is synthetic (the fixtures are built in `backend/evals/run.py` and
`backend/app/data/samples.py`; the labelled sets are hand-written YAML). Commands run from `backend/`.
What fails and why: [ERROR_ANALYSIS.md](ERROR_ANALYSIS.md).

## Result files

| File | What it is | Command | Recorded replies |
|---|---|---|---|
| `backend/evals/results.json` | The assistant on the 34 golden questions, live on Groq `openai/gpt-oss-120b` (run before latency and cost were recorded) | `python evals/run.py` | `evals/llm_cache.json` |
| `backend/evals/results_openrouter.json` | The same assistant and model weights served by OpenRouter (Groq's free daily token budget is smaller than one full run); carries latency and cost | `GROQ_API_KEY= LLM_FALLBACK_MODEL=openai/gpt-oss-120b WEALTHOS_ENV=dev python evals/run.py --name openrouter` | `evals/llm_cache_openrouter.json` |
| `backend/evals/results_baseline.json` | A plain text-to-SQL bot on the same 34 questions and the same model (one call writes SQL; `guard_sql` and the read-only engine run it; no planner, metrics layer or number check) | `GROQ_API_KEY= LLM_FALLBACK_MODEL=openai/gpt-oss-120b WEALTHOS_ENV=dev python evals/run.py --arm baseline` | `evals/llm_cache_baseline.json` |
| `backend/evals/results_local_qwen3_8b.json` | The assistant on a local model (Ollama `qwen3:8b`, 6 GB laptop GPU) | `LLM_BASE_URL=http://localhost:11434/v1 GROQ_API_KEY=ollama LLM_MODEL=qwen3:8b LLM_REASONING_EFFORT=none OPENROUTER_API_KEY= LLM_TIMEOUT_S=300 WEALTHOS_ENV=dev python evals/run.py --name local_qwen3_8b` (thinking off: with it on, a short trial was far too slow for the time budget and its thinking filled Ollama's default context window) | `evals/llm_cache_local_qwen3_8b.json` |
| `backend/evals/results_sets.json` | The labelled sets: PII redaction, `guard_sql` attacks, number-check perturbation grid, deflated Sharpe known answer, column mapping (rules only, and rules + AI) | `python evals/sets/run_sets.py` (keyless); add `--live` for the rules + AI mapping arm (`GROQ_API_KEY= LLM_FALLBACK_MODEL=openai/gpt-oss-120b WEALTHOS_ENV=dev`) | `evals/llm_cache_mapping.json` |

CI (`pytest`, no network) replays all 34 questions for every recorded run from its cache and fails on any question
whose plan kind, values or badge differ from the committed file (`tests/test_evals_smoke.py`), and re-runs the keyless
labelled sets, failing if any rate is worse than `results_sets.json` (`tests/test_eval_sets.py`).

## Golden questions: assistant vs plain text-to-SQL

| | Assistant (Groq) | Assistant (OpenRouter) | Text-to-SQL baseline | Assistant, local `qwen3:8b` |
|---|---|---|---|---|
| Right numbers on the 25 value questions | 100.0% <!-- src: backend/evals/results.json#summary.value_accuracy_pct --> | 100.0% <!-- src: backend/evals/results_openrouter.json#summary.value_accuracy_pct --> | 72.0% <!-- src: backend/evals/results_baseline.json#summary.value_accuracy_pct --> | 44.0% <!-- src: backend/evals/results_local_qwen3_8b.json#summary.value_accuracy_pct --> |
| Right kind of answer (plan, clarify, refuse) on all 34 | 97.1% <!-- src: backend/evals/results.json#summary.plan_match_pct --> | 94.1% <!-- src: backend/evals/results_openrouter.json#summary.plan_match_pct --> | 85.3% <!-- src: backend/evals/results_baseline.json#summary.plan_match_pct --> | 97.1% <!-- src: backend/evals/results_local_qwen3_8b.json#summary.plan_match_pct --> |
| Value answers with the trusted badge | 92.0% <!-- src: backend/evals/results.json#summary.trusted_pct_on_value_cases --> | 92.0% <!-- src: backend/evals/results_openrouter.json#summary.trusted_pct_on_value_cases --> | 0.0% <!-- src: backend/evals/results_baseline.json#summary.trusted_pct_on_value_cases --> | 60.0% <!-- src: backend/evals/results_local_qwen3_8b.json#summary.trusted_pct_on_value_cases --> |
| Advice questions refused | 100.0% <!-- src: backend/evals/results.json#summary.advice_abstention_pct --> | 100.0% <!-- src: backend/evals/results_openrouter.json#summary.advice_abstention_pct --> | 100.0% <!-- src: backend/evals/results_baseline.json#summary.advice_abstention_pct --> | 100.0% <!-- src: backend/evals/results_local_qwen3_8b.json#summary.advice_abstention_pct --> |
| Sentences the number check threw away | 4 <!-- src: backend/evals/results.json#summary.number_check_rejects --> | 5 <!-- src: backend/evals/results_openrouter.json#summary.number_check_rejects --> | no check | 4 <!-- src: backend/evals/results_local_qwen3_8b.json#summary.number_check_rejects --> |
| Seconds per question, p50 / p95 | not recorded | 4.0 / 9.04 <!-- src: backend/evals/results_openrouter.json#summary.p50_seconds, summary.p95_seconds --> | 1.5 / 4.58 <!-- src: backend/evals/results_baseline.json#summary.p50_seconds, summary.p95_seconds --> | 4.9 / 8.49 <!-- src: backend/evals/results_local_qwen3_8b.json#summary.p50_seconds, summary.p95_seconds --> |
| Model calls per question | 1.76 <!-- src: backend/evals/results.json#summary.llm_calls_per_question --> | 1.79 <!-- src: backend/evals/results_openrouter.json#summary.llm_calls_per_question --> | 1.0 <!-- src: backend/evals/results_baseline.json#summary.llm_calls_per_question --> | 1.47 <!-- src: backend/evals/results_local_qwen3_8b.json#summary.llm_calls_per_question --> |
| Tokens per question | not recorded | 3299 <!-- src: backend/evals/results_openrouter.json#summary.tokens_per_question --> | 833 <!-- src: backend/evals/results_baseline.json#summary.tokens_per_question --> | 2917 <!-- src: backend/evals/results_local_qwen3_8b.json#summary.tokens_per_question --> |
| USD per question (as billed by OpenRouter) | not recorded | $0.000219 <!-- src: backend/evals/results_openrouter.json#summary.usd_per_question --> | $0.000064 <!-- src: backend/evals/results_baseline.json#summary.usd_per_question --> | $0 (local) |

Notes:
- Each column is one live run. Two live runs of the same arm can differ by a question or two (the model is not
  deterministic even at temperature 0): the Groq and OpenRouter columns use the same weights and differ on one clarify
  question. Read differences of one or two questions as noise.
- The latency and cost columns time the assistant before it became a LangGraph graph (same prompts and calls; CI
  replays the recorded replies through the graph and gets the same answers). Seconds exclude time spent waiting out a
  rate limit; the runs here waited 0 s.
- USD is what OpenRouter reported per call (`usage.cost`). On Groq's free tier the same calls are billed $0.

## Labelled sets

| Set | Headline | Size |
|---|---|---|
| PII redaction | recall 84.6% <!-- src: backend/evals/results_sets.json#pii.recall_pct -->, 91.7% without the three known-gap rows (names in payment narrations) <!-- src: backend/evals/results_sets.json#pii.recall_without_known_gaps_pct -->; placeholder precision 83.8% <!-- src: backend/evals/results_sets.json#pii.precision_pct -->; amounts, dates and references kept 92.0% <!-- src: backend/evals/results_sets.json#pii.keep_survival_pct --> | 55 strings <!-- src: backend/evals/results_sets.json#pii.rows --> |
| `guard_sql` + locked engine | 100.0% of attacks blocked <!-- src: backend/evals/results_sets.json#sql.block_rate_pct -->, 0 escaped <!-- src: backend/evals/results_sets.json#sql.escaped -->, 0.0% of legitimate queries blocked <!-- src: backend/evals/results_sets.json#sql.false_block_rate_pct --> | 53 attacks <!-- src: backend/evals/results_sets.json#sql.attacks -->, 16 legitimate <!-- src: backend/evals/results_sets.json#sql.legit --> |
| `check_numbers` perturbation grid | false accept 1.5% <!-- src: backend/evals/results_sets.json#numbers.false_accept_pct -->, false reject 0.0% <!-- src: backend/evals/results_sets.json#numbers.false_reject_pct -->; a sum of two other cells stated as the answer is accepted 100.0% of the time <!-- src: backend/evals/results_sets.json#numbers.derived_sum_accept_pct --> | 179 sentences <!-- src: backend/evals/results_sets.json#numbers.cells --> |
| Deflated Sharpe | matches the published worked example (DSR 0.9004 at 100 trials, 0.9505 at 46) with error 0.0 <!-- src: backend/evals/results_sets.json#deflated_sharpe.max_abs_error -->; expected-max-Sharpe term 0.1132 by formula <!-- src: backend/evals/results_sets.json#deflated_sharpe.sr0_formula --> vs 0.1119 by Monte Carlo <!-- src: backend/evals/results_sets.json#deflated_sharpe.sr0_monte_carlo_20000x100 --> | 2 published values |
| Column mapping, rules only | 83.9% of header rows fully right <!-- src: backend/evals/results_sets.json#mapping_rules.accuracy_pct -->; columns precision 97.7% <!-- src: backend/evals/results_sets.json#mapping_rules.column_precision_pct --> / recall 92.1% <!-- src: backend/evals/results_sets.json#mapping_rules.column_recall_pct --> | 31 header rows <!-- src: backend/evals/results_sets.json#mapping_rules.rows --> |
| Column mapping, rules + AI | 83.9% fully right <!-- src: backend/evals/results_sets.json#mapping_rules_ai.accuracy_pct -->; columns precision 96.4% <!-- src: backend/evals/results_sets.json#mapping_rules_ai.column_precision_pct --> / recall 95.0% <!-- src: backend/evals/results_sets.json#mapping_rules_ai.column_recall_pct -->; `high` confidence right 100.0% <!-- src: backend/evals/results_sets.json#mapping_rules_ai.confidence_precision.high.pct -->, `medium` right 95.8% <!-- src: backend/evals/results_sets.json#mapping_rules_ai.confidence_precision.medium.pct --> | 8 model calls <!-- src: backend/evals/results_sets.json#mapping_rules_ai.llm_calls --> |

The mapping rules were extended after the first measurement (commit "fix(ingest): recognise more bank, card and
broker column names"), so the rules-only number is in-sample: the same rows that showed the gaps now score them. The
first measurement is in the earlier commit of `results_sets.json` ("feat(evals): labelled sets ...").
`saved` confidence is not measured: it replays a mapping the user confirmed for the exact same columns, so it is as
right as the user was.
