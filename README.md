<div align="center">

# WealthOS

**Personal Financial Intelligence Platform**

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![LangGraph](https://img.shields.io/badge/LangGraph-Orchestrator-1C3A5E?style=flat-square)](https://langchain.com)
[![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-pgvector-336791?style=flat-square&logo=postgresql&logoColor=white)](https://postgresql.org)
[![Redis](https://img.shields.io/badge/Redis-Cache-DC382D?style=flat-square&logo=redis&logoColor=white)](https://redis.io)
[![Streamlit](https://img.shields.io/badge/Streamlit-Frontend-FF4B4B?style=flat-square&logo=streamlit&logoColor=white)](https://streamlit.io)

*7 specialized agents × 7 MCP servers × 41 tools → one personalized investment memo in under 90 seconds.*

</div>

---

## What It Does

A user asks: **"Should I invest ₹20,000 in Reliance right now?"**

WealthOS knows their monthly surplus is ₹18,000, food spending spiked 35% last month, they have an outstanding home loan EMI, and their 80C deduction is unutilized. The output is not generic advice — it is advice for **this person, at this moment in their financial life.**

---

---

## Architecture
```mermaid
flowchart LR
    %% Color Classes for Beautification
    classDef input fill:#dbeafe,stroke:#2563eb,stroke-width:2px,color:#1e3a8a
    classDef interface fill:#d1fae5,stroke:#059669,stroke-width:2px,color:#064e3b
    classDef mcp fill:#fef3c7,stroke:#d97706,stroke-width:2px,color:#78350f
    classDef agent fill:#fce7f3,stroke:#db2777,stroke-width:2px,color:#831843
    classDef orch fill:#f1f5f9,stroke:#f97316,stroke-width:3px,color:#7c2d12
    classDef intel fill:#ede9fe,stroke:#7c3aed,stroke-width:2px,color:#4c1d95
    classDef data fill:#e0f2fe,stroke:#0284c7,stroke-width:2px,color:#0c4a6e
    classDef output fill:#fee2e2,stroke:#dc2626,stroke-width:2px,color:#7f1d1d

    %% Row 1: Input and Interface
    Input[Multi-Modal Input<br>Text / Voice / PDF]:::input --> FastAPI[FastAPI + Streamlit<br>Unified Interface]:::interface

    %% Row 2: MCP Servers (Horizontal)
    FastAPI --> M1[Market Data<br>yfinance]:::mcp
    FastAPI --> M2[SEC Filings<br>10-K / 10-Q]:::mcp
    FastAPI --> M3[News & Sentiment]:::mcp
    FastAPI --> M4[Finance Calculators<br>DCF / WACC]:::mcp
    FastAPI --> M5[Tax & Portfolio]:::mcp

    %% Row 3: 7 Agents (Horizontal)
    M1 & M2 & M3 & M4 & M5 --> A1[Finance Agent<br>Health Score]:::agent
    M1 & M2 & M3 & M4 & M5 --> A2[Research Agent<br>Parallel Fetch]:::agent
    M1 & M2 & M3 & M4 & M5 --> A3[Data Agent<br>asyncpg + Redis]:::agent
    M1 & M2 & M3 & M4 & M5 --> A4[Risk Agent<br>Debate Pattern]:::agent
    M1 & M2 & M3 & M4 & M5 --> A5[Code Agent<br>E2B Sandbox]:::agent
    M1 & M2 & M3 & M4 & M5 --> A6[Rebalancing Agent<br>Allocation Warnings]:::agent
    M1 & M2 & M3 & M4 & M5 --> A7[Writer Agent<br>DSPy + Guardrails]:::agent

    %% Row 4: Orchestrator
    A1 & A2 & A3 & A4 & A5 & A6 & A7 --> Orchestrator[LangGraph Orchestrator<br>8 Nodes · Parallel Execution]:::orch

    %% Row 5: Intelligence Layer (Horizontal)
    Orchestrator --> RAG[RAG Pipeline<br>Voyage AI · Qdrant]:::intel
    Orchestrator --> Mem0[Mem0 Memory<br>User Context]:::intel
    Orchestrator --> Temporal[Temporal<br>Scheduled Workflows]:::intel

    %% Row 6: Data Layer (Horizontal)
    Orchestrator --> DB[(PostgreSQL<br>+ Qdrant Cloud)]:::data
    Orchestrator --> Cache[(Redis Cache)]:::data
    Orchestrator --> Sandbox[E2B Sandbox]:::data
    Orchestrator --> Ollama[Ollama Local<br>qwen2.5:7b]:::data

    %% Row 7: Output
    Orchestrator --> Output[Final Report / Alert]:::output
```

---

---

<div align="center">

## Agents

| Agent | Framework | Key Capability | Output |
|:---:|:---:|:---:|:---:|
| Finance Agent | Pure Python | z-score anomaly detection (σ = 2.0) | Health Score 0–100, surplus, subscriptions |
| Research Agent | asyncio | parallel fetch — market + news + SEC | Sentiment, macro context |
| Data Agent | asyncpg + httpx | schema-validated numbers, Redis 15-min TTL | `FinancialSnapshot` with confidence flag |
| Risk Agent | LangGraph (3-node debate) | Macro, Stock, Scorer agents | Risk score 1–10 + recommendation |
| Code Agent | E2B | sandbox-executed Python | DCF intrinsic value, Monte Carlo distribution |
| Rebalancing Agent | Pure Python | >40% sector concentration warning | Rebalance recommendation |
| Writer Agent | DSPy + LangGraph | compiled few-shot prompt, custom Pydantic validators | Final investment memo |

</div>

---

---

<div align="center">

##  MCP Servers

| Server | Tools | Data Source |
|:---:|:---:|:---:|
| `market_server` | 10 | yfinance — price, P/E, market cap, historical |
| `sec_edgar_server` | 4 | SEC EDGAR — 10‑K / 10‑Q filing URLs + XBRL facts |
| `news_server` | 4 | NewsAPI + Firecrawl — headlines, sentiment, Reddit |
| `finance_server` | 6 | PostgreSQL — transactions, anomalies, subscriptions, EMIs |
| `calculator_server` | 7 | XIRR, SIP, EMI, FIRE, goal savings, compound interest |
| `tax_server` | 4 | 80C / HRA / slabs — old vs new regime (FY 2024-25) |
| `portfolio_server` | 6 | PostgreSQL + yfinance — holdings, P&L, allocation, add/remove |

**Total: 41 tools across 7 servers**

</div>

---

---

<div align="center">

## 🔬 Under the Hood

| Category | Implementation | Why It Matters |
|:---:|:---:|:---|
| **Observability** | LangSmith · W&B Weave (eval scoring) | pipeline traces via LangSmith; eval quality tracking via Weave |
| **Memory** | Mem0 vector memory | Cross‑session recall of past analyses and user context |
| **Durability** | Temporal workflows | Crash‑safe execution with automatic retry & checkpointing |
| **Code Sandbox** | E2B | Secure Python execution for DCF & Monte Carlo models |
| **Retrieval** | Qdrant hybrid search (Voyage AI dense + BM25 sparse) + Cohere reranking | Prevents hallucinated numbers from financial tables |
| **LLM Stack** | Groq `llama-3.3-70b-versatile` (primary) · Ollama `qwen2.5:7b` (fallback) | Groq for speed; local Ollama fallback when offline |
| **Parallelism** | `asyncio.gather` in LangGraph | 2× speedup vs sequential agent execution |
| **Validation** | Custom Pydantic v2 validators | Blocks impossible outputs before they reach the user |
| **Prompt Optimization** | DSPy BootstrapFewShot (15 golden examples) | Compiled prompt measurably outperforms hand‑written baseline |
| **Notifications** | Composio | Gmail + WhatsApp alerts without OAuth boilerplate |
| **Vector Store** | Qdrant Cloud (hybrid dense+sparse) | Separate from Postgres; optimized for similarity search |

</div>

---

---

<div align="center">

## 🧰 Tech Stack

| Layer | Technologies |
|:---:|:---|
| **Orchestration** | LangGraph (8‑node state machine) · Temporal (durable workflows) |
| **Agents** | Pure Python · asyncio |
| **LLM** | Groq `llama-3.3-70b-versatile` (primary) · Ollama `qwen2.5:7b` (fallback) |
| **RAG** | Qdrant Cloud hybrid search · Voyage AI `voyage-finance-2` (1,024‑dim) · Cohere reranking |
| **Memory** | Mem0 (cross‑session vector memory) |
| **Prompt Optimization** | DSPy (BootstrapFewShot) |
| **Validation** | Guardrails AI · Pydantic v2 |
| **Code Execution** | E2B Sandbox |
| **Database** | PostgreSQL 16 + Qdrant Cloud (vector store) |
| **Cache** | Redis (15‑min TTL, pub/sub) |
| **Notifications** | Composio (Gmail + WhatsApp) |
| **Observability** | LangSmith · W&B Weave |
| **Backend** | FastAPI |
| **Frontend** | Streamlit |

</div>

---

---

<div align="center">

## 🚧 Roadmap

| Feature | Status |
|:---:|:---:|
| Docker Compose · AWS EC2 deployment | 🔄 In Progress |
| Multi‑user authentication & isolated portfolios | 🔄 Planned |
| Real‑time price alerts via Kafka | 🔄 Planned |
| Fine‑tuned Writer Agent (LoRA on Qwen) | 🔄 Planned |

</div>

---

---

<div align="center">
  
## 🚀 Quick Start

```bash
git clone https://github.com/AmanDataGuy/WealthOS
cd WealthOS
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# Start services (WSL)
sudo service postgresql start
redis-server --daemonize yes
ollama serve &
ollama pull qwen2.5:7b
ollama pull mxbai-embed-large

# Run
uvicorn api.main:app --reload --port 8000
streamlit run wealthos_app.py --server.port 8501
