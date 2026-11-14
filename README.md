# WealthOS

**An AI analyst for your own money that checks every number it says** — Indian bank, broker and mutual-fund
statements in, plain-English answers out, plus a US company-analyst mode built from SEC filings.

[![CI](https://github.com/AmanDataGuy/WealthOS/actions/workflows/ci.yml/badge.svg)](https://github.com/AmanDataGuy/WealthOS/actions/workflows/ci.yml)
![tests](https://img.shields.io/badge/tests-193%20passing-brightgreen)
![python](https://img.shields.io/badge/python-3.11-3776AB)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

[Eval report](TECHNICAL_REPORT.md) · [Architecture](docs/architecture.md) · [Company mode](docs/company_mode.md) · [MCP server](docs/mcp.md)

| Result | WealthOS | Baseline |
|---|---|---|
| Right numbers, 34-question golden set | **96%** on Groq and on OpenRouter <!-- src: backend/evals/results.json#summary.value_accuracy_pct --> | 72% letting the same model write SQL <!-- src: backend/evals/results_baseline.json#summary.value_accuracy_pct --> |
| Right kind of answer (answer, ask back, refuse advice) | **100%** <!-- src: backend/evals/results.json#summary.plan_match_pct --> | 85% <!-- src: backend/evals/results_baseline.json#summary.plan_match_pct --> |
| SQL attacks blocked / normal queries wrongly blocked | **53 of 53** / 0 of 16 <!-- src: backend/evals/results_sets.json#sql.block_rate_pct --> | – |
| Wrong numbers the number check lets through | **1.5%** of 179 altered sentences <!-- src: backend/evals/results_sets.json#numbers.false_accept_pct --> | – |
| Company accounting checks / Excel formulas recomputed | **115 of 117** / 573 of 573 <!-- src: backend/evals/company_tieouts.json#pass_rates --> | – |

<sub>Model `openai/gpt-oss-120b`, measured 13–14 Nov 2026 on synthetic data; 2.9 s median per question. Every number
links to a committed result file; the method and every run are in the [technical report](TECHNICAL_REPORT.md).</sub>

| Compare companies | Earnings review | Rebalance plan within limits |
|---|---|---|
| ![Compare](docs/screenshots/company-compare.png) | ![Earnings review](docs/screenshots/company-earnings-review.png) | ![Rebalance plan](docs/screenshots/company-rebalance-plan.png) |

## What it does

- **Ask your money anything.** Upload bank and card statements (CSV, Excel, HDFC/ICICI/SBI PDFs), broker tradebooks,
  holdings and CAS files, then ask: *"How much did I spend on food this FY?"*, *"Old or new tax regime for me?"*.
  New here? **Load sample data** fills a synthetic year in one click. ([ingest](docs/ingest.md))
- **Every number is checked.** The model plans and explains; code computes. A sentence with a number that is not in
  the results is replaced by the table. ([assistant graph](docs/agent_graph.md))
- **Company analyst.** Type a US ticker: five years of statements, ratios and a DCF as an Excel model where every
  figure names its SEC filing; compare 2–6 companies; review each quarter against a forecast you locked before the
  results; check a rebalance plan against your own limits. ([company mode](docs/company_mode.md))
- **Dashboards and Excel.** Build tiles by hand or by asking; export to Excel; fill your own Excel template.
- **Strategy lab and rebalancing.** Backtest a rule with Indian charges and a deflated Sharpe ratio; see drift from
  your target mix.
- **Use it from an MCP client.** A read-only MCP server exposes metrics, tax, drift and backtests. ([MCP](docs/mcp.md))
- **No advice, ever.** WealthOS is not SEBI-registered: it never says buy, sell or hold.

## How it works

```mermaid
flowchart LR
    Q[Question] --> P[plan<br>one model call picks a tool]
    P --> R[run<br>code: metrics, guarded SQL, tax, SEC...]
    R --> E[explain<br>one model call over the rows]
    E --> V[verify<br>every number checked]
    V --> A[Answer + table + source]
```

1. A vague question ("what's my return?") is asked back in code, before any model call.
2. The planner picks one of 15 plan kinds; code runs it on a locked, read-only DuckDB over your files.
3. The explainer sees at most 40 rows; `verify` matches every number in its text to the rows, or shows the table.

Key decision: a metrics layer of 18 defined measures instead of free text-to-SQL. Same model, same questions: 96%
vs 72% right numbers. More in [architecture](docs/architecture.md).

## Quickstart

```bash
git clone https://github.com/AmanDataGuy/WealthOS && cd WealthOS
cp .env.example .env                 # set GROQ_API_KEY and WEALTHOS_JWT_SECRET (32+ bytes)
docker compose up -d --build         # web, API, price worker, Postgres
# open http://localhost:8080, sign up, then "Load sample data"
```

Local development (Python 3.11): `python -m venv venv && pip install -r backend/requirements-dev.txt`, then
`bash start.sh` (web on :5173). Tests, no keys needed: `cd backend && pytest -q`.

## Evaluation

| Eval | Size | Result | Baseline / note |
|---|---|---|---|
| Golden set, right numbers | 25 value questions | 96% Groq, 96% OpenRouter | 72% plain text-to-SQL; 44% on a local 8B model |
| Golden set, right kind of answer | 34 questions | 100% | 85% text-to-SQL |
| SQL guard | 53 attacks + 16 normal queries | 100% blocked, 0% false blocks | one real hole found and fixed |
| Number check | 179 altered sentences | 1.5% false accept, 0% false reject | 0 of 50 invented numbers accepted |
| PII redaction | 55 strings | 84.6% recall | misses names inside payment narrations |
| Company tie-outs | 5 companies | 115 / 117 checks; 573 / 573 formulas | quarters: 68 / 68 revenue years tie to the 9-month figure |

Golden-set answers are replayed in CI with no keys; the labelled sets fail the build if any rate gets worse.
Reproduce live: `cd backend && python evals/run.py`. Every run and failure: [eval results](docs/eval/README.md),
[error analysis](docs/eval/ERROR_ANALYSIS.md).

## Limitations

- **Not deployed yet.** It runs locally or with Docker; an AWS deployment is planned.
- **Synthetic data.** Every eval set is synthetic; the PDF reader has not seen a real bank statement.
- **Small samples.** 34 golden questions: one question is about 3 points; two runs of one model can differ by one.
- **Regex PII redaction** misses names inside payment narrations.
- **Company mode** covers US filers only, no banks or insurers, and takes the share price as an input.

## Docs

| Topic | Where |
|---|---|
| Architecture, parts, repo layout, settings | [docs/architecture.md](docs/architecture.md) |
| The assistant graph, checkpoints, tracing | [docs/agent_graph.md](docs/agent_graph.md) |
| File ingest and column mapping | [docs/ingest.md](docs/ingest.md) |
| Company mode and the earnings loop | [docs/company_mode.md](docs/company_mode.md) |
| Evals, result files, error analysis | [docs/eval/](docs/eval/README.md), [TECHNICAL_REPORT.md](TECHNICAL_REPORT.md) |
| MCP server setup | [docs/mcp.md](docs/mcp.md) |
| Security model | [SECURITY.md](SECURITY.md) |

**Stack:** Python 3.11 · FastAPI · LangGraph · DuckDB · Postgres · React + TypeScript · Groq / OpenRouter ·
openpyxl · MCP · Docker. **License:** MIT.
