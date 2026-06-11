# WealthOS — Setup Guide

Zero to one working query. Every command is exact and copy-pasteable.

---

## What You Need Before Starting

- Python 3.12
- Docker Desktop running
- Three API keys: **Groq**, **Voyage AI**, **Cohere**

Everything else in this guide is free / self-hosted.

---

## What "works" at each level

| Level | What you get | What's missing |
|---|---|---|
| **Minimum** (Groq only) | Pipeline completes, memo is generated | No personal finance data, no RAG context, no DCF |
| **Recommended** (Groq + Voyage + Cohere + Postgres + Qdrant) | Full memo with market data and RAG qualitative context | No DCF/Monte Carlo (needs E2B), no news summaries (needs NewsAPI + Ollama) |
| **Full** (all keys + all services) | Everything | — |

This guide targets the **Recommended** level — that's what the three keys you have enable.

---

## Part 1 — Python Environment

```bash
# From the project root (d:\projects\WealthOS)
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate    # Linux / Mac

pip install -r requirements.txt
```

Expected output: lots of package installs, no red errors.  
If you see `ERROR: Could not find a version that satisfies the requirement`, check that you are running Python 3.12:

```bash
python --version   # must be 3.12.x
```

---

## Part 2 — Environment Variables

Edit `.env` in the project root. The file already exists. Set these three values:

```
VOYAGE_API_KEY=your_voyage_key_here
COHERE_API_KEY=your_cohere_key_here
```

`GROQ_API_KEY` is already filled in. Leave everything else as-is — the defaults point to the local Docker services you will start in Part 3.

**Full dependency table (what each env var does):**

| Env Var | What It Does | Blocking? | Where to Get |
|---|---|---|---|
| `GROQ_API_KEY` | LLM calls (risk agent, writer agent, data agent) | **YES — app produces no output without it** | console.groq.com — free |
| `VOYAGE_API_KEY` | Embeds queries into 1024-dim vectors for RAG | Degrades (zero-vector fallback, no meaningful RAG) | dash.voyageai.com — free tier |
| `COHERE_API_KEY` | Reranks RAG candidates | Degrades (reranking skipped) | dashboard.cohere.com — free tier |
| `WEALTHOS_DB_URL` | PostgreSQL connection string | Degrades (data/finance agents return empty data) | Default points to Docker container below |
| `QDRANT_URL` | Qdrant vector DB | Degrades (RAG returns empty) | Default points to Docker container below |
| `REDIS_URL` | Cache (15-min TTL) | Optional — fails silently | Default points to Docker container below |
| `E2B_API_KEY` | Sandboxed Python for DCF + Monte Carlo | Optional — code node fails, error node catches | e2b.dev — free 500 credits/month |
| `NEWSAPI_KEY` | Financial news headlines | Optional — news skipped | newsapi.org — free 100 req/day |
| `LANGCHAIN_API_KEY` | LangSmith pipeline traces | Optional — @trace_node becomes no-op | smith.langchain.com — free |
| `WANDB_API_KEY` | W&B Weave eval tracking | Optional — skipped silently | wandb.ai — free |
| `MEM0_API_KEY` | Cross-session user memory | Optional — memory skipped silently | mem0.ai — free 500 memories |
| `FIRECRAWL_API_KEY` | Reddit sentiment scraping | Optional — Reddit step skipped | firecrawl.dev — free tier |
| `OLLAMA_URL` | Local LLM fallback + research agent summarisation | Optional — Groq is primary | localhost:11434 (local install) |
| `SEC_USER_AGENT` | Header for SEC EDGAR free API | Optional — already set in .env | No sign-up needed |

---

## Part 3 — Start Local Services

Run all three. Order doesn't matter.

### PostgreSQL

```bash
docker run -d \
  --name wealthos-postgres \
  -e POSTGRES_USER=wealthos \
  -e POSTGRES_PASSWORD=wealthos_secure_password \
  -e POSTGRES_DB=wealthos \
  -p 5432:5432 \
  -v wealthos-pgdata:/var/lib/postgresql/data \
  postgres:16
```

Verify it started:
```bash
docker logs wealthos-postgres 2>&1 | tail -5
# Expected last line: "database system is ready to accept connections"
```

### Redis

```bash
docker run -d \
  --name wealthos-redis \
  -p 6379:6379 \
  redis:7-alpine
```

### Qdrant

```bash
docker run -d \
  --name wealthos-qdrant \
  -p 6333:6333 \
  -p 6334:6334 \
  -v wealthos-qdrant-storage:/qdrant/storage \
  qdrant/qdrant:latest
```

Verify Qdrant is up:
```bash
curl http://localhost:6333/healthz
# Expected: {"title":"qdrant - vector search engine","version":"..."}
```

**On subsequent runs**, start existing containers instead of re-creating them:
```bash
docker start wealthos-postgres wealthos-redis wealthos-qdrant
```

---

