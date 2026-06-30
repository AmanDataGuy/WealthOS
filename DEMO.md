# WealthOS — Quickstart & Demo Guide

> From zero to running analysis in under 5 minutes.

---

## Prerequisites

- Docker + Docker Compose v2
- Python 3.11+ (for local dev without Docker)
- API keys (see `.env.example`)

---

## Option A — Docker Compose (recommended)

```bash
# 1. Clone and configure
git clone https://github.com/your-handle/WealthOS.git
cd WealthOS
cp .env.example .env
# Fill in GROQ_API_KEY (required) and other keys

# 2. Start all services
docker compose up --build -d

# 3. Initialize database schema
docker exec wealthos-api psql $WEALTHOS_DB_URL -f scripts/init_db.sql

# 4. Initialize Qdrant collections
docker exec wealthos-api python scripts/init_qdrant.py

# 5. Open the app
# Streamlit frontend:  http://localhost:8501
# FastAPI Swagger UI:  http://localhost:8000/docs
```

---

## Option B — Local Dev (no Docker)

```bash
# 1. Spin up infrastructure only
docker compose up wealthos-db wealthos-redis wealthos-qdrant -d

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env — set GROQ_API_KEY at minimum

# 4. Initialize
psql $WEALTHOS_DB_URL -f scripts/init_db.sql
python scripts/init_qdrant.py

# 5. Start API
uvicorn api.main:app --reload --port 8000

# 6. Start frontend (separate terminal)
streamlit run wealthos_app.py
```

---

## Required API Keys

| Key | Where to get | Required? |
|---|---|---|
| `GROQ_API_KEY` | console.groq.com | YES — LLM backbone |
| `E2B_API_KEY` | e2b.dev | YES — DCF/Monte Carlo sandbox |
| `MEM0_API_KEY` | app.mem0.ai | YES — cross-session memory |
| `COHERE_API_KEY` | cohere.com | Recommended — RAG reranking |
| `NEWS_API_KEY` | newsapi.org | Recommended — news headlines |
| `LANGCHAIN_API_KEY` | smith.langchain.com | Optional — LangSmith tracing |
| `WANDB_API_KEY` | wandb.ai | Optional — eval tracking |

---

## Running Your First Analysis

### Via Streamlit UI

1. Open `http://localhost:8501`
2. Sign up or log in
3. Navigate to **Analyze** in the sidebar
4. Enter a ticker (e.g. `NVDA`) and a query
5. Click **Analyze** — memo appears in 60-90 seconds

### Via API

```bash
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "ticker": "NVDA",
    "query": "Should I invest in NVDA for long-term growth?",
    "user_id": "00000000-0000-0000-0000-000000000001"
  }'
```

### Via CLI (fastest for testing)

```bash
python -m graph.graph NVDA
python -m graph.graph TSLA 00000000-0000-0000-0000-000000000001
```

---

## Indexing Documents for RAG

### Index a US Stock (SEC 10-K)

```bash
# Fetch and index AAPL 10-K automatically
python -m rag.pipeline --ticker AAPL

# Verify chunk count
python -c "
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue
qc = QdrantClient('http://localhost:6333')
r = qc.count('wealthos_docs', count_filter=Filter(must=[FieldCondition('ticker', match=MatchValue(value='AAPL'))]))
print(f'AAPL chunks: {r.count}')
"
```

### Index a Personal Document

```bash
curl -X POST http://localhost:8000/upload-doc \
  -F "file=@/path/to/salary_slip.pdf" \
  -F "user_id=00000000-0000-0000-0000-000000000001"
```

---

## Evaluation

```bash
# RAG quality (context precision, recall, faithfulness, answer relevancy)
python eval/ragas_eval.py

# Writer output quality (faithfulness, relevancy, hallucination — 28 golden examples)
python eval/run_deepeval.py --limit 5

# W&B Weave LLM-as-judge (4-dimension memo scoring)
python eval/evaluate.py

# Re-compile DSPy writer prompt from golden dataset
python -m eval.dspy_optimizer
```

---

## Observability

- **LangSmith**: `https://smith.langchain.com` — every pipeline run traced with per-node latency and token cost
- **W&B Weave**: `https://wandb.ai` — before/after DSPy prompt optimization scores
- **Local logs**: `logs/query_log.jsonl` — one JSON line per API call
- **Analysis history**: Streamlit sidebar → **Reports** page — full memo for every past run

---

## Architecture

```
Query + Ticker
    |
FastAPI /analyze
    |
LangGraph Pipeline (8 nodes, ~60-90s)
    |-- Finance Node     -> health score, surplus, risk capacity  (Postgres + MCP)
    |-- Data Node        -> live price, P/E, FCF, growth rate     (yfinance + Redis)
    |-- Research Node    -> 10-K RAG context, news sentiment      (Qdrant hybrid + NewsAPI)
    |-- Risk Node        -> macro + stock debate -> risk score 1-10 (LLM debate)
    |-- Code Node        -> DCF intrinsic value, Monte Carlo      (E2B sandbox)
    |-- Rebalancing Node -> portfolio drift, suggested actions    (Pure Python)
    |-- Writer Node      -> 7-section investment memo             (DSPy compiled prompt)
    |
Memo stored in analysis_history (Postgres)
Verdict vector stored in user_analyses (Qdrant)
2-line summary stored in Mem0
```

---

## Troubleshooting

**Pipeline times out**: Groq free tier is 12k TPM. Run analyses 60 seconds apart, or add `GROQ_API_KEY_2` to `.env` for automatic key rotation.

**E2B DCF returns error**: `E2B_API_KEY` missing or expired. The memo still completes — the Valuation Analysis section notes DCF is unavailable.

**Qdrant returns empty context**: Run `python scripts/init_qdrant.py` then `python -m rag.pipeline --ticker AAPL` to index documents.

**Personal docs not appearing in memo**: Upload via the Streamlit Upload page or `/upload-doc` endpoint. Research agent pulls personal docs by `user_id` tag.

**Indian stocks get thin analysis**: Indian stocks have ~8 chunks vs 180-280 for US stocks. The BSE PDF indexer is in the roadmap — see `plan_ahead.md` Phase 3.
