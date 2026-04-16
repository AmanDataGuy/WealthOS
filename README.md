# WealthOS 💰
### Personal Financial Intelligence Platform

> *"Should I invest ₹20,000 in Reliance right now?"*
> WealthOS knows your monthly surplus is ₹18,000, your food spending spiked 35% last month, you have an outstanding EMI of ₹12,000, and your portfolio is already 18% overweight in energy — and gives you an answer built for **your** financial life, not a generic one.

[![Build Status](https://img.shields.io/github/actions/workflow/status/yourusername/wealthOS/ci.yml?branch=main&style=flat-square)](https://github.com/yourusername/wealthOS/actions)
[![Live Demo](https://img.shields.io/badge/demo-live-brightgreen?style=flat-square)](https://wealthos.yourdomain.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue?style=flat-square)](https://www.python.org/)

---

## What It Does

WealthOS is a **7-agent personal financial intelligence platform** that does two things no existing tool combines: it analyzes stocks like a Wall Street research desk, and it analyzes *your* personal finances like a CFO — then cross-references both.

The result is not generic advice. It's a memo that knows your surplus, your EMIs, your goals, your existing portfolio allocation — and tells you specifically whether *you* should buy this stock, and what to do with the rest of your portfolio when you do.

---

## Demo

> *Screenshots / GIF coming soon — first working demo after Phase 3*

---

## Input — Four Ways to Talk to WealthOS

| Mode | How | Handled By | Best For |
|---|---|---|---|
| **Text** | Type in chat | CopilotKit | Questions, manual expense entry |
| **Voice** | Tap microphone → speak | OpenAI Whisper STT | Hands-free queries |
| **Image** | Upload JPEG/PNG | Claude 3.5 Sonnet (vision) | Receipts, salary slips, expense screenshots |
| **PDF** | Upload PDF | Claude 3.5 Sonnet (vision, page-by-page) | Bank statements, payslips, tax documents |

All four input types normalize into the same pipeline. Uploading a 3-month bank statement PDF gives you the same depth of personal finance analysis as entering your expenses manually.

---

## Architecture

```
[Text] [Voice → Whisper] [Image → Claude Vision] [PDF → Claude Vision]
                         │
                   Input Router
                         │
                         ▼
          LiteLLM Gateway (Groq / Bedrock / DeepSeek R1 / Ollama)
                         │
                         ▼
        LangGraph Orchestrator + Temporal (durable workflows)
                         │
       ┌─────────────────┼──────────────────────────────┐
       ▼                 ▼                              ▼
 Finance Agent     Research Agent                  Data Agent
 - OCR inputs       - Browser-Use                  - yfinance MCP
 - Anomaly detect     live web nav                 - SEC EDGAR MCP
 - Health Score     - Firecrawl news               - LlamaIndex + Qdrant
 - Tax/goals/debt   - Sentiment                    - PydanticAI validated
       │                 │                              │
       └─────────────────┴──────────────────────────────┘
                         │
              ┌──────────┴───────────┐
              ▼                      ▼
        Risk Agent             Code Agent
        CrewAI +               Smolagents +
        DeepSeek R1            E2B sandbox
        (personal              DCF + Monte Carlo
        risk score)            Python execution
              │                      │
              └──────────┬───────────┘
                         │
               Rebalancing Agent
               - Current holdings
               - Drift from target
               - Buy/sell suggestions
                         │
              [HITL Checkpoint — AG-UI]
                         │
               Writer Agent
               DSPy-optimized prompts
               Streaming personalized memo
                         │
            ┌────────────┼─────────────┐
            ▼            ▼             ▼
         PDF Export   Kafka        WhatsApp /
         → Drive      price        Gmail
                      alerts       alerts
```

---

## 7 Agents

| # | Agent | Framework | What It Does |
|---|---|---|---|
| 1 | **Finance Agent** | Agno | Builds your complete financial picture — spending anomalies, EMIs, goals, tax gaps, insurance. Computes Financial Health Score. Runs first, always. |
| 2 | **Research Agent** | Agno + Browser-Use | Navigates live NSE/Moneycontrol/analyst pages with a real browser. Extracts analyst recommendations, news sentiment, macro trends. |
| 3 | **Data Agent** | PydanticAI + AWS Bedrock | Fetches validated financial numbers via yfinance + SEC EDGAR. Queries 10-K/10-Q filings semantically via LlamaIndex + Qdrant. Zero hallucinated numbers. |
| 4 | **Risk Agent** | CrewAI + DeepSeek R1 | Scores investment risk 1-10 — adjusted for your actual EMI burden, surplus, and goal timeline. Uses chain-of-thought reasoning. |
| 5 | **Code Agent** | Smolagents + E2B | Writes and executes real Python: 5-year DCF model, 10,000-path Monte Carlo simulation, sensitivity table. Verified numbers from a real sandbox. |
| 6 | **Rebalancing Agent** | Agno | Checks your current portfolio, computes sector drift from your target allocation, and tells you exactly what to buy/sell when you make the new investment. |
| 7 | **Writer Agent** | LangGraph + DSPy | Synthesizes all 6 agents into a personalized investment memo. Prompts are DSPy-compiled against a golden dataset, not hand-written. |

---

## Key Features

### Financial Health Score
A single **0–100 score** summarizing your financial health across 5 dimensions: surplus ratio, debt burden, goal progress, tax utilization, and insurance coverage. Shown on the dashboard. Embedded in every investment memo.

| Score | Grade |
|---|---|
| 80–100 | Excellent |
| 65–79 | Good |
| 50–64 | Fair |
| < 50 | Needs Attention |

---

### Portfolio Rebalancing
Before recommending any investment, the Rebalancing Agent looks at your *actual holdings* and shows you:
- Your current sector allocation vs your target
- How the new investment changes that allocation
- Specific buy/sell actions with rupee amounts to stay on target

> *"Buying Reliance at ₹20,000 increases your energy sector weight from 18% to 26% vs your 20% target. Consider trimming ₹12,000 from ONGC to rebalance."*

---

### Morning Briefing (Proactive Agent)
Every morning at **8:00 AM**, without you asking, WealthOS sends a 5-line briefing to WhatsApp or Gmail:

```
WealthOS Morning Briefing

📉 Reliance: -4.2% overnight (Q3 miss, analyst downgrade)
💰 Your food spend is 28% above average this month
🔔 TCS is within 1.8% of your ₹3,800 price alert
🌐 RBI rate decision today at 2 PM — affects your HDFC holding
💡 ₹42,000 in unutilized 80C with 3 months left in FY
```

Powered by Temporal cron + Kafka + Composio.

---

### PDF Export
After every analysis, export the full investment memo as a formatted PDF. Automatically pushed to your Google Drive via Composio.

---

### Price Alerts
Set a price target during HITL review → Kafka watches for it → WhatsApp/Gmail notification when it triggers.

---

### Multi-Stock Comparison
Ask: *"Compare Reliance vs TCS vs Infosys"* → 3 parallel pipelines → side-by-side memo ranked by fit to *your* portfolio.

---

### Privacy Mode
Toggle on → all personal bank data and spending analysis routes through local **Ollama** inference. Nothing sensitive leaves your environment.

---

## Personal Finance Features (Finance Agent)

- Spending anomaly detection — z-score vs 3-month rolling average, flags > 1.5σ
- Goal tracker — back-calculates monthly savings needed per goal
- Debt optimizer — avalanche vs snowball for active EMIs
- Tax optimizer — unutilized 80C, 80D, HRA deductions
- Subscription auditor — recurring charges with no recent usage
- Insurance gap analyzer — coverage vs recommended multiple of income
- Peer benchmarking — your spending vs anonymized income-bracket cohort
- Receipt OCR — photograph a receipt → auto-categorized transaction
- Bank statement parser — upload PDF → full transaction history extracted

---

## Tech Stack

### Core Infrastructure
| Layer | Technology |
|---|---|
| Orchestration | LangGraph v0.2 + Temporal |
| LLM Gateway | LiteLLM (Groq / AWS Bedrock / DeepSeek R1 / o3-mini / Ollama) |
| Agent Protocol | Google A2A |
| Tool Protocol | MCP (11 custom servers) + Composio |
| Frontend | CopilotKit + AG-UI |
| Backend | FastAPI + JWT |
| Database | PostgreSQL + pgvector |

### AI & Agents
| Component | Technology |
|---|---|
| Finance + Rebalancing Agent | Agno |
| Data Agent | PydanticAI + AWS Bedrock |
| Risk Agent | CrewAI + DeepSeek R1 |
| Code Agent | Smolagents + E2B sandbox |
| Vision / OCR (image + PDF) | Claude 3.5 Sonnet (multimodal) |
| Voice Input | OpenAI Whisper |
| Document RAG | LlamaIndex + Qdrant |
| Web Navigation | Browser-Use |
| Web Scraping | Firecrawl |
| Prompt Optimization | DSPy |
| Output Validation | Guardrails AI + PydanticAI |
| Long-term Memory | Mem0 |
| Reasoning | DeepSeek R1 / o3-mini |
| Local Inference | Ollama |

### Infrastructure
| Component | Technology |
|---|---|
| Vector DB | Qdrant |
| Caching | Redis |
| Event Streaming | Kafka |
| Self-hosted Embeddings | Modal (bge-large on A100) |
| Fine-tuning | Modal H100 (QLoRA) |
| Observability | LangSmith + AgentOps + W&B Weave |
| Cloud | AWS Bedrock + S3 + App Runner + CloudWatch |
| Deployment | Docker + GitHub Actions → ECR → App Runner |
| Notifications | Composio (Gmail + WhatsApp + Google Drive) |

---

## 11 Custom MCP Servers

| Server | Tools |
|---|---|
| `yfinance-mcp` | get_price, get_financials, get_history, get_info |
| `sec-edgar-mcp` | get_10k, get_10q, get_filings_list |
| `news-mcp` | search_news, get_sentiment, get_headlines |
| `finance-mcp` | parse_transactions, detect_anomalies, calc_surplus, audit_subscriptions |
| `calculator-mcp` | calc_cagr, calc_wacc, calc_dcf_inputs, calc_intrinsic_value |
| `tax-mcp` | calc_80c_remaining, calc_hra, estimate_tax, get_optimal_regime |
| `portfolio-mcp` | get_holdings, get_correlation, get_exposure, get_sector_weight |
| `macro-mcp` | get_interest_rates, get_inflation, get_sector_performance |
| `alert-mcp` | set_price_alert, cancel_alert, list_alerts |
| `competitor-mcp` | get_peers, compare_metrics, get_market_share |
| `qdrant-mcp` | embed_and_store, similarity_search, find_related |

---

## Setup

### Prerequisites
- Docker + Docker Compose
- Python 3.11+
- Node.js 18+

### Quick Start

```bash
git clone https://github.com/yourusername/wealthOS
cd wealthOS
cp .env.example .env
# Fill in your API keys
docker-compose up --build
```

Visit `http://localhost:3000`

### Environment Variables

```env
# LLM Providers
OPENAI_API_KEY=
GROQ_API_KEY=
ANTHROPIC_API_KEY=
DEEPSEEK_API_KEY=

# Observability
LANGCHAIN_API_KEY=
AGENTOPS_API_KEY=
WANDB_API_KEY=

# Tools
MEM0_API_KEY=
FIRECRAWL_API_KEY=
COMPOSIO_API_KEY=
E2B_API_KEY=

# Infrastructure
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/wealthOS
QDRANT_URL=http://localhost:6333
REDIS_URL=redis://localhost:6379
KAFKA_BOOTSTRAP_SERVERS=localhost:9092

# AWS
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION=us-east-1

# Auth + Modal + Other
JWT_SECRET_KEY=
MODAL_TOKEN_ID=
MODAL_TOKEN_SECRET=
NEWSAPI_KEY=
FRED_API_KEY=
TEMPORAL_HOST=localhost:7233
```

### Services

| Service | Port | Purpose |
|---|---|---|
| `wealthOS-api` | 8000 | FastAPI + LangGraph backend |
| `wealthOS-frontend` | 3000 | Next.js + CopilotKit |
| `wealthOS-mcp` | 8001 | All 11 MCP servers |
| `wealthOS-db` | 5432 | PostgreSQL 16 + pgvector |
| `wealthOS-qdrant` | 6333 | Qdrant vector database |
| `wealthOS-redis` | 6379 | Redis 7 |
| `wealthOS-kafka` | 9092 | Kafka + Zookeeper |
| `wealthOS-ollama` | 11434 | Ollama (privacy mode) |
| `wealthOS-temporal` | 7233 | Temporal workflow engine |

---

## API

```
POST /analyze              — main analysis (streaming)
POST /analyze/compare      — multi-stock comparison
POST /approve              — HITL approval
POST /transcribe           — Whisper STT
POST /export/pdf           — export memo as PDF

POST /finance/snapshot     — upload bank statement / receipt (image or PDF)
POST /finance/goals        — set financial goals
GET  /finance/{user_id}    — PersonalFinanceSnapshot
GET  /finance/health/{user_id} — Financial Health Score

GET  /memory/{user_id}     — memory history
GET  /alerts/{user_id}     — active price alerts
POST /alerts               — set alert
DELETE /alerts/{id}        — cancel alert

POST /briefing/enable      — enable morning briefing
PUT  /briefing/time        — set briefing time

GET  /portfolio/{user_id}  — holdings + rebalance suggestion
POST /portfolio/holdings   — update holdings

GET  /health               — service health
```

---

## Evaluation

> *Results updated as each phase completes*

| Metric | Target | Result |
|---|---|---|
| Data Agent accuracy (price, P/E, EPS) | 99%+ | — |
| Finance anomaly detection F1 | > 0.85 | — |
| DCF vs analyst consensus | within 15% | — |
| Rebalancing drift calculation | 100% accurate | — |
| Health Score computation | 100% accurate | — |
| Research quality (LLM-as-Judge, 1-5) | > 4.0 | — |
| Writer quality — baseline | — | — |
| Writer quality — DSPy compiled | +0.3–0.5 over baseline | — |
| Writer quality — fine-tuned | best | — |
| p95 latency (10 concurrent users) | < 90s | — |
| Error rate under load | 0% 5xx | — |
| Kafka alert lag | < 5s | — |
| Morning briefing delivery | < 30s after 8 AM | — |

---

## Build Phases

- [ ] **Phase 0** — Foundation: Docker stack, LiteLLM gateway, Input Router (all 4 input types)
- [ ] **Phase 1** — 11 MCP servers with pytest coverage
- [ ] **Phase 2** — LlamaIndex + Qdrant RAG pipeline over 10-K/10-Q filings
- [ ] **Phase 3** — All 7 agents built and tested standalone ← *apply to jobs here*
- [ ] **Phase 4** — LangGraph orchestrator + Temporal + Morning Briefing + Multi-stock mode
- [ ] **Phase 5** — DSPy prompt optimization
- [ ] **Phase 6** — Mem0 + Redis + Kafka + WhatsApp/Gmail alerts + PDF export
- [ ] **Phase 7** — Observability (LangSmith + AgentOps + W&B Weave)
- [ ] **Phase 8** — Frontend (CopilotKit, 4 input modes, Finance dashboard, Rebalancing panel)
- [ ] **Phase 9** — FastAPI + Docker + CI/CD → AWS App Runner (live URL)
- [ ] **Phase 10** — Evaluation suite (all metrics in table above)
- [ ] **Phase 11** — Modal fine-tuning + inference endpoint

---

## Project Structure

```
wealthOS/
├── agents/
│   ├── finance_agent.py
│   ├── research_agent.py
│   ├── data_agent.py
│   ├── risk_agent.py
│   ├── code_agent.py
│   ├── rebalancing_agent.py
│   └── writer_agent.py
├── mcp_servers/          # 11 MCP servers
├── graph/
│   ├── state.py
│   └── nodes.py
├── rag/
│   ├── indexer.py
│   └── query_engine.py
├── input/
│   ├── router.py         # InputRouter — normalizes all 4 input types
│   ├── whisper_handler.py
│   ├── vision_handler.py
│   └── pdf_processor.py
├── memory/
│   └── mem0_client.py
├── gateway/
│   └── litellm_config.yaml
├── workflows/
│   ├── temporal_workflows.py
│   └── morning_briefing.py
├── api/
│   ├── main.py
│   └── export.py         # PDF export
├── health/
│   └── score.py          # HealthScore computation
├── frontend/
├── eval/
│   ├── writer_golden_dataset.json
│   └── compiled_writer.json
├── modal_apps/
│   ├── embedder.py
│   ├── finetune_writer.py
│   └── writer_inference.py
└── .github/workflows/
    └── ci.yml
```

---

## License

MIT