## Part 4 — Initialize the Database

Run once. Safe to re-run (all statements are IF NOT EXISTS).

```bash
docker exec -i wealthos-postgres psql -U wealthos -d wealthos < scripts/init_db.sql
```

Expected output (one line per object created):
```
CREATE TABLE
CREATE INDEX
CREATE TABLE
...
INSERT 0 1
INSERT 0 1
```

The last two INSERTs seed one test user (`user_id = 00000000-0000-0000-0000-000000000001`) with a TCS.NS holding and a tracked symbol, so your first test query has something to work with.

Verify:
```bash
docker exec -it wealthos-postgres psql -U wealthos -d wealthos -c "\dt"
```
Expected: a table listing showing `transactions`, `subscriptions`, `financial_goals`, `emis`, `financial_facts`, `portfolio_holdings`, `tracked_symbols`.

---

## Part 5 — Initialize the Qdrant Collection

Run once. Safe to re-run (skips if collection exists).

```bash
python scripts/init_qdrant.py
```

Expected output:
```
Connecting to Qdrant at http://localhost:6333 ...
Creating collection 'wealthos_docs' ...
  Creating payload indexes ...

Done. Collection 'wealthos_docs' created.
```

**Note:** This creates the empty collection. The collection will have 0 documents until you run the RAG indexer (`rag/indexer.py`) against actual SEC filings or annual reports. The pipeline still works without indexed documents — RAG just returns empty context and the memo relies on live market data only.

---

## Part 6 — Start FastAPI

```bash
uvicorn api.main:app --reload --port 8000
```

Successful startup looks like:
```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Started reloader process
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Application startup complete.
```

Verify the API is up:
```bash
curl http://localhost:8000/health
# Expected: {"status":"ok"}
```

Check agent cards are registered:
```bash
curl http://localhost:8000/agents
# Expected: {"agents":[{"name":"finance_agent",...},{"name":"risk_agent",...}, ...]}
```

---

## Part 7 — Start Streamlit

Open a second terminal (keep FastAPI running in the first):

```bash
streamlit run wealthos_app.py --server.port 8501
```

Expected output:
```
  You can now view your Streamlit app in your browser.
  Local URL: http://localhost:8501
```

Open `http://localhost:8501` in your browser.

---

## Part 8 — Run One Test Query

### Via curl (no browser needed)

```bash
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Should I invest 10000 rupees in TCS.NS right now?",
    "tickers": ["TCS.NS"],
    "user_id": "00000000-0000-0000-0000-000000000001"
  }'
```

### Via Streamlit

Type the same query in the chat box. The user_id defaults to `00000000-0000-0000-0000-000000000001`.

---

## What Successful Output Looks Like

The `/analyze` endpoint returns a JSON object. A successful run has:

```json
{
  "status": "success",
  "memo": "## TCS.NS Investment Analysis\n\n**Recommendation: HOLD**\n\n...",
  "risk_score": 4,
  "recommendation": "hold",
  "messages": [
    "[finance_agent] ✅ PersonalFinanceSnapshot built — confidence: low",
    "[data_agent] ✅ FinancialSnapshot built for TCS.NS",
    "[risk_agent] ✅ Risk score: 4/10",
    "[code_agent] ❌ E2B key not set — skipping DCF",
    "[writer_agent] ✅ Memo generated"
  ]
}
```

The `memo` field is a multi-paragraph markdown string. The FastAPI console will print per-agent logs showing which nodes executed.

**If `code_agent` shows ❌:** that is expected without `E2B_API_KEY`. The pipeline completes; the memo just lacks the DCF section.

**If `risk_agent` or `writer_agent` shows ❌:** check that `GROQ_API_KEY` is set correctly in `.env` and that uvicorn was started after editing `.env`.

---

## Query Log

After every `/analyze` call, a structured log line is appended to `logs/query_log.jsonl`:

```bash
cat logs/query_log.jsonl
```

Fields: `query`, `tickers`, `user_id`, `latency_ms`, `total_tokens`, `estimated_cost_usd`, `agents_invoked`, `success`, `error`.

A typical run costs ~$0.001–$0.003 in Groq tokens.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ModuleNotFoundError` on startup | venv not activated or deps not installed | `venv\Scripts\activate` then `pip install -r requirements.txt` |
| `ConnectionRefusedError` on first request | PostgreSQL not running | `docker start wealthos-postgres` |
| `memo` is very short with no numbers | `VOYAGE_API_KEY` or `COHERE_API_KEY` not set | Edit `.env`, restart uvicorn |
| `writer_agent ❌` | `GROQ_API_KEY` wrong or expired | Check key at console.groq.com |
| `qdrant_client` timeout | Qdrant container not running | `docker start wealthos-qdrant` |
| Port 8000 already in use | Another uvicorn instance | `uvicorn api.main:app --port 8001` and update `NEXT_PUBLIC_API_URL` in `.env` |
