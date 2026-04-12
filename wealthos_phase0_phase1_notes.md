# WealthOS — Phase 0 & Phase 1 Build Notes
### Personal study reference — full decisions, challenges, and reasoning

---

## HOW TO USE THESE NOTES

These notes capture everything that happened during Phase 0 (Foundation) and
Phase 1 (MCP Servers) of the WealthOS build. Each section covers:
- What we built and what it does
- Every challenge we hit and exactly how we solved it
- Every architectural decision and the reasoning behind it
- What each file contains, what tools it exposes, and what data it touches

Read top to bottom for a full story, or jump to any section for a specific file.

---

---

# PART 1 — PHASE 0: FOUNDATION & SETUP

---

## What Phase 0 Was About

Before writing a single agent or a single MCP server, Phase 0's job was to make
sure the ground was solid. That means:
- PostgreSQL database running with all tables created
- Redis running for caching
- All credentials in .env and verified working
- Virtual environment set up correctly in WSL
- Project folder structure created on disk

Everything in Phase 1, 2, 3 and beyond depends on Phase 0 being correct. If the
DB schema is wrong, every server that touches Postgres will fail. If Redis isn't
running, every server that caches will silently return stale or broken data.

---

## 0.1 — The PostgreSQL Setup and Its Challenges

### Where Postgres Lives

PostgreSQL was installed directly inside WSL (Windows Subsystem for Linux),
NOT inside Docker. This was deliberate — covered in detail in the Docker section.

Postgres install path: WSL Ubuntu, accessed via `sudo -u postgres psql`
Database name: `wealthos`
Database user: `postgres` (superuser — `wealthos_user` was attempted but caused auth issues)

### The "Wrong Database" Problem

**Challenge:** When connecting to psql without specifying a database, it defaults
to the `postgres` system database, not our `wealthos` database. So `\dt` was
returning "Did not find any relations" even though the tables had already been
created — because we were looking in the wrong place.

**What the error looked like:**
```
postgres=# \dt
Did not find any relations.
```

**Fix:** Always specify the database when connecting:
```bash
sudo -u postgres psql -d wealthos
```
Or switch after connecting:
```sql
\c wealthos
\dt
```

**Lesson:** `\l` lists all databases. Always run it first to confirm which DB
exists before assuming tables are missing.

### The Missing Columns Problem

The initial schema was minimal — the `users` table only had `id`, `email`, and
`created_at`. It was missing `name` and `phone`, which the servers needed.
Similarly, `portfolio_holdings` was missing `asset_type` and `added_at`.

**Challenge:** These mismatches only surfaced when we tried to insert test data or
when asyncpg queries referenced columns that didn't exist.

**Fix:** Used ALTER TABLE to add missing columns without recreating anything:
```sql
ALTER TABLE users ADD COLUMN name TEXT;
ALTER TABLE users ADD COLUMN phone TEXT;
ALTER TABLE portfolio_holdings ADD COLUMN asset_type VARCHAR(20) DEFAULT 'equity';
ALTER TABLE portfolio_holdings ADD COLUMN added_at TIMESTAMPTZ DEFAULT NOW();
```

**Lesson:** The blueprint schema was a starting point, not a final spec. Real-world
usage revealed gaps. Always check `\d tablename` before querying a table.

### The gen_random_uuid() Problem

**Challenge:** The `portfolio_holdings` table `id` column was a UUID PRIMARY KEY
but had no DEFAULT, meaning inserts without providing an explicit UUID would fail
with a not-null constraint violation.

**Error:**
```
ERROR: null value in column "id" of relation "portfolio_holdings"
violates not-null constraint
```

**Fix:**
```sql
ALTER TABLE portfolio_holdings ALTER COLUMN id SET DEFAULT gen_random_uuid();
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- needed for gen_random_uuid()
```

**Lesson:** When defining UUID primary keys, always include `DEFAULT gen_random_uuid()`
in the schema from the start. UUID generation requires either the `pgcrypto` or
`uuid-ossp` extension.

### The pg_hba.conf Authentication Problem

**Challenge:** asyncpg (the async Python Postgres driver) connects via TCP on
127.0.0.1, not via Unix socket. The pg_hba.conf file had `peer` auth for local
connections but `scram-sha-256` for TCP connections. The password set in `.env`
didn't match what postgres actually had set.

**Error:**
```
asyncpg.exceptions.InvalidPasswordError: password authentication failed for user "postgres"
```

