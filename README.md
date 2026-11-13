<div align="center">

# WealthOS

**Ask your own financial data anything, with every number traced to its source**

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![DuckDB](https://img.shields.io/badge/DuckDB-Engine-FFF000?style=flat-square&logo=duckdb&logoColor=black)](https://duckdb.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791?style=flat-square&logo=postgresql&logoColor=white)](https://postgresql.org)
[![React](https://img.shields.io/badge/React-18-61DAFB?style=flat-square&logo=react&logoColor=black)](https://react.dev)

</div>

---

## What It Does

The home page is a chat. Under the message box, **+ Query your own data** takes bank and card statements (CSV,
Excel or PDF in HDFC, ICICI and SBI layouts), broker tradebooks, holdings, CAS PDFs, daily prices, annual reports,
Google Sheets and plain notes. New here? **Load sample data** fills an account with a synthetic year in one click. Then ask:
*"How much did I spend on food this FY?"*, *"Old or new tax regime for me?"*, *"Is my portfolio off target?"*,
*"Backtest INFY when RSI is below 30, sell above 70"*. Chats are kept on the left, each named in a few words.

Four more pages sit on the top bar:

| Page | What you do there |
|---|---|
| **Dashboards** | Build by hand (drag, resize, remove, rearrange tiles) or with AI ("make spending monthly"); start from a template; download as Excel; fill your own Excel template (formulas and all) with your data |
| **Strategy lab** | Describe a rule in words, or upload a Python file defining `weights(close)`; backtest with Indian charges against buy-and-hold |
| **Rebalancing** | Set a target mix; see each holding's drift and the rupee change back to target |
| **Company** | Type a US-listed company or ticker; get a five-year workbook built from its SEC filings, where every reported figure carries its XBRL concept and filing, every total and ratio is a live Excel formula, and accounting tie-outs are checked. Also: compare 2-6 companies, review each quarter against a forecast you locked before the results, and check a rebalance plan against your own limits ([docs](docs/company_mode.md)) |

The AI plans and explains. Code computes every number. No buy/sell advice, ever.

| Compare companies | Earnings review | Rebalance plan within limits |
|---|---|---|
| ![Compare](docs/screenshots/company-compare.png) | ![Earnings review](docs/screenshots/company-earnings-review.png) | ![Rebalance plan](docs/screenshots/company-rebalance-plan.png) |

---

## Architecture

```mermaid
flowchart LR
    classDef input fill:#dbeafe,stroke:#2563eb,stroke-width:2px,color:#1e3a8a
    classDef interface fill:#d1fae5,stroke:#059669,stroke-width:2px,color:#064e3b
    classDef core fill:#f1f5f9,stroke:#f97316,stroke-width:2px,color:#7c2d12
    classDef data fill:#e0f2fe,stroke:#0284c7,stroke-width:2px,color:#0c4a6e
    classDef guard fill:#f5f5f4,stroke:#78716c,stroke-width:2px,color:#44403c

    Files[Files · Google Sheets · notes]:::input --> Ingest[data/ingest + mapping<br>rules, one AI call, you confirm]:::core
    Ingest --> PQ[(Parquet per user<br>backend/storage/)]:::data
    Worker[worker.py<br>evening prices]:::core --> PQ

    UI[React app<br>Chat · Dashboards · Strategy lab · Rebalancing · Company]:::interface -- HTTP --> API[FastAPI<br>app/main.py]:::interface
    API --> Agent[ai/graph.py · LangGraph<br>plan → run a tool → explain → verify<br>checkpointed per chat]:::core
    API --> Company[company/<br>SEC filings → statements → ratios → DCF → workbook<br>one checked AI read of the earnings release]:::core
    Agent --> Tools[metric · SQL · SEC · filings<br>tax · rebalance · prices · backtest]:::core
    Tools --> Engine[data/engine.py<br>locked DuckDB]:::core
    API --> Features[features/<br>dashboards · strategy · rebalance · excel]:::core
    Features --> Engine
    Engine --> PQ
    Agent --> Verify[ai/verify.py<br>SQL guard · number check]:::guard
    MCP[mcp_server.py<br>read-only: metrics, tax, drift, backtests]:::interface --> Engine
    API --> PG[(Postgres<br>users · LLM cost log)]:::data
```

> The AI never writes a number the user sees. It picks a tool; code runs it; every number in the written answer is
> matched against the tool's rows, or the sentence is replaced by the table.

---

## Project Layout

```
backend/
  app/
    main.py          the HTTP API: app, middleware, startup, sign-in checks
    api/             the routes, one router per area (account, sources, assistant, dashboards, strategy, company)
    worker.py        background job: evening price refresh
    mcp_server.py    read-only MCP server for Claude Desktop (setup and tools: docs/mcp.md)
    ai/              the assistant: LangGraph graph, LLM client, PII redaction, SQL + number checks, tools, filing search
    company/         company analyst: SEC facts, statements, ratios, valuation, release reader, tie-outs, workbook
    core/            settings, rate limits, account export / delete, workspaces
    data/            DuckDB engine, metrics.yaml, file parsers (ingest/, finx/), column mapping, prices, time periods
    features/        dashboards, templates, Excel templates, reports, strategy lab + Python sandbox, rebalancing, tax
  migrations/        numbered Postgres SQL files, applied once each at API startup
  tests/  evals/  scripts/
  requirements.txt  Dockerfile
frontend/
  src/               App (top bar) · Chat · Upload · Dashboards · Strategy · Rebalance · Company · tiles, panel, report
  Dockerfile  nginx.conf
docker-compose.yml   web, API, price worker, Postgres
```

User data lives in `backend/storage/` (never committed). Postgres holds only accounts and the LLM cost log.

---

<div align="center">

## Under the Hood

| Category | Stack | Detail |
|:---:|:---|:---|
| **Engine** | DuckDB over Parquet | In-memory connection per request; no external access or extensions; memory/thread limits; locked config; 10 s timeout |
| **Assistant** | LangGraph: one plan, up to 3 tools, one explanation | State saved per chat (SQLite checkpointer per user); seconds per node on every answer; optional Langfuse tracing ([docs](docs/agent_graph.md)). Tools: metrics, guarded SQL, SEC XBRL, filing pages, calc, tax, rebalancing, price fetch, backtest, save a new metric; "remember that ..." and clarifications become remembered meanings |
| **SQL Guard** | DuckDB parser + sqlglot | One SELECT, no file/URL/settings functions, every table and column checked, 5,000-row cap |
| **Number Check** | `ai/verify.py` | Every number in a written answer must match a result (or a sum / difference / % change within one row or column); currency must match |
| **LLM** | Groq `openai/gpt-oss-120b` + OpenRouter fallback | PII redacted on every prompt; per-user and server daily cost caps; usage logged per user |
| **Dashboards** | Spec + ops + edit history | Every edit is a checked op; undo/redo; refresh runs no AI; Excel export with a provenance sheet |
| **Excel templates** | openpyxl + headless LibreOffice | Your workbook's Data sheet is filled and recalculated; macros, OFFSET/INDIRECT and Tables refused |
| **Strategy lab** | pandas, vectorized | Decide at the close, fill at the next open; STT, stamp duty, exchange and SEBI fees, GST; deflated Sharpe |
| **Python strategies** | Separate process | Code checked first (pandas / numpy / math only, no files or network), 30 s limit, output checked; off in production unless enabled |
| **Rebalancing** | Latest holdings snapshot | Current vs target weight, drift in points, rupee change to target; arithmetic only |
| **Tax** | Per-FY rule tables | Old vs new regime, capital gains from your tradebook; "verify with a CA" |
| **Auth** | bcrypt + JWT | 7-day sessions; refuses to start without a 32+ byte secret; login lockout; rate limits; export or delete all your data |
| **Compliance** | Always on | No buy/sell/hold advice or price targets; WealthOS is not SEBI-registered |

</div>

---

## Eval Results

`backend/evals/run.py` asks 34 questions over synthetic bank rows, a Zerodha tradebook and a holdings export; every
expected value is computed in plain Python, not by the engine. CI replays all 34 from recorded replies with no keys.

Live run on 2026-11-13 with `openai/gpt-oss-120b` served by OpenRouter; the per-question rows are in
`backend/evals/results_openrouter.json`.

| What | Result |
|---|---|
| Plan type matched the expected one | 100% (34 of 34) <!-- src: backend/evals/results_openrouter.json#summary.plan_match_pct --> |
| Every expected number present in the answer rows | 96% (24 of 25 value questions) <!-- src: backend/evals/results_openrouter.json#summary.value_accuracy_pct --> |
| Answers marked *trusted* on value questions | 92.0% <!-- src: backend/evals/results_openrouter.json#summary.trusted_pct_on_value_cases --> |
| Buy/sell advice questions refused | 100% (5 of 5) <!-- src: backend/evals/results_openrouter.json#summary.advice_abstention_pct --> |
| Seconds per question, p50 / p95 | 2.9 / 4.8 <!-- src: backend/evals/results_openrouter.json#summary.p50_seconds,summary.p95_seconds --> |
| Cost per question | $0.00022 <!-- src: backend/evals/results_openrouter.json#summary.usd_per_question --> |

The one miss: *"How much did I spend on Swiggy last financial year?"* was answered for this financial year (the
planner picked the wrong period; the answer names the period it used). Vague questions such as *"What's my return?"*
are now asked back in code before the model is called.

Same model, same 34 questions, letting it write SQL directly instead: **72.0%** of numbers right
<!-- src: backend/evals/results_baseline.json#summary.value_accuracy_pct -->. The metrics layer and the number check
are the difference. Guard tests (53 SQL attacks, 179 perturbed sentences, 55 PII strings, 31 mapping layouts), the
company-mode tie-outs and a local-model run are in **[TECHNICAL_REPORT.md](TECHNICAL_REPORT.md)**.

---

## Quick Start

```bash
git clone https://github.com/AmanDataGuy/WealthOS
cd WealthOS
python -m venv venv && venv\Scripts\activate      # Windows
pip install -r backend/requirements.txt
cp .env.example .env                              # fill in GROQ_API_KEY and WEALTHOS_JWT_SECRET
docker compose up -d wealthos-db                  # Postgres: sign-in and the cost log
python backend/scripts/verify_env.py              # check the setup
.\start.ps1                                       # Windows; bash start.sh on macOS / Linux
```

Open **http://localhost:5173**, sign up, and use **+ Query your own data** under the chat box. Or run everything with
`docker compose up -d --build` and open http://localhost:8080.

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` | Primary LLM (required) |
| `OPENROUTER_API_KEY` | Fallback LLM provider (recommended) |
| `WEALTHOS_DB_URL` | Postgres for sign-in and the LLM cost log |
| `WEALTHOS_JWT_SECRET` | Signs session tokens; required, 32+ bytes |
| `WEALTHOS_ENV` | `dev`, `test` or `production` (unset = production) |
| `SEC_USER_AGENT` | Name + email SEC asks every client to send |
| `WEALTHOS_MAX_DAILY_COST_USD` / `_USER` | Daily LLM spend caps: server (default 5), per user (default 0.50) |
| `WEALTHOS_TRUSTED_PROXIES` | Proxy IPs whose X-Forwarded-For is trusted for rate limits |
| `WEALTHOS_ENABLE_NSE` | NSE price downloads (default on outside production, off in production) |
| `WEALTHOS_ENABLE_PY_STRATEGIES` | Python strategy uploads (default on outside production, off in production) |
| `WEALTHOS_ALLOW_FREE_FALLBACK` | Use a `:free` fallback model in production (default off) |
| `WEALTHOS_RISK_FREE_RATE` | Sharpe ratio risk-free rate (default 6.5%) |
| `LIBREOFFICE_PATH` | Only if LibreOffice is not in the default place (Excel templates) |

See `.env.example` for the full list. Every API route is listed at http://localhost:8000/docs.
