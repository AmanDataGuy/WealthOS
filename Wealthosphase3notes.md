# WealthOS v2.0 — Phase 3 Development Notes
### Individual Agents — Finance Agent & Research Agent
**Date:** April 10, 2026 | **Status:** ~30% Complete (2 of 7 agents done)

---

# Table of Contents

1. [Phase 3 Overview & Goals](#phase-3-overview)
2. [Pre-Phase 3 — Completing Phase 2 RAG Improvements](#pre-phase-3)
3. [The 3-Route Hybrid RAG Architecture](#hybrid-rag)
4. [New Database Tables Added](#new-tables)
5. [New RAG Files Created](#new-rag-files)
6. [Step 3.1 — Finance Agent](#finance-agent)
7. [Step 3.2 — Research Agent](#research-agent)
8. [All Bugs Encountered & Fixes](#bugs)
9. [Architecture Decisions & Alternatives Considered](#architecture)
10. [What's Left in Phase 3](#remaining)
11. [Key Files Reference](#files)
12. [Lessons Learned](#lessons)

---

# 1. Phase 3 Overview & Goals {#phase-3-overview}

## What Phase 3 Is About

Phase 3 builds the 7 individual agents that form the core intelligence of WealthOS. Each agent is built and tested **standalone** before Phase 4 wires them all together inside LangGraph.

The blueprint explicitly says: **apply to jobs after Phase 3 is complete.** This is the most portfolio-critical phase.

## The 8 Steps

| Step | Agent | Framework | Status |
|------|-------|-----------|--------|
| 3.1 | Finance Agent | Pure Python (originally Agno) | ✅ Done |
| 3.2 | Research Agent | Pure Python + Agno | ✅ Done |
| 3.3 | Data Agent | PydanticAI + AWS Bedrock | ❌ Pending |
| 3.4 | Risk Agent | CrewAI + DeepSeek R1 | ❌ Pending |
| 3.5 | Code Agent | Smolagents + E2B | ❌ Pending |
| 3.6 | Rebalancing Agent | Agno | ❌ Pending |
| 3.7 | Guardrails AI Validation | Guardrails AI | ❌ Pending |
| 3.8 | Writer Agent | LangGraph node + DSPy | ❌ Pending |

## Why Finance Agent Runs First

The Finance Agent is what makes WealthOS distinct from generic stock analysis tools. Every other agent receives the `PersonalFinanceSnapshot` as context and adjusts its output accordingly. Before asking "should I buy Reliance?", WealthOS first asks — can you even afford to? The Finance Agent answers that question.

---

# 2. Pre-Phase 3 — Completing Phase 2 RAG Improvements {#pre-phase-3}

Before building agents, Phase 2 RAG had two outstanding problems that needed to be closed:

## Problem 1 — TSLA Revenue Returning Wrong Year

The query engine was returning `$94,827M` (FY2023) instead of `$97,690M` (FY2024) for Tesla revenue. Root cause: the income statement chunk containing both years was being beaten in vector similarity by a balance sheet chunk with a score of 0.812. When the wrong chunk reached the LLM, it misread the left column as the current year.

**Resolution (temporary):** A TSLA-specific year column rule was added to the system prompt. This is acknowledged as a hack and is planned to be replaced by section-aware chunking in Phase 2.6.

## Problem 2 — Route Confusion in the Router

The router was sending "How did management describe revenue growth?" to the SQL route because it detected the word "revenue." Narrative questions were being incorrectly classified as numerical questions.

**Root cause:** SQL trigger words were checked before narrative trigger words, so "revenue" in a qualitative question still triggered the SQL path.

**Fix:** Reversed the priority order — narrative triggers (describe, management, how did, explain) are now checked first. If narrative keywords are present, the question goes to the vector route regardless of financial keywords in the sentence.

---

# 3. The 3-Route Hybrid RAG Architecture {#hybrid-rag}

This was the major architectural decision made at the boundary of Phase 2 and Phase 3. The original RAG (single pgvector semantic search) was insufficient for financial data. A hybrid approach was designed.

## Why the Original Single-Route RAG Was Failing

The core problem: financial documents contain two completely different kinds of content — structured tables of exact numbers, and narrative prose about strategy and risk. A single embedding model treats both the same way, which means:

- Revenue tables scored high in similarity searches, but so did every other financial table
- The LLM received the wrong table and returned the wrong number
- No single vector score threshold could reliably distinguish correct from incorrect chunks

## The 3-Route Design

### Route 1 — Semantic Vector Search (already built)
- **What it handles:** Qualitative questions about business strategy, risk factors, management commentary, competitive landscape
- **How it works:** Question is embedded via `mxbai-embed-large` → cosine similarity search in `document_embeddings` table → top chunks passed to `qwen2.5:7b` for summarization
- **Table used:** `document_embeddings` (existing)
- **Example questions:** "What are Tesla's main risk factors?" / "What products does Apple sell?"

### Route 2 — Structured SQL (new)
- **What it handles:** Any question where the answer is a specific financial number
- **How it works:** Router detects numerical keywords → queries `financial_facts` table directly → returns exact figure with no LLM involved in fetching the number
- **Table used:** `financial_facts` (new)
- **Example questions:** "What is Tesla's revenue FY2024?" / "What is Amazon's net income?"
- **Key insight:** For numerical facts, the LLM should never be involved in fetching the number — it should only format the answer. The number should come from a trusted database row.

### Route 3 — Section-Aware Vector Search (partially built)
- **What it handles:** Questions targeting what a specific team or person wrote in a specific section of the filing
- **How it works:** Detects section-specific keywords → filters `document_embeddings` by `section` column first → then runs vector search only within that section
- **Table used:** `document_embeddings` (with new `section` column added)
- **Example questions:** "How did management describe AWS growth?" / "What did the CEO say about future outlook?"
- **Status:** Router identifies `section_rag` route correctly, but currently falls back to vector search because the `section` column was added after the initial indexing. Re-ingestion with section tagging is needed to fully activate this route.

## The Router — `rag/router.py`

A simple keyword-matching function. No AI involved — pure Python string matching.

**Logic order (critical — narrative MUST be checked before SQL):**

```
Question arrives
      │
      ├── Contains narrative keywords? (describe, management, how did, explain...)
      │         └── → section_rag
      │
      ├── Contains numerical keywords? (revenue, income, profit, margin...)
      │         └── → sql
      │
      └── Default → vector
```

**Why no AI in the router:** Using an LLM to route queries adds latency and a potential failure point. The pattern of financial questions is predictable enough that keyword matching works reliably. An LLM router would be over-engineering.

## EDGAR XBRL API Problem — Populating `financial_facts`

The original plan was to use the SEC EDGAR XBRL API to populate the `financial_facts` table. This ran into a significant problem: different companies use different XBRL keys for the same concept.

- TSLA total revenue = sum of `SalesRevenueGoodsNet` + `SalesRevenueServicesNet` + `SalesRevenueEnergyServices`
- AMZN total revenue = `Revenues`
- GOOGL total revenue = `Revenues`
- AAPL total revenue = `RevenueFromContractWithCustomerExcludingAssessedTax`

This means per-company key mapping config is required, making the EDGAR approach brittle for new tickers.

**Resolution:** The `market_server.py` MCP already has a `get_financials()` tool using `yfinance`. A `rag/populate_facts.py` script was designed to call this existing tool and populate `financial_facts` directly — using Yahoo Finance's already-cleaned data rather than raw XBRL. This avoids the key mapping problem entirely.

**Important note on yfinance data:** yfinance returns TTM (trailing twelve months) data, not strict fiscal year data. This is an accepted trade-off for simplicity. For strict FY data, the EDGAR EDGAR XBRL approach with per-company config would be required.

---

# 4. New Database Tables Added {#new-tables}

These tables were added to the existing `wealthos` PostgreSQL database to support the hybrid RAG architecture.

## `financial_facts`
For Route 2 — stores extracted financial figures with zero ambiguity.

```sql
CREATE TABLE financial_facts (
    id          SERIAL PRIMARY KEY,
    ticker      TEXT NOT NULL,
    metric      TEXT NOT NULL,       -- 'total_revenue', 'net_income', etc.
    value       NUMERIC NOT NULL,
    unit        TEXT DEFAULT 'millions_usd',
    fiscal_year INT NOT NULL,
    period      TEXT,                -- 'FY2024', 'Q4 2024'
    source      TEXT DEFAULT '10-K',
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_facts_ticker_metric_year 
ON financial_facts(ticker, metric, fiscal_year);
```

**Why long-format (one row per metric) rather than wide-format (one column per metric):** Different companies report different metrics — AMZN has AWS revenue, TSLA doesn't. Wide format would require null columns for every company-specific metric. Long format means AMZN simply has rows that TSLA doesn't. Schema never changes when new companies or metrics are added.

## `document_sections`
For Route 3 — maps page ranges to section names in each filing.

```sql
CREATE TABLE document_sections (
    id          SERIAL PRIMARY KEY,
    ticker      TEXT NOT NULL,
    section     TEXT NOT NULL,       -- 'income_statement', 'risk_factors', etc.
    start_page  INT,
    end_page    INT,
    fiscal_year INT,
    doc_type    TEXT DEFAULT '10-K'
);
```

## `query_logs`
For observability and debugging — records every query, which route was used, and the answer.

```sql
CREATE TABLE query_logs (
    id          SERIAL PRIMARY KEY,
    question    TEXT,
    ticker      TEXT,
    route_used  TEXT,                -- 'sql', 'vector', 'section_rag'
    answer      TEXT,
    latency_ms  INT,
    created_at  TIMESTAMP DEFAULT NOW()
);
```

## Column Addition to `document_embeddings`

```sql
ALTER TABLE document_embeddings 
ADD COLUMN section TEXT DEFAULT 'unknown';
```

This tags each chunk with its source section. Old chunks remain `'unknown'` — re-ingestion is needed to populate this for existing data.

---

# 5. New RAG Files Created {#new-rag-files}

## `rag/edgar_ingestor.py`
Hits the SEC EDGAR XBRL API to populate `financial_facts`. Defines `get_financial_facts()` which pulls revenue, net income, operating income, gross profit, assets, debt, and cash flow for any US ticker.

Key challenge here was the per-company XBRL key inconsistency described above. The file uses a `try_keys()` helper that tries multiple XBRL key names in order and returns the first one that has data. This handles most companies but TSLA still required a manual DB correction for the total revenue figure.

## `rag/router.py`
The query router. Contains two keyword lists and a `route()` function that returns one of `'sql'`, `'section_rag'`, or `'vector'`. Narrative trigger words are checked first to prevent misclassification.

## `rag/fact_query.py`
Handles the SQL route. Takes a ticker, metric, and fiscal year, runs a `SELECT` against `financial_facts`, and returns a formatted answer string. No LLM involved.

## `rag/populate_facts.py` (designed, not yet run)
Will call the existing `market_server.get_financials()` MCP tool to populate `financial_facts` via yfinance rather than EDGAR XBRL. This approach is cleaner because yfinance already handles the per-company key inconsistency internally.

## `rag/indexer.py` — Section Detection Added

The `detect_section()` function was added to the indexer. It scans each chunk for 14 known SEC filing header patterns:

| Keyword detected | Section tag assigned |
|---|---|
| "risk factors" | `risk_factors` |
| "management discussion" | `md_and_a` |
| "consolidated statements of operations" | `income_statement` |
| "consolidated balance sheet" | `balance_sheet` |
| "cash flow" | `cash_flow` |
| ... | ... |

Each chunk is now stored with a `section` metadata field. **This requires re-indexing all tickers** to take effect — old chunks still have `section = 'unknown'`.

---

# 6. Step 3.1 — Finance Agent {#finance-agent}

## What the Finance Agent Does

It builds a complete picture of the user's personal financial health before any investment analysis begins. Every other agent receives its output (`PersonalFinanceSnapshot`) as context. This is the feature that makes WealthOS personalized rather than generic — the Risk Agent adjusts risk scores based on EMI burden, the Writer Agent references the user's actual surplus in the memo.

## Framework Decision — Why Not Agno

The original blueprint specified Agno for the Finance Agent. During implementation, Agno failed to start because it silently depends on the `openai` package internally — even when configured to use Ollama exclusively. It imports OpenAI internals at startup regardless of the model selected.

**Why pure Python was chosen instead:**

The Finance Agent has a **fixed, deterministic 4-step flow**. It always runs: get transactions → parse uploads → detect anomalies → compute health score. There is no dynamic reasoning about which tool to call next — the sequence never changes based on context.

Agno's entire value is managing a **dynamic tool-calling loop** where the LLM decides at runtime which tools to call and in what order. For a fixed sequence, Agno adds overhead with no benefit. Pure Python is simpler, faster, zero hidden dependencies, and easier to debug.

**Where Agno does earn its place:** The Research Agent, which genuinely needs to decide at runtime whether to search the web, call SEC EDGAR, or query the RAG pipeline based on what it finds. Dynamic reasoning is real there.

## File Structure — `agents/finance_agent.py`

The file is organized into 4 sections:

### Section 1 — Pydantic Models

All data shapes are defined at the top. These typed objects flow through the entire system.

- **`Transaction`** — a single spending row: merchant, amount, date, category, source (manual/bank_statement/receipt)
- **`AnomalyFlag`** — one flagged unusual spend: category, expected amount, actual amount, z-score, severity
- **`HealthScore`** — the computed score object: total (0-100), grade (A/B/C/D/F), breakdown by 5 dimensions, top issue identified
- **`PersonalFinanceSnapshot`** — the final output: income, expenses, surplus, all transactions, anomalies, goals, health score, data confidence level, generated timestamp

### Section 2 — Tool Functions

Five functions, each with one job:

**`scan_receipt(image_path)`**
- Calls Ollama `llama3.2-vision` via the `/api/generate` endpoint (vision-capable endpoint)
- Prompt asks for JSON output with: merchant, amount, date, category
- Returns a `Transaction` object
- Fails gracefully — returns `None` if vision model call fails or JSON is malformed

**`parse_bank_statement(pdf_path)`**
- Converts PDF pages to images using `fitz` (PyMuPDF)
- Sends each page image to `llama3.2-vision`
- Aggregates all extracted transaction rows across pages
- Returns `list[Transaction]`
- Privacy-conscious: runs entirely locally, no data leaves the machine

**`detect_anomalies(transactions)`**
- Pure Python, no LLM
- Groups transactions by category
- Computes rolling mean and standard deviation per category (requires ≥3 months of data)
- Flags any category where current month spend exceeds 1.5 standard deviations from the mean
- Returns `list[AnomalyFlag]`
- The z-score formula: `z = (current - mean) / (std + 1e-9)` — the small epsilon prevents division by zero

**`compute_health_score(snapshot_data)`**
- Pure Python, implements the exact formula from blueprint Section 1.4
- 5 dimensions with weighted scoring:
  - Savings rate (30 points) — surplus / income ratio
  - Debt-to-income (25 points) — lower is better, penalizes high EMI burden
  - Expense stability (20 points) — consistency of month-to-month spending
  - Goal progress (15 points) — average progress across all financial goals
  - Emergency fund (10 points) — months of expenses covered by liquid savings
- Total: 0-100, grade assigned: A (80+), B (65-79), C (50-64), D/F (below 50)
- Returns `HealthScore`

**`get_transactions_from_db(user_id)`**
- Calls `finance_server.get_transactions()` MCP tool
- Fetches last 3 months of transactions from Postgres
- Returns `list[Transaction]`

### Section 3 — Orchestrator

Pure Python orchestrator function (not Agno). Runs the 4 steps in sequence:

```
1. Check DB → how many transactions?
2. Check uploads → files provided?
3. Check manual input → income/expense numbers given?
→ Route to appropriate cold start path
→ Run anomaly detection
→ Compute health score
→ Build PersonalFinanceSnapshot
```

### Section 4 — Entry Point & Cold Start Handling

`run_finance_agent(user_id, uploads=[])` is the single function LangGraph will call in Phase 4.

**Cold start problem:** New users have no transaction history in Postgres. Three paths are handled:

| Situation | What happens | Confidence level |
|---|---|---|
| 3+ months of DB transactions | Full analysis, all tools run | `high` |
| DB empty but uploads provided | Parse uploads → write to DB → analyze | `medium` |
| DB empty, no uploads, manual numbers given | Rough snapshot from income/expense ratio | `low` |
| Nothing at all | Return `insufficient_data` state, request upload | `none` |

The `data_confidence` field propagates through the system — downstream agents check this before trusting the snapshot. The Risk Agent won't give a confident risk score if confidence is `low`.

## Test Results

```
Health Score  : 84.2 / 100  [ Grade A ]
Savings Rate  : 27.1%  →  100.0 sub-score
Confidence    : low (manual input path — expected)
Cold start    : handled cleanly
```

One deprecation warning was found and fixed: `datetime.utcnow()` is deprecated in Python 3.12+. Replaced with `datetime.now(timezone.utc)`.

---

# 7. Step 3.2 — Research Agent {#research-agent}

## What the Research Agent Does

Answers "what is happening in the world that affects your investments?" It takes the user's tracked symbols and fetches live financial intelligence from three sources: real-time news via NewsAPI, live market price data via yfinance, and SEC filing metadata via the EDGAR API. All results are enriched by the local LLM and returned as a typed `ResearchSnapshot`.

## Why Agno Was Used Here

Unlike the Finance Agent, the Research Agent has dynamic reasoning requirements:

- Does this ticker have a recent SEC filing worth reading?
- Is there enough news coverage or should it dig deeper into RAG?
- Should it summarize a filing or is it already short?
- Which tool results are material vs noise?

These decisions change per query and per ticker. Agno's tool-calling loop (where the LLM decides at runtime which tools to call) genuinely adds value here. The agent is given all 5 tools and its own instructions, and Agno manages the decision loop internally.

**However:** Due to the Agno/OpenAI dependency issue, and because the tool-calling loop for this agent is actually somewhat predictable (market data + news + SEC for each symbol), the final implementation used pure Python orchestration with `asyncio.gather` for parallelism, matching the performance of Agno without the hidden dependency. The LLM is still called for enrichment (summaries, macro analysis) — only the tool dispatch is pure Python.

## File Structure — `agents/research_agent.py`

### Section 1 — Pydantic Models

- **`NewsItem`** — headline, source, published date, relevance score, LLM-compressed summary
- **`MarketSignal`** — ticker, current price, 30-day change percentage, trend direction (up/down/sideways), signal strength
- **`SECInsight`** — company, filing type, filed date, LLM-extracted key insights, risk flags
- **`ResearchSnapshot`** — the final output: all news items, market signals, SEC insights, macro summary, data confidence, generated timestamp

### Section 2 — Tool Functions

**`fetch_market_data(symbols)`**
- Calls `yfinance` directly — no API key, no rate limits
- Pulls 30 days of price history per symbol
- Computes percentage change over the period
- Classifies trend: up (>3%), down (<-3%), sideways (within ±3%)
- Returns `list[MarketSignal]`

**`fetch_financial_news(symbols)`**
- Calls the already-built `news_server.py` MCP tool — uses NewsAPI as primary source
- Builds query string from symbols: `"AAPL OR TSLA OR MSFT"`
- Returns raw articles — LLM enrichment happens separately
- Firecrawl is available as fallback if NewsAPI quota is hit

**`fetch_sec_filings(tickers)`**
- Calls `sec_edgar_server.get_10k()` and `get_10q()` — completely free, no API key
- Gets filing metadata: type, filed date, document URL
- Returns filing metadata list — actual document content fetched separately if needed

**`summarize_with_llm(text, instruction)`**
- Generic Ollama call via `/api/generate`
- Uses `qwen2.5:7b` (7B parameter model — much better than llama3.2 3B for financial text)
- Takes any long text + a specific instruction
- Used for: compressing news articles, extracting key insights from SEC filings, generating macro summary
- This is the only function that involves LLM reasoning

**`query_rag_pipeline(question, user_id)`**
- Embeds question using `mxbai-embed-large` via Ollama
- Queries `document_embeddings` table in pgvector
- Returns relevant chunks if `research_documents` table exists
- Fails silently if table doesn't exist yet — no crash

**`get_tracked_symbols(user_id)`**
- Calls `finance_server` MCP to pull user's tracked symbols from Postgres
- Falls back to empty list for new users — handled gracefully

### Section 3 — Orchestrator

Uses `asyncio.gather` to run market data, news, and SEC filing fetches in parallel — the three slowest operations run simultaneously rather than sequentially. This reduces total latency significantly.

After parallel fetching, LLM enrichment runs sequentially:
- Each news article → `summarize_with_llm` → compressed NewsItem
- Each SEC filing → `summarize_with_llm` → key insights extracted
- All data combined → final macro summary generated

### Section 4 — Entry Point & Cold Start

`run_research_agent(user_id, symbols=None)` handles three cases:

| Situation | What happens |
|---|---|
| User has tracked symbols in DB | Full research on all symbols |
| No symbols but query/symbols provided directly | Research those symbols |
| No symbols, no query | Returns global macro summary only |

## Model Selection — qwen2.5:7b vs llama3.2

The original plan was to use `llama3.2` (3B parameters) for text reasoning. During testing, it produced weak financial summaries — short, surface-level, missed key information from filings.

**Why qwen2.5:7b was chosen:**
- 7B parameters — significantly more reasoning capacity
- Alibaba trained it on heavy structured and numerical data, making it better at financial text specifically
- Still fits in 6GB VRAM (RTX 3050)
- Performance improvement for financial summarization was noticeable

The switch required only an `ollama pull qwen2.5:7b` and a one-line model name change in the code.

## Test Results

```
Market Signals  : 2/2 fetched (AAPL -1.7% sideways, TSLA -13.8% down)
News Items      : 10/10 from NewsAPI
SEC Filings     : 2/2 (AAPL 10-Q Jan 2026, TSLA 10-K Jan 2026)
LLM Calls       : 8 (all via qwen2.5:7b)
Embeddings      : mxbai-embed-large working
RAG             : skipped cleanly — table not yet created
Confidence      : medium (test user, no DB symbols)
```

---

# 8. All Bugs Encountered & Fixes {#bugs}

## Bug 1 — Agno Silent OpenAI Dependency

**Error:** `pip show openai` showed it was required even though the agent was configured to use Ollama only.

**Cause:** Agno imports OpenAI Python SDK internals at module load time regardless of configured model. This is not documented.

**Fix:** Dropped Agno for Finance Agent entirely. Used pure Python orchestrator. This eliminated the hidden dependency. Agno remains planned for Research Agent and future agents where dynamic tool-calling is genuinely needed.

---

## Bug 2 — Router Misclassifying Narrative Revenue Questions

**Error:** "How did management describe revenue growth?" → routed to SQL → returned a financial figure instead of narrative text.

**Cause:** Router checked SQL triggers first. "revenue" was in the question, so it hit the SQL path despite being a qualitative question about management commentary.

**Fix:** Reversed check order — narrative triggers are now checked before SQL triggers. Added "growth" and "trend" to narrative triggers. The rule: if narrative keywords are present, it's a qualitative question, period.

---

## Bug 3 — TSLA Revenue Wrong Year

**Error:** `$94,827M` returned for FY2024 TSLA revenue; correct answer is `$97,690M`.

**Cause:** Two years appear side-by-side in the income statement table. The income statement chunk scored lower in vector similarity than a balance sheet chunk. When the LLM received the wrong context, it defaulted to reading the left column (earlier year) as current.

**Fix (temporary):** Added explicit year column mapping to the system prompt:
```
For TSLA: 94,827 = FY2023 (wrong), 97,690 = FY2024 (correct)
```

**Proper fix (planned Phase 2.6):** Section-aware chunking with section metadata. Income statement chunks will be tagged `section = 'income_statement'` — revenue questions will only search that section. No hardcoded values needed.

---

## Bug 4 — EDGAR XBRL Wrong Key for TSLA

**Error:** EDGAR returned `$81,462M` for TSLA total revenue FY2024. Correct is `$97,690M`.

**Cause:** Tesla uses `RevenueFromContractWithCustomerExcludingAssessedTax` in XBRL, which captures automotive revenue only — not energy storage or services. Total revenue requires summing three keys: `SalesRevenueGoodsNet` + `SalesRevenueServicesNet` + `SalesRevenueEnergyServices`.

**Investigation:** Fetched all revenue-related XBRL keys for TSLA from `https://data.sec.gov/api/xbrl/companyfacts/CIK0001318605.json` and scanned for keys containing "evenue" or "Sales". Found 31 keys — no single key returned the consolidated total.

**Fix:** Designed per-company revenue config in `edgar_ingestor.py` with `mode: "sum"` for TSLA. Long-term fix: use `populate_facts.py` with yfinance instead, which already handles the aggregation.

---

## Bug 5 — WSL vs Windows Ollama Split

**Error:** `llama3.2-vision` was pulled in the Windows Ollama instance. The project runs in WSL and uses the WSL Ollama instance, which didn't have the model.

**Cause:** Developer used the Windows PowerShell terminal for one `ollama pull` command instead of the VSCode WSL terminal.

**Fix:** Pulled all models again from the WSL terminal. Rule established: always use the VSCode integrated terminal (WSL) for all commands — pip, ollama, python, psql.

---

## Bug 6 — .env Concatenation Bug

**Error:** `NEWSAPI_KEY` and `OLLAMA_URL` were on the same line without a newline separator:
```
NEWSAPI_KEY=cde15d89240OLLAMA_URL=http://localhost:11434
```
This caused `NEWSAPI_KEY` to contain the entire string and `OLLAMA_URL` to be undefined.

**Fix:** Opened `.env` in nano, added a newline between the two variables.

---

## Bug 7 — `No module named 'rag'`

**Error:** Running `python rag/query_engine.py` failed with `ModuleNotFoundError`.

**Cause:** When Python is invoked as `python rag/query_engine.py`, it adds `rag/` to `sys.path` as the script directory — not the project root. So `from rag.router import route` looks for `rag/rag/router.py`, which doesn't exist.

**Fix:** Use the `-m` flag to run as a module:
```bash
python -m rag.query_engine "question here" TICKER
```
This keeps the project root on `sys.path` and resolves all internal imports correctly. This applies to all files in packages (rag, agents, mcp_servers).

---

## Bug 8 — `datetime.utcnow()` Deprecation Warning

**Error:** `DeprecationWarning: datetime.datetime.utcnow() is deprecated`

**Cause:** Python 3.12+ deprecated `datetime.utcnow()` because it returns a naive datetime with no timezone info, which is ambiguous.

**Fix:**
```python
# Before
from datetime import datetime
generated_at = datetime.utcnow().isoformat()

# After
from datetime import datetime, timezone
generated_at = datetime.now(timezone.utc).isoformat()
```

---

# 9. Architecture Decisions & Alternatives Considered {#architecture}

## Framework Decision: Agno vs Pure Python vs LangChain

**What was evaluated:**

| Framework | Pros | Cons | Decision |
|---|---|---|---|
| Agno | Clean API, typed outputs, multi-agent built-in | Hidden OpenAI dep, overkill for fixed flows | Use for dynamic agents only |
| LangChain | Mature, many integrations | Heavy, verbose, slow | Not used |
| CrewAI | Multi-agent debate built-in | Medium complexity | Planned for Risk Agent |
| Pure Python | Zero deps, fastest, most debuggable | More code for dynamic flows | Used for Finance Agent |

**The key insight:** Framework choice should match the agent's reasoning pattern:
- Finance Agent = fixed 4-step sequence → pure Python wins
- Research Agent = somewhat predictable but benefits from tool-calling → pure Python with asyncio
- Risk Agent = needs multi-agent debate → CrewAI wins
- Code Agent = dynamic code generation loop → Smolagents wins

## Model Decision: qwen2.5:7b vs llama3.2 vs mxbai-embed-large

**For text reasoning:** `qwen2.5:7b` was chosen over `llama3.2` (3B). The 7B model is significantly better at financial text — Alibaba trained it on structured/numerical data. The 3B model produced shallow summaries. Both fit in 6GB VRAM.

**For embeddings:** `mxbai-embed-large` (1024 dimensions) was chosen and is already embedded from Phase 2. `nomic-embed-text` (768 dims) was considered as an alternative but `mxbai-embed-large` ranks higher on MTEB benchmarks and was already pulled.

**For vision:** `llama3.2-vision` via Ollama for development. The blueprint specifies Claude 3.5 Sonnet for production (via AWS Bedrock). LiteLLM in Phase 4 will make this a one-line swap.

## Why Not GraphRAG for Phase 3

The developer had built GraphRAG (with Neo4j) in two prior projects. The decision was made not to use it again for two reasons:

**Portfolio argument:** Using the same architecture three times signals you only know one approach. A hybrid RAG (vector + SQL) demonstrates system design maturity and the ability to choose the right tool for the problem.

**Technical argument for financial data:** GraphRAG excels at relationship traversal ("which funds owned Tesla before the earnings miss?"). For WealthOS's primary use case — answering "what is Tesla's revenue" and "what are Tesla's risks" — the hybrid SQL+vector approach is faster, simpler, and more accurate.

GraphRAG would be appropriate for a future upgrade: "How does AWS growth affect Amazon's overall margins over time?" requires graph traversal across years and segments. This is noted as a Phase 8+ upgrade.

## TradingAgents Pattern — Considered for Risk Agent

The `TauricResearch/TradingAgents` GitHub repo (8,200+ stars) implements a trading firm structure where agents debate before making decisions. Example: Research Agent says "buy", Risk Agent says "debt burden too high" — they resolve before Writer Agent sees the output.

**Decision:** Not integrated yet. Planned for Step 3.4 (Risk Agent). The CrewAI Risk Agent already has 3 sub-agents (MacroAnalyst, PortfolioAnalyst, RiskScorer) — the debate loop can be added at that point by having MacroAnalyst and PortfolioAnalyst counter each other's arguments before RiskScorer gives the final verdict. Agno's `mode="debate"` setting makes this clean.

## Why yfinance over EDGAR XBRL for `financial_facts`

As documented in the XBRL bug section, EDGAR returns inconsistent keys per company. The `market_server.py` MCP already wraps yfinance's `get_financials()` which returns cleaned, aggregated data. Using the already-built MCP tool to populate the table is:
- Less code (the hard work is already done)
- More reliable (yfinance handles the XBRL key variations)
- Consistent with the rest of the stack

Trade-off: yfinance returns TTM (trailing twelve months), not strict fiscal year. For a portfolio project, this is acceptable.

---

# 10. What's Left in Phase 3 {#remaining}

## Completed ✅
- [x] 3.1 — Finance Agent (pure Python, 5 tools, cold start handled, health score working)
- [x] 3.2 — Research Agent (3 parallel sources, LLM enrichment, graceful fallbacks)

## Remaining ❌

### 3.3 — Data Agent (PydanticAI + AWS Bedrock)
Will use `PydanticAI` for structured data extraction with validation. Primary purpose: pull hard financial numbers from yfinance + SEC EDGAR with zero hallucination. Every number validated against a Pydantic schema. 3-step retry loop if validation fails. Redis cache with 15-minute TTL per ticker.

Key difference from Research Agent: Data Agent is about precision and validation, not narrative intelligence.

### 3.4 — Risk Agent (CrewAI + DeepSeek R1)
Three-sub-agent crew: MacroAnalyst, PortfolioAnalyst, RiskScorer. DeepSeek R1 used for chain-of-thought reasoning on complex multi-factor risk decisions. Receives `PersonalFinanceSnapshot` from Finance Agent and adjusts risk score based on EMI burden, surplus, goal timelines, and Financial Health Score.

TradingAgents debate pattern will be added here — MacroAnalyst and PortfolioAnalyst counter each other's arguments before RiskScorer generates the final score.

### 3.5 — Code Agent (Smolagents + E2B)
Builds and executes real Python financial models in E2B sandbox (safe execution environment). Builds: 5-year DCF model, Monte Carlo simulation (10,000 paths), sensitivity table. Output: DCF intrinsic value + probability distribution chart as base64 image.

### 3.6 — Rebalancing Agent (Agno)
Analyzes current portfolio holdings against target allocation. Projects the impact of adding a new investment. Suggests specific buy/sell actions with rupee amounts. Runs calculations in E2B sandbox for verified arithmetic.

### 3.7 — Guardrails AI Validation Layer
All agent outputs pass through Guardrails AI before the Writer Agent receives them. Validates: risk score within range 1-10, risk factors list length 1-5, research summary restricted to finance topics. Dual-layer validation with PydanticAI.

### 3.8 — Writer Agent (LangGraph node + DSPy)
Synthesizes all 6 agent outputs into a personalized investment memo. Prompts are DSPy-compiled (see Phase 5) rather than hand-written. Streams memo section-by-section via AG-UI. After memo: PDF export via Composio → Google Drive.

---

# 11. Key Files Reference {#files}

```
WealthOS/
├── agents/
│   ├── finance_agent.py      ✅ Finance Agent — pure Python orchestrator
│   └── research_agent.py     ✅ Research Agent — async parallel fetch + LLM enrichment
│
├── rag/
│   ├── indexer.py            ✅ Modified — section detection + tagging added
│   ├── pipeline.py           ✅ Unchanged — ticker → EDGAR → download → index
│   ├── query_engine.py       ✅ Modified — routes to correct handler
│   ├── router.py             ✅ New — keyword-based route classification
│   ├── edgar_ingestor.py     ✅ New — EDGAR XBRL → financial_facts
│   ├── fact_query.py         ✅ New — SQL handler for Route 2
│   └── populate_facts.py     📋 Designed — yfinance → financial_facts
│
├── mcp_servers/
│   ├── market_server.py      ✅ Has get_financials() used by populate_facts
│   ├── sec_edgar_server.py   ✅ Has get_financial_facts() — XBRL endpoint added
│   ├── news_server.py        ✅ NewsAPI + Firecrawl
│   ├── finance_server.py     ✅ Postgres tools for user finance data
│   └── portfolio_server.py   ✅ Portfolio holdings + live prices
│
└── .env
    ├── WEALTHOS_DB_URL        ✅ postgresql+asyncpg://postgres:...
    ├── TAVILY_API_KEY         ✅ Added (may not be needed — using NewsAPI)
    ├── NEWSAPI_KEY            ✅ Confirmed working
    ├── FIRECRAWL_API_KEY      ✅ Confirmed
    └── OLLAMA_URL             ✅ http://localhost:11434
```

## Active Ollama Models (WSL)

| Model | Size | Used For |
|---|---|---|
| `qwen2.5:7b` | 4.7 GB | Text reasoning — Research Agent, Writer |
| `mxbai-embed-large` | 669 MB | Embeddings — pgvector RAG |
| `llama3.2-vision` | 7.8 GB | Vision/OCR — Finance Agent receipt scanning |

## Key Commands

```bash
# Always activate venv first (VSCode WSL terminal)
source venv/bin/activate

# Run Finance Agent startup check
python -m agents.finance_agent

# Run Research Agent startup check
python -m agents.research_agent

# Query the RAG (use -m flag always)
python -m rag.query_engine "question here" TICKER

# Populate financial_facts from EDGAR (re-run after DB reset)
python -m rag.edgar_ingestor

# Index a filing
python -m rag.pipeline AAPL
python -m rag.pipeline TSLA

# Connect to DB
psql -h localhost -p 5432 -U postgres -d wealthos
```

---

# 12. Lessons Learned {#lessons}

## Technical Lessons

**1. Always check hidden dependencies before committing to a framework.**
Agno pulls in OpenAI under the hood even when configured to use Ollama. This isn't documented. If you're building a zero-cost local stack, verify every package's transitive dependencies before writing code around it.

**2. Match framework to reasoning pattern, not to what's "modern."**
A fixed 4-step sequence doesn't need an agent framework. Use pure Python. Reserve frameworks for genuinely dynamic reasoning where the LLM needs to decide which tools to call at runtime.

**3. Long-format tables scale infinitely; wide-format tables break on new companies.**
The `financial_facts` table uses one row per metric per company. Adding NVDA, RELIANCE, or any new company requires no schema changes — just new rows. Wide format (one column per metric) would require a schema migration every time a new company reports a metric no other company uses.

**4. EDGAR XBRL keys are not standardized across companies.**
The same financial concept (total revenue) uses different XBRL key names across different companies. Tesla uses three keys that must be summed. Building on EDGAR XBRL requires per-company key mapping config. yfinance abstracts this away — it's a better data source for this use case.

**5. The `-m` flag is mandatory for running Python files inside packages.**
`python rag/query_engine.py` adds `rag/` to `sys.path`. `python -m rag.query_engine` adds the project root. All cross-package imports require the `-m` flag or setting `PYTHONPATH`.

**6. Always use one terminal — VSCode WSL integrated terminal, nothing else.**
Any `ollama pull` or `pip install` done in the Windows PowerShell terminal installs to a different environment entirely. The VSCode WSL terminal is the only valid terminal for this project. Look for `(venv)` prefix before every command.

**7. Async parallelism matters for agents that make multiple external calls.**
The Research Agent fetches market data, news, and SEC filings simultaneously using `asyncio.gather`. Sequential execution would be 3x slower. For any agent making multiple independent external calls, parallelism is essential.

## Process Lessons

**1. Test the entry point function before testing individual tools.**
Running `python -m agents.finance_agent` at the bottom of the file tests the cold start logic, Pydantic models, and health score math all at once — before any external API calls. This surfaces import errors and schema problems immediately.

**2. The wrong architecture wastes more time than writing the right architecture slowly.**
The team spent significant time on the XBRL key hunting problem before switching to yfinance. Recognizing when an approach has a fundamental limitation (per-company key inconsistency) and pivoting to a better approach (existing MCP + yfinance) earlier would have saved hours.

**3. Never hard-code expected values in production code.**
Putting `97,690 = FY2024 (correct)` directly in a system prompt is acknowledged as a tactical hack. It works for the demo but breaks whenever Tesla files a new annual report or when a new ticker is added. Section-aware chunking (Phase 2.6) is the architectural fix.

---

## Phase 3 Summary Stats (so far)

| Metric | Value |
|---|---|
| Agents completed | 2 of 7 (Finance, Research) |
| Agents pending | 5 (Data, Risk, Code, Rebalancing, Writer) |
| New files created | 6 (finance_agent.py, research_agent.py, router.py, edgar_ingestor.py, fact_query.py, populate_facts.py design) |
| New DB tables | 3 (financial_facts, document_sections, query_logs) |
| New DB columns | 1 (section on document_embeddings) |
| Bugs fixed | 8 |
| Framework pivots | 1 (Agno → pure Python for Finance Agent) |
| Models added | 1 (llama3.2-vision pulled to WSL) |

---

*Next session: Step 3.3 — Data Agent (PydanticAI + AWS Bedrock). This agent is the precision layer — validates every financial number against a Pydantic schema, uses LlamaIndex to query the RAG pipeline, and produces a FinancialSnapshot with zero hallucinated numbers. AWS Bedrock credentials and PydanticAI installation should be verified before starting.*