**Diagnosis:** Ran `sudo cat /etc/postgresql/*/main/pg_hba.conf | grep -E "local|host"`
and saw:
- `local all postgres peer` — Unix socket connections use peer (no password needed)
- `host all all 127.0.0.1/32 scram-sha-256` — TCP connections need a password

The issue: we'd been using `sudo -u postgres psql` (Unix socket, peer auth, no
password) but asyncpg uses TCP, which requires the actual password.

**Fix:** Reset the password via peer auth (which doesn't need the old password):
```bash
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'wealth123';"
```
Then updated `.env`:
```
WEALTHOS_DB_URL=postgresql+asyncpg://postgres:wealth123@localhost:5432/wealthos
```

**Lesson:** `sudo -u postgres psql` bypasses password auth entirely (uses peer).
But any Python driver connecting via `localhost` uses TCP and needs the actual
password set in Postgres.

### Final Database Schema — All 9 Tables

| Table | Purpose |
|-------|---------|
| users | User accounts — id, email, name, phone |
| analyses | Saved stock analysis results per user |
| personal_finance_snapshots | JSONB snapshots of Finance Agent output |
| portfolio_holdings | User's stock holdings — ticker, quantity, buy price, sector |
| price_alerts | User-set price alert targets |
| token_budgets | LLM token usage tracking per user |
| transactions | Bank/expense transactions — date, amount, type, category |
| financial_goals | User goals — house, car, retirement etc |
| subscriptions | Recurring charges — detected and flagged |

pgvector and pgcrypto extensions enabled for vector search and UUID generation.

---

## 0.2 — The Virtual Environment Challenge

### Windows venv vs WSL

**Challenge:** The original virtual environment was created on Windows
(`python -m venv venv` in PowerShell), which creates a `venv/Scripts/` folder.
WSL expects `venv/bin/` (Linux path). Running `source venv/bin/activate` in WSL
failed because the path doesn't exist in a Windows-created venv.

**Error:**
```
-bash: venv/bin/activate: No such file or directory
```

**Fix 1 (temporary):** Use the Windows path from WSL:
```bash
source venv/Scripts/activate
```

**Fix 2 (permanent):** Delete the Windows venv and create a fresh Linux one:
```bash
rm -rf venv
sudo apt install python3.12-venv   # needed on Debian/Ubuntu
python3 -m venv venv
source venv/bin/activate
```

**Lesson:** When working on a project that runs in WSL, always create the venv
from inside WSL, not from Windows PowerShell. The `venv` folder structure differs
between Windows and Linux.

### Note on `python` vs `python3`

In WSL/Ubuntu, `python` often doesn't exist — only `python3`. All commands in
this project use `python3` explicitly.

---

## 0.3 — The Docker Decision: Why We Didn't Use Docker Compose

### What the Original Plan Said

The blueprint called for a full `docker-compose.yml` with 9 services:
- wealthOS-api, wealthOS-mcp, wealthOS-frontend
- wealthOS-db (PostgreSQL), wealthOS-qdrant, wealthOS-redis
- wealthOS-kafka, wealthOS-ollama, wealthOS-temporal

### Why We Chose Not to Use Docker Compose

**Reason 1 — Postgres was already working in WSL**
We had PostgreSQL installed natively in WSL with all 9 tables created and tested.
Migrating to a Docker container would mean:
- Changing the `DATABASE_URL` to a Docker network address
- Copying all existing data across
- Potentially hitting connection issues between WSL and the Docker network
- Throwing away work that was already done and verified

**Reason 2 — Kafka, Temporal, and Ollama aren't needed yet**
These services are only needed in later phases:
- Kafka → Phase 6 (price alerts)
- Temporal → Phase 4 (durable workflows)
- Ollama → Phase 3 (local LLM)

Spinning them up in Phase 0 just to have them sitting idle adds noise, memory
usage, and surface area for things to break.

**Reason 3 — Docker Compose networking adds complexity**
When the Python backend runs in WSL and Postgres runs in a Docker container, the
`DATABASE_URL` needs to use `host.docker.internal` instead of `localhost`. This
is a subtle but painful source of bugs. Keeping Postgres native in WSL keeps the
connection string simple: `localhost:5432`.

**Reason 4 — Two services are enough for now**
Redis and Qdrant were the only services actually needed for Phase 1 MCP servers.
Both can be started with single `docker run` commands — no compose file needed.

### What We Actually Run

| Service | How It Runs | When Added |
|---------|------------|-----------|
| PostgreSQL | Native in WSL | Phase 0 (done) |
| Redis | `docker run -d --name redis -p 6379:6379 redis:7-alpine` | Phase 0 (done) |
| Qdrant | `docker run -d --name qdrant -p 6333:6333 qdrant/qdrant` | Phase 0 (ready for Phase 2) |
| Kafka | Docker (later) | Phase 6 |
| Temporal | Docker (later) | Phase 4 |
| Ollama | Docker (later) | Phase 3 |

**Key takeaway:** We're not avoiding Docker — we're using it surgically, adding
each service only when its phase actually needs it.

---

## 0.4 — The .env File and Key Management

### Environment Variable Naming Convention

One early confusion: the blueprint used `DATABASE_URL` but our `.env` had
`WEALTHOS_DB_URL`. We standardized on `WEALTHOS_DB_URL` and updated every
server to read that key. This avoids conflicts with other projects and is
more explicit.

### The asyncpg URL Format Problem

asyncpg expects `postgresql://` not `postgresql+asyncpg://` in the raw connection
string, even though SQLAlchemy uses `postgresql+asyncpg://`.

**Solution used in every server that connects to Postgres:**
```python
DATABASE_URL = os.getenv("WEALTHOS_DB_URL", "")
DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
```
This way the `.env` can use either format and the server handles it.

### VS Code Environment Injection Warning

VS Code terminal showed:
```
An environment file is configured but terminal environment injection is disabled.
Enable "python.terminal.useEnvFile" to use environment variables from .env files.
```

This warning is harmless for WealthOS because every server calls `load_dotenv()`
explicitly at the top of the file. The `.env` gets loaded by Python regardless
of whether VS Code injects it into the terminal.

To silence the warning:
```json
// .vscode/settings.json
{ "python.terminal.useEnvFile": true }
```

### Critical: load_dotenv() Placement

**Challenge:** In `news_server.py`, `load_dotenv()` was originally placed after
the Redis initialization and after the `NEWSAPI_KEY` and `NEWSAPI_BASE` constants
were defined. Since `os.getenv()` reads environment variables at the moment it's
called, placing `load_dotenv()` after those calls meant the `.env` file hadn't
been loaded yet, and the key came back as an empty string.

**Symptom:** `get_headlines('AAPL')` returned empty `headlines: []` with
`from_cache: False` — the API was being called but with a blank key, so NewsAPI
returned no results silently.

**Fix:** Always place `load_dotenv()` as the very first thing after imports,
before any `os.getenv()` call anywhere in the file.

```python
# CORRECT ORDER
import os
from dotenv import load_dotenv

load_dotenv()  # ← MUST be before any os.getenv()

NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
```

---

---

# PART 2 — PHASE 1: MCP SERVERS

---

## What MCP Servers Are and Why They Exist

MCP (Model Context Protocol) is an open protocol for exposing tools to AI agents.
Instead of each agent having its own messy functions for fetching data, every agent
calls standardized MCP tools via a consistent interface.

Think of MCP servers as dedicated microservices that agents can call:
- Agent says: "Get me the price of RELIANCE.NS"
- market_server.py handles it: hits yfinance, caches in Redis, returns Pydantic-validated result
- Agent never directly imports yfinance or knows about Redis

This keeps agents clean — they only know about tool names and input/output schemas.
Debugging is also much easier: if DCF numbers are wrong, you know it's
`calculator_server.py`, not the Code Agent itself.

## Why 7 Servers Instead of 11

The original blueprint specified 11 MCP servers. After analysis, 4 were dropped:

| Dropped Server | Why Dropped | What Replaced It |
|---------------|-------------|-----------------|
| macro-mcp | yfinance already has Nifty, Sensex, currency, sector indices | Functions added to market_server.py |
| competitor-mcp | Just yfinance calls with peer lookup logic — too thin for its own server | Functions added to market_server.py |
| alert-mcp | Alerts need a background scheduler to actually fire — a separate MCP server just manages a list, which Postgres already does | APScheduler added in Phase 6 |
| qdrant-mcp | Postgres already has pgvector extension — same vector search capability, zero extra infrastructure | pgvector inside PostgreSQL |

**Result:** The same capabilities, 4 fewer services to run and maintain, and a
simpler infrastructure story.

---

## SERVER 1 — market_server.py

### What It Is

The consolidation of three original servers: `yfinance-mcp`, `macro-mcp`, and
`competitor-mcp`. It's the primary market data layer that most agents will call.
Named `market_server.py` instead of `yfinance_server.py` because it does more
than just fetch yfinance data — it handles macro indices, currency rates, sector
performance, and competitor comparisons.

### Data Source

Yahoo Finance via the `yfinance` Python library. No API key required — yfinance
scrapes Yahoo Finance's public data endpoints.

### Caching

Redis. TTLs:
- Stock prices: 5 minutes (prices change frequently)
- Financials, ratios, company info: 1 hour (changes daily at most)
- Macro data (indices, currencies, sector performance): 1 hour

### Tools Exposed — 10 total

| Tool | What It Does | Key Parameters |
|------|-------------|---------------|
| `get_price(ticker)` | Current price, day change, volume, 52-week range | ticker: e.g. "RELIANCE.NS" |
| `get_financials(ticker)` | P/E, EPS, revenue, margins, debt-to-equity | ticker |
| `get_history(ticker, period)` | OHLCV data for a date range | ticker, period: "1mo", "3mo", "1y" |
| `get_info(ticker)` | Company description, sector, industry, website | ticker |
| `get_recommendations(ticker)` | Analyst buy/hold/sell consensus | ticker |
| `get_market_overview()` | Nifty50, Sensex, BankNifty, S&P500, Nasdaq, Dow | — |
| `get_currency_rates()` | USD/INR, EUR/INR, GBP/INR, Gold, Crude | — |
| `get_sector_performance()` | 8 Nifty sectoral indices sorted by day change | — |
| `get_competitors(ticker)` | Peer list for major Indian stocks via hardcoded map | ticker |
| `compare_stocks(tickers)` | Side-by-side P/E, ROE, margins, market cap | tickers: list of strings |

### How compare_stocks Works

`compare_stocks()` takes a list of tickers, fetches financials for each via yfinance,
and returns a side-by-side comparison table plus a "best-in-class" picks section
that flags which company wins on each metric (P/E, ROE, margins, market cap).

### How get_competitors Works

Uses a hardcoded Python dictionary mapping major Indian tickers to their sector
peers. Example: `"RELIANCE.NS"` → `["ONGC.NS", "BPCL.NS", "IOC.NS"]`. For
tickers not in the map, falls back to looking up the yfinance `sector` field and
returning stocks from that sector.

### Naming Decision

Named `market_server.py` not `yfinance_server.py` because:
1. It does macro data (indices, currencies) which has nothing to do with yfinance
   conceptually, even if yfinance is the data source
2. Competitor comparison is a separate business concept
3. Future-proofing — if we later swap yfinance for a paid data API, the server
   name stays correct

---

## SERVER 2 — sec_edgar_server.py

### What It Is

Fetches SEC filings (10-K annual reports, 10-Q quarterly reports) for US-listed
stocks directly from the SEC's public API. No API key required.

### Data Source

SEC EDGAR (Electronic Data Gathering, Analysis, and Retrieval) — a free public
US government database of all company filings.

### Why No API Key

EDGAR is a US government public service. All data is free and openly accessible.
However, SEC does require automated scripts to identify themselves via a
`User-Agent` header. Without it, requests return 403.

**Required .env entry:**
```
SEC_USER_AGENT=WealthOS yourname@email.com
```
The email doesn't have to be real — it just needs to be there so SEC logs know
which application is making requests.

### How It Works Internally

1. Fetches `https://www.sec.gov/files/company_tickers.json` — a public JSON file
   mapping every ticker to a CIK (Central Index Key, SEC's internal company ID)
2. Uses the CIK to fetch `https://data.sec.gov/submissions/CIK{cik}.json` —
   the full filing history for that company
3. Filters by form type (`10-K` or `10-Q`) and returns the most recent one
4. Returns the direct `.htm` document URL — this URL is what Phase 2's LlamaIndex
   RAG pipeline will use to download and index the filing

### Caching

Redis. TTL: 6 hours. Filings are only published quarterly — they don't change
intraday.

### Tools Exposed — 3 total

| Tool | What It Does | Returns |
|------|-------------|---------|
| `get_10k(ticker)` | Latest annual report (10-K) | ticker, CIK, company name, filing date, accession number, document URL |
| `get_10q(ticker)` | Latest quarterly report (10-Q) | Same structure as 10-K |
| `get_filings_list(ticker, count)` | List of N most recent filings | List of filing objects with type, date, URL |

### Indian Stocks Note

SEC EDGAR only has US-listed companies. Indian stocks (RELIANCE.NS, INFY.NS etc.)
won't be found here. That's expected — for Indian stocks, we'll use Firecrawl to
scrape BSE/NSE annual reports in a later phase.

### Test Results That Confirmed It Working

```
AAPL: 10-K filed 2025-10-31, direct URL to aapl-20250927.htm
MSFT: 10-Q filed 2026-01-28, direct URL to msft-20251231.htm
TSLA: 3 filings — 10-K (Jan 2026) + 2x 10-Q (Oct 2025, Jul 2025)
```

---

## SERVER 3 — news_server.py

### What It Is

Fetches recent financial news headlines for stocks and computes a sentiment score.
Primary source is NewsAPI. Firecrawl is the fallback when NewsAPI quota is hit.

### Data Source

NewsAPI (free tier: 100 requests/day). Requires `NEWSAPI_KEY` in `.env`.

### Caching

Redis. TTL: 30 minutes. News changes frequently but not every few seconds.

### Tools Exposed — 3 total

| Tool | What It Does | Parameters |
|------|-------------|-----------|
| `search_news(query, days)` | Search news articles for any query string | query: "Reliance Q3 results", days: 7 |
| `get_headlines(ticker, count)` | Top N news headlines for a specific stock | ticker, count: default 5 |
| `get_sentiment(ticker)` | Sentiment score for a stock from recent news | ticker |

### How Sentiment Scoring Works (Phase 1 Version)

The Phase 1 sentiment scorer is keyword-based. It checks each headline and description
for positive words (e.g. "profit", "growth", "bullish", "upgrade") and negative
words (e.g. "loss", "debt", "downgrade", "bearish"). Returns:
- A score from -1.0 (fully negative) to +1.0 (fully positive)
- A label: "positive", "neutral", or "negative"
- A breakdown: how many articles fell into each category

In Phase 3, when the Research Agent is built, this gets upgraded to Groq LLM
classification per headline — much more accurate, especially for nuanced headlines.

### Challenges and Fixes

**Challenge 1: Empty results despite valid API key**

Root cause: `load_dotenv()` was placed after `NEWSAPI_KEY = os.getenv(...)`.
The env file hadn't been loaded yet when the key was read.

Fix: Move `load_dotenv()` to immediately after imports, before any `os.getenv()`.

**Challenge 2: `NEWSAPI_BASE is not defined` error**

Root cause: When fixing Challenge 1, the `NEWSAPI_BASE = "https://newsapi.org/v2/everything"`
line was accidentally deleted. It had to be added back.

**Challenge 3: `TypeError: can only concatenate str (not "NoneType") to str`**

Root cause: Some news articles have `None` as their description (missing field).
The sentiment scorer was doing `title + " " + description` without handling None.

Fix:
```python
# Before (breaks on None)
text = (article.get("title", "") + " " + article.get("description", "")).lower()

# After (safe)
text = (article.get("title", "") + " " + (article.get("description") or "")).lower()
```

**Challenge 4: Cached empty results**

After fixing the API key issue, the old empty results were still being served from
Redis cache. Had to flush Redis to force fresh API calls:
```bash
docker exec -it redis redis-cli FLUSHALL
```

**Lesson:** When debugging cache-backed servers, always flush Redis before re-testing.
Otherwise you're testing the cache, not the server.

### Free Tier Noise

NewsAPI free tier returns articles from all sources including non-English ones.
Searches for "AAPL" returned Japanese Apple gadget articles. This is a NewsAPI
free tier limitation, not a bug. The fix (filtering by language in the query) is
already in the server code — `language=en` is passed. Some non-English sources
still slip through.

---

## SERVER 4 — finance_server.py

### What It Is

The personal finance data layer. Reads from the user's transaction history in
PostgreSQL and provides spending analysis, surplus calculation, subscription
auditing, and goal tracking. This is the primary data source for the Finance Agent
which computes the Financial Health Score.

### Data Source

PostgreSQL — specifically the `transactions`, `subscriptions`, `financial_goals`
tables. No external API. No Redis caching (personal data is always fresh).

### Key Design Decision: No External API

Unlike market_server (yfinance) or news_server (NewsAPI), finance_server only
touches our own database. This means:
- No rate limits
- No API costs
- No caching needed (data is always current)
- Personal financial data never leaves our infrastructure

### Database Connection Pattern

All DB operations are async using asyncpg. The connection pool is initialized
once and reused:

```python
DATABASE_URL = os.getenv("WEALTHOS_DB_URL", "")
DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
```

The URL stripping is necessary because asyncpg's `asyncpg.connect()` takes a
plain `postgresql://` URL, not the SQLAlchemy dialect format.

### Tools Exposed — 5 total

| Tool | What It Does | Parameters |
|------|-------------|-----------|
| `get_transactions(user_id, months)` | Fetch raw transactions from Postgres filtered by date range | user_id (UUID string), months: default 3 |
| `analyze_spending(user_id, months)` | Category breakdown + anomaly detection (which categories are unusually high) | user_id, months |
| `get_surplus(user_id, months)` | Total income (credits) minus total expenses (debits) = monthly surplus | user_id, months |
| `get_subscriptions(user_id)` | List recurring charges, flag ones not used in 30+ days | user_id |
| `get_goals(user_id)` | Financial goals with progress percentage and months to target | user_id |

### How Anomaly Detection Works

The `analyze_spending()` tool computes a 3-month average per spending category.
If the current month's spending in any category exceeds the average by more than
1.5 standard deviations (z-score > 1.5), it's flagged as an anomaly.

Example: if food spending averages ₹3,000/month and this month is ₹8,000, the
z-score would be high and the tool flags it: "Food spending is 167% above your
3-month average."

### Test Data Used

Because the database was empty when testing, we inserted a test user and sample
transactions via psql:
```sql
INSERT INTO users (id, email, name)
VALUES ('00000000-0000-0000-0000-000000000001', 'test@wealthos.com', 'Test User');
```
Then 12 sample transactions: 3 months of salary (credits) + expenses across
Food, Groceries, Entertainment, Utilities, Housing.

### The asyncpg Authentication Issue

Covered in detail in Phase 0 notes. asyncpg connects via TCP (127.0.0.1:5432)
which requires the actual Postgres password. The password had to be explicitly
set with `ALTER USER postgres PASSWORD 'wealth123'` and then used in the
`WEALTHOS_DB_URL` environment variable.

---

## SERVER 5 — calculator_server.py

### What It Is

A pure math engine. No database, no external API, no Redis cache. Just financial
formulas implemented in Python and exposed as MCP tools. The Code Agent (Phase 5)
will call these tools to get the inputs it needs for DCF models and Monte Carlo
simulations.

### Data Source

Pure Python / numpy / scipy. All calculations are deterministic — same inputs
always give same outputs.

### Tools Exposed — 13 total

| Tool | What It Does | Formula |
|------|-------------|---------|
| `compound_interest(principal, rate, years)` | Future value of a lump sum | P × (1 + r)^n |
| `loan_emi(principal, rate, months)` | Monthly EMI payment | Standard loan amortization |
| `sip_returns(monthly_amount, rate, years)` | Final corpus from monthly SIP | SIP future value formula |
| `inflation_adjusted(amount, rate, years)` | Real value of money after inflation | P / (1 + r)^n |
| `fire_number(monthly_expenses, withdrawal_rate)` | Corpus needed to retire | Expenses × 12 / withdrawal_rate |
| `xirr(cashflows, dates)` | Actual return rate on a series of investments | Scipy Newton-Raphson |
| `calc_cagr(start, end, years)` | Compound annual growth rate | (end/start)^(1/years) - 1 |
| `calc_pe_ratio(price, eps)` | Price-to-Earnings ratio | price / eps |
| `calc_eps_growth(eps_history)` | EPS growth rate over time | CAGR on EPS values |
| `calc_debt_ratio(debt, equity)` | Debt-to-equity ratio | debt / equity |
| `calc_dcf_inputs(ticker)` | Gather FCF, growth rate, WACC inputs for DCF | Calls yfinance internally |
| `calc_wacc(ticker)` | Weighted Average Cost of Capital | Standard WACC formula |
| `calc_intrinsic_value(fcf, growth, wacc)` | DCF intrinsic value estimate | Multi-year DCF sum |

### Why a Separate Server for Math?

Separation of concerns. The Code Agent shouldn't have pandas and numpy imports
mixed in with its LLM calls. Calculator server handles all financial math in one
place. If a formula is wrong, there's one place to fix it.

Also: the Code Agent runs Python in an E2B sandbox (Phase 5). Pre-computing DCF
inputs via calculator_server before passing them to E2B is more reliable than
having E2B fetch yfinance data itself inside the sandbox.

### New Dependency

`scipy` was added to requirements for the `xirr()` function which uses
Newton-Raphson root finding. Install:
```bash
pip install scipy
```

---

## SERVER 6 — tax_server.py

### What It Is

Indian tax calculation engine. Fully codified implementation of Indian Income Tax
rules for FY 2025-26. Pure Python — no external API, no database, no caching.

### Why Indian Tax Specifically?

WealthOS is built for Indian retail investors. Indian tax law is complex:
- Two separate regimes (old vs new) with different slabs and deductions
- Multiple investment-linked deductions: 80C (₹1.5L limit), 80D, HRA
- Different capital gains rules: STCG vs LTCG, different rates for equity vs MF
  vs real estate, different holding period thresholds
- Advance tax schedule with 4 quarterly deadlines

No existing Python library covers all of this accurately. We built it from scratch.

### Tools Exposed — 5 total

| Tool | What It Does | Notes |
|------|-------------|-------|
| `calculate_tax(income, section_80c, hra, other_deductions)` | Computes tax under both old and new regime, returns which saves more | Auto-compares regimes |
| `capital_gains_tax(buy_price, sell_price, quantity, holding_days, asset_type)` | STCG/LTCG based on holding period and asset type | Handles equity, MF, property differently |
| `tax_saving_suggestions(income, investments)` | Lists unutilized deductions the user could still claim | 80C, 80D, HRA |
| `advance_tax_schedule(income)` | Quarterly advance tax deadlines and amounts | 4 installments: 15 Jun, 15 Sep, 15 Dec, 15 Mar |
| `get_optimal_regime(income, deductions)` | Which regime (old vs new) gives lower tax outgo | Simple comparison |

### Capital Gains Rules Coded

- **Equity (stocks/MF):** LTCG if held > 1 year (365 days), else STCG
  - LTCG rate: 12.5% (above ₹1.25L exemption as of FY25-26)
  - STCG rate: 20% (revised from 15% in Budget 2024)
- **Real estate / other:** LTCG if held > 2 years (730 days), else STCG
  - LTCG rate: 20% with indexation
  - STCG: taxed at income slab rate

---

## SERVER 7 — portfolio_server.py

### What It Is

The user's actual investment portfolio layer. Fetches holdings from PostgreSQL
and enriches them with live prices from yfinance to compute current value, P&L,
and sector allocation. The Rebalancing Agent (Phase 5) depends heavily on this
server.

### Data Source

PostgreSQL (`portfolio_holdings` table) for holdings data + yfinance for live
prices. The server calls yfinance directly — it doesn't go through market_server.
This is intentional: portfolio_server is self-contained.

### Caching

No Redis cache on this server. Holdings data is user-specific and needs to be
current. Live prices do have a small yfinance internal cache.

### Tools Exposed — 4 total

| Tool | What It Does | Returns |
|------|-------------|---------|
| `get_holdings(user_id)` | Fetch all portfolio holdings from Postgres | ticker, quantity, avg_buy_price, sector, asset_type |
| `get_portfolio_value(user_id)` | Holdings × live prices | Total current value, per-ticker values |
| `get_pnl(user_id)` | Profit/loss per holding and overall | invested, current value, PnL, PnL%, status, top gainer, top loser |
| `get_allocation(user_id)` | Sector and asset class breakdown | % allocation by sector, concentration warning if any sector > 40% |

### Concentration Warning Feature

`get_allocation()` automatically adds a warning if any single sector is more than
40% of the portfolio. Example output:
```
"⚠️ Technology sector is 67.25% of portfolio — consider diversifying"
```
This feeds directly into the Rebalancing Agent's logic in Phase 5.

### Test Data Results

With RELIANCE.NS (10 shares @ ₹2,400) and INFY.NS (20 shares @ ₹1,500):
- Total invested: ₹54,000
- Total current value: ₹39,834 (both stocks down on test date)
- Technology: 67.25%, Energy: 32.75%
- Concentration warning triggered

These test results correctly showed both stocks at a loss — which is accurate for
those prices on that date. The server is working correctly; the market just wasn't
cooperating.

### Schema Fixes Required During Testing

The `portfolio_holdings` table was missing:
- `asset_type` column — had to be added with `ALTER TABLE`
- `added_at` column — had to be added with `ALTER TABLE`
- `id` column default — had to set `DEFAULT gen_random_uuid()`

All of these were added via ALTER TABLE without recreating the table.

---

## 0.5 — pgvector: Replacing Qdrant MCP Server

### What Qdrant Was Going to Do

The original plan included a dedicated Qdrant vector database server (`qdrant-mcp`)
for storing and searching financial document embeddings. It would have:
- Stored chunked 10-K/10-Q filing text as vectors
- Enabled semantic search: "find sections about Reliance's debt obligations"
- Given agents long-term memory of past analyses

### Why We Replaced It with pgvector

Qdrant is a purpose-built vector database — excellent for production at scale.
But it requires running a separate Docker container, a separate MCP server, and
a separate set of client libraries.

pgvector is a PostgreSQL extension that adds a `vector` data type and vector
similarity search (cosine, L2, inner product) directly into PostgreSQL.

**The case for pgvector:**
- Postgres is already running
- pgvector is already installed (it's in the pgvector/pgvector Docker image)
- Zero new infrastructure
- Vector search queries are just SQL — the same asyncpg connection used everywhere
- For the scale of WealthOS (one user, hundreds of document chunks) pgvector is
  more than fast enough

**The case for Qdrant (why it might matter later):**
- At millions of documents, Qdrant's HNSW index is faster than pgvector's IVFFlat
- Qdrant has built-in payload filtering that's more expressive than SQL WHERE
- If WealthOS scales to many users and large document sets, Qdrant would win

**Decision:** Use pgvector now. If scale demands it later, migration is
straightforward because the embedding logic doesn't change — just the storage backend.

### pgvector Table Created

```sql
CREATE TABLE document_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticker TEXT,
    doc_type TEXT,           -- '10-K', '10-Q', 'news', 'insight'
    chunk_text TEXT,
    embedding vector(1536),  -- 1536 dimensions for OpenAI ada-002
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX ON document_embeddings
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
```

The IVFFlat index is an approximate nearest neighbor index. `lists = 100` means
the index partitions the vector space into 100 clusters — a good starting point
for datasets under 1 million vectors.

---

## The Complete Server Dependency Map

This shows which agents will call which servers in later phases:

| Agent | MCP Servers It Will Call |
|-------|------------------------|
| Finance Agent | finance_server, calculator_server, tax_server |
| Research Agent | news_server, market_server (via news + macro tools) |
| Data Agent | market_server, sec_edgar_server, calculator_server |
| Risk Agent | market_server (macro, competitors), portfolio_server |
| Code Agent | calculator_server (DCF inputs, WACC) |
| Rebalancing Agent | portfolio_server, market_server (live prices) |
| Writer Agent | No MCP servers — only reads other agents' outputs |

---

## Git Commit Strategy

Each MCP server was committed and pushed individually with descriptive messages:

```
feat(mcp): init mcp_servers package + market server
feat(mcp): add sec edgar server — 10-K and 10-Q filing retrieval
feat(mcp): add news server — headlines, sentiment analysis
feat(mcp): add finance server — transactions, anomalies, surplus, subscriptions
feat(mcp): add calculator server — DCF inputs, WACC, CAGR, ratios
feat(mcp): add tax server — 80C, HRA, slab calculations
feat(mcp): add portfolio server — holdings, sector weights, correlation
```

Individual commits per server = cleaner git history and each server shows as a
separate contribution on the GitHub activity graph, which matters for portfolio
visibility.

---

## Phase 1 Summary — What's Done and What's Not

### Done

| Server | Status | Tools | Test Passing |
|--------|--------|-------|-------------|
| market_server.py | ✅ Built & tested | 10 | Yes |
| sec_edgar_server.py | ✅ Built & tested | 3 | Yes |
| news_server.py | ✅ Built & tested | 3 | Yes |
| finance_server.py | ✅ Built & tested | 5 | Yes |
| calculator_server.py | ✅ Built & tested | 13 | Yes |
| tax_server.py | ✅ Built & tested | 5 | Yes |
| portfolio_server.py | ✅ Built & tested | 4 | Yes |

### Not Done Yet (Phase 1 Formal Completion)

- Formal pytest suite (~35 tests across all 7 servers)
- GitHub Actions CI running on every push
- `mcp_servers/README.md` documenting all 7 servers

These are tracked as Phase 1 loose ends to close before Phase 2 begins.

---

## What Comes Next — Phase 2

Phase 2 is the LlamaIndex RAG pipeline. It takes the `document_url` returned by
`sec_edgar_server.get_10k()` and:
1. Downloads the 10-K filing HTML/PDF
2. Chunks it into sections (LlamaIndex's text splitter)
3. Embeds each chunk using Modal's hosted bge-large model
4. Stores embeddings in the `document_embeddings` table (pgvector)
5. Exposes a query engine that agents can call: "What does Apple's latest 10-K
   say about its debt obligations?"

The Data Agent (Phase 3) will use this pipeline to pull validated numbers from
SEC filings without hallucinating them.

---

*End of Phase 0 and Phase 1 notes.*
*Next: Phase 2 — LlamaIndex RAG Pipeline*
