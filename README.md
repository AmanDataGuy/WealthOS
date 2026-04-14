<div align="center">

# WealthOS v2.0

**Personal Financial Intelligence Platform**

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![LangGraph](https://img.shields.io/badge/LangGraph-Orchestrator-1C3A5E?style=flat-square)](https://langchain.com)
[![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-pgvector-336791?style=flat-square&logo=postgresql&logoColor=white)](https://postgresql.org)
[![Redis](https://img.shields.io/badge/Redis-Cache-DC382D?style=flat-square&logo=redis&logoColor=white)](https://redis.io)
[![Streamlit](https://img.shields.io/badge/Streamlit-Frontend-FF4B4B?style=flat-square&logo=streamlit&logoColor=white)](https://streamlit.io)

*7 specialized agents × 7 MCP servers × 43 tools → one personalized investment memo in under 90 seconds.*

</div>

---

## What It Does

A user asks: **"Should I invest ₹20,000 in Reliance right now?"**

WealthOS knows their monthly surplus is ₹18,000, food spending spiked 35% last month, they have an outstanding home loan EMI, and their 80C deduction is unutilized. The output is not generic advice — it is advice for **this person, at this moment in their financial life.**

---

## Architecture

```svg
<svg viewBox="0 0 860 560" xmlns="http://www.w3.org/2000/svg" font-family="'Segoe UI', system-ui, sans-serif">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#0f172a"/>
      <stop offset="100%" style="stop-color:#1e293b"/>
    </linearGradient>
    <linearGradient id="inputGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" style="stop-color:#6366f1"/>
      <stop offset="100%" style="stop-color:#8b5cf6"/>
    </linearGradient>
    <linearGradient id="mcpGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" style="stop-color:#0ea5e9"/>
      <stop offset="100%" style="stop-color:#06b6d4"/>
    </linearGradient>
    <linearGradient id="agentGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" style="stop-color:#10b981"/>
      <stop offset="100%" style="stop-color:#34d399"/>
    </linearGradient>
    <linearGradient id="orchGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" style="stop-color:#f59e0b"/>
      <stop offset="100%" style="stop-color:#fbbf24"/>
    </linearGradient>
    <linearGradient id="outGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" style="stop-color:#ef4444"/>
      <stop offset="100%" style="stop-color:#f97316"/>
    </linearGradient>
    <filter id="glow">
      <feGaussianBlur stdDeviation="2" result="coloredBlur"/>
      <feMerge><feMergeNode in="coloredBlur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>

  <!-- Background -->
  <rect width="860" height="560" fill="url(#bg)" rx="12"/>

  <!-- Title -->
  <text x="430" y="32" text-anchor="middle" fill="#f8fafc" font-size="15" font-weight="700" letter-spacing="2">WEALTHOS v2.0 — SYSTEM ARCHITECTURE</text>

  <!-- ── ROW 1: INPUT MODES ── -->
  <text x="430" y="62" text-anchor="middle" fill="#94a3b8" font-size="10" letter-spacing="1">INPUT</text>

  <!-- 4 input boxes -->
  <rect x="60" y="70" width="95" height="28" rx="6" fill="url(#inputGrad)" opacity="0.85"/>
  <text x="107" y="88" text-anchor="middle" fill="white" font-size="10" font-weight="600">📝  Text</text>

  <rect x="175" y="70" width="95" height="28" rx="6" fill="url(#inputGrad)" opacity="0.85"/>
  <text x="222" y="88" text-anchor="middle" fill="white" font-size="10" font-weight="600">🎙  Voice</text>

  <rect x="290" y="70" width="95" height="28" rx="6" fill="url(#inputGrad)" opacity="0.85"/>
  <text x="337" y="88" text-anchor="middle" fill="white" font-size="10" font-weight="600">🖼  Image</text>

  <rect x="405" y="70" width="95" height="28" rx="6" fill="url(#inputGrad)" opacity="0.85"/>
  <text x="452" y="88" text-anchor="middle" fill="white" font-size="10" font-weight="600">📄  PDF</text>

  <!-- FastAPI box -->
  <rect x="560" y="70" width="240" height="28" rx="6" fill="#1e3a5f" stroke="#0ea5e9" stroke-width="1"/>
  <text x="680" y="88" text-anchor="middle" fill="#7dd3fc" font-size="10" font-weight="600">FastAPI  ·  Streamlit</text>

  <!-- Arrows down -->
  <line x1="430" y1="98" x2="430" y2="118" stroke="#475569" stroke-width="1.2" stroke-dasharray="3,2"/>

  <!-- ── ROW 2: MCP SERVERS ── -->
  <text x="430" y="130" text-anchor="middle" fill="#94a3b8" font-size="10" letter-spacing="1">7 MCP SERVERS · 43 TOOLS</text>

  <!-- MCP server pills -->
  <rect x="22" y="138" width="112" height="30" rx="6" fill="url(#mcpGrad)" opacity="0.8" filter="url(#glow)"/>
  <text x="78" y="158" text-anchor="middle" fill="white" font-size="9.5" font-weight="700">market_server</text>
  <text x="78" y="168" text-anchor="middle" fill="#e0f2fe" font-size="7.5">10 tools · yfinance</text>

  <rect x="148" y="138" width="112" height="30" rx="6" fill="url(#mcpGrad)" opacity="0.8"/>
  <text x="204" y="158" text-anchor="middle" fill="white" font-size="9.5" font-weight="700">sec_edgar_server</text>
  <text x="204" y="168" text-anchor="middle" fill="#e0f2fe" font-size="7.5">3 tools · 10-K / 10-Q</text>

  <rect x="274" y="138" width="112" height="30" rx="6" fill="url(#mcpGrad)" opacity="0.8"/>
  <text x="330" y="158" text-anchor="middle" fill="white" font-size="9.5" font-weight="700">news_server</text>
  <text x="330" y="168" text-anchor="middle" fill="#e0f2fe" font-size="7.5">3 tools · sentiment</text>

  <rect x="400" y="138" width="112" height="30" rx="6" fill="url(#mcpGrad)" opacity="0.8"/>
  <text x="456" y="158" text-anchor="middle" fill="white" font-size="9.5" font-weight="700">finance_server</text>
  <text x="456" y="168" text-anchor="middle" fill="#e0f2fe" font-size="7.5">5 tools · transactions</text>

  <rect x="526" y="138" width="112" height="30" rx="6" fill="url(#mcpGrad)" opacity="0.8"/>
  <text x="582" y="158" text-anchor="middle" fill="white" font-size="9.5" font-weight="700">calculator_server</text>
  <text x="582" y="168" text-anchor="middle" fill="#e0f2fe" font-size="7.5">13 tools · DCF·WACC</text>

  <rect x="652" y="138" width="95" height="30" rx="6" fill="url(#mcpGrad)" opacity="0.8"/>
  <text x="699" y="158" text-anchor="middle" fill="white" font-size="9.5" font-weight="700">tax_server</text>
  <text x="699" y="168" text-anchor="middle" fill="#e0f2fe" font-size="7.5">5 tools · 80C·HRA</text>

  <rect x="761" y="138" width="78" height="30" rx="6" fill="url(#mcpGrad)" opacity="0.8"/>
  <text x="800" y="158" text-anchor="middle" fill="white" font-size="9.5" font-weight="700">portfolio</text>
  <text x="800" y="168" text-anchor="middle" fill="#e0f2fe" font-size="7.5">4 tools · P&amp;L</text>

  <!-- Arrow down -->
  <line x1="430" y1="170" x2="430" y2="190" stroke="#475569" stroke-width="1.2" stroke-dasharray="3,2"/>

  <!-- ── ROW 3: 7 AGENTS ── -->
  <text x="430" y="202" text-anchor="middle" fill="#94a3b8" font-size="10" letter-spacing="1">7 SPECIALIZED AGENTS</text>

  <rect x="22" y="210" width="109" height="38" rx="6" fill="url(#agentGrad)" opacity="0.85"/>
  <text x="76" y="226" text-anchor="middle" fill="white" font-size="9" font-weight="700">Finance Agent</text>
  <text x="76" y="240" text-anchor="middle" fill="#d1fae5" font-size="7.5">Pure Python · z-score</text>
  <text x="76" y="250" text-anchor="middle" fill="#d1fae5" font-size="7.5">Health Score 0-100</text>

  <rect x="143" y="210" width="109" height="38" rx="6" fill="url(#agentGrad)" opacity="0.85"/>
  <text x="197" y="226" text-anchor="middle" fill="white" font-size="9" font-weight="700">Research Agent</text>
  <text x="197" y="240" text-anchor="middle" fill="#d1fae5" font-size="7.5">asyncio.gather</text>
  <text x="197" y="250" text-anchor="middle" fill="#d1fae5" font-size="7.5">parallel fetch</text>

  <rect x="264" y="210" width="109" height="38" rx="6" fill="url(#agentGrad)" opacity="0.85"/>
  <text x="318" y="226" text-anchor="middle" fill="white" font-size="9" font-weight="700">Data Agent</text>
  <text x="318" y="240" text-anchor="middle" fill="#d1fae5" font-size="7.5">PydanticAI · Redis</text>
  <text x="318" y="250" text-anchor="middle" fill="#d1fae5" font-size="7.5">15-min TTL cache</text>

  <rect x="385" y="210" width="109" height="38" rx="6" fill="url(#agentGrad)" opacity="0.85"/>
  <text x="439" y="226" text-anchor="middle" fill="white" font-size="9" font-weight="700">Risk Agent</text>
  <text x="439" y="240" text-anchor="middle" fill="#d1fae5" font-size="7.5">CrewAI 3-node</text>
  <text x="439" y="250" text-anchor="middle" fill="#d1fae5" font-size="7.5">debate pattern</text>

  <rect x="506" y="210" width="109" height="38" rx="6" fill="url(#agentGrad)" opacity="0.85"/>
  <text x="560" y="226" text-anchor="middle" fill="white" font-size="9" font-weight="700">Code Agent</text>
  <text x="560" y="240" text-anchor="middle" fill="#d1fae5" font-size="7.5">Smolagents · E2B</text>
  <text x="560" y="250" text-anchor="middle" fill="#d1fae5" font-size="7.5">DCF · Monte Carlo</text>

  <rect x="627" y="210" width="109" height="38" rx="6" fill="url(#agentGrad)" opacity="0.85"/>
  <text x="681" y="226" text-anchor="middle" fill="white" font-size="9" font-weight="700">Rebalancing Agent</text>
  <text x="681" y="240" text-anchor="middle" fill="#d1fae5" font-size="7.5">Pure Python</text>
  <text x="681" y="250" text-anchor="middle" fill="#d1fae5" font-size="7.5">&gt;40% sector warn</text>

  <rect x="748" y="210" width="91" height="38" rx="6" fill="url(#agentGrad)" opacity="0.85"/>
  <text x="793" y="226" text-anchor="middle" fill="white" font-size="9" font-weight="700">Writer Agent</text>
  <text x="793" y="240" text-anchor="middle" fill="#d1fae5" font-size="7.5">DSPy compiled</text>
  <text x="793" y="250" text-anchor="middle" fill="#d1fae5" font-size="7.5">Guardrails AI</text>

  <!-- Arrow down -->
  <line x1="430" y1="250" x2="430" y2="270" stroke="#475569" stroke-width="1.2" stroke-dasharray="3,2"/>

  <!-- ── ROW 4: ORCHESTRATOR ── -->
  <text x="430" y="283" text-anchor="middle" fill="#94a3b8" font-size="10" letter-spacing="1">LANGGRAPH ORCHESTRATOR · 8 NODES · 60–90s PIPELINE</text>

  <!-- Execution flow boxes -->
  <rect x="30" y="291" width="120" height="32" rx="6" fill="url(#orchGrad)" opacity="0.85"/>
  <text x="90" y="306" text-anchor="middle" fill="#1c1917" font-size="9" font-weight="700">① finance_node</text>
  <text x="90" y="318" text-anchor="middle" fill="#292524" font-size="7.5">personal context</text>

  <line x1="150" y1="307" x2="175" y2="307" stroke="#fbbf24" stroke-width="1.5" marker-end="url(#arr)"/>

  <rect x="175" y="291" width="148" height="32" rx="6" fill="url(#orchGrad)" opacity="0.85"/>
  <text x="249" y="306" text-anchor="middle" fill="#1c1917" font-size="9" font-weight="700">② PARALLEL NODE</text>
  <text x="249" y="318" text-anchor="middle" fill="#292524" font-size="7.5">data + research (asyncio.gather)</text>

  <line x1="323" y1="307" x2="348" y2="307" stroke="#fbbf24" stroke-width="1.5"/>

  <rect x="348" y="291" width="148" height="32" rx="6" fill="url(#orchGrad)" opacity="0.85"/>
  <text x="422" y="306" text-anchor="middle" fill="#1c1917" font-size="9" font-weight="700">③ PARALLEL NODE</text>
  <text x="422" y="318" text-anchor="middle" fill="#292524" font-size="7.5">risk + code (asyncio.gather)</text>

  <line x1="496" y1="307" x2="521" y2="307" stroke="#fbbf24" stroke-width="1.5"/>

  <rect x="521" y="291" width="120" height="32" rx="6" fill="url(#orchGrad)" opacity="0.85"/>
  <text x="581" y="306" text-anchor="middle" fill="#1c1917" font-size="9" font-weight="700">④ rebalancing</text>
  <text x="581" y="318" text-anchor="middle" fill="#292524" font-size="7.5">sector allocation</text>

  <line x1="641" y1="307" x2="666" y2="307" stroke="#fbbf24" stroke-width="1.5"/>

  <rect x="666" y="291" width="163" height="32" rx="6" fill="url(#orchGrad)" opacity="0.85"/>
  <text x="747" y="306" text-anchor="middle" fill="#1c1917" font-size="9" font-weight="700">⑤ writer_node → END</text>
  <text x="747" y="318" text-anchor="middle" fill="#292524" font-size="7.5">DSPy · Guardrails AI</text>

  <!-- Arrow down -->
  <line x1="430" y1="324" x2="430" y2="344" stroke="#475569" stroke-width="1.2" stroke-dasharray="3,2"/>

  <!-- ── ROW 5: INTELLIGENCE LAYER ── -->
  <text x="430" y="356" text-anchor="middle" fill="#94a3b8" font-size="10" letter-spacing="1">INTELLIGENCE LAYER</text>

  <!-- RAG -->
  <rect x="22" y="364" width="180" height="50" rx="7" fill="#1e293b" stroke="#6366f1" stroke-width="1.2"/>
  <text x="112" y="380" text-anchor="middle" fill="#a5b4fc" font-size="9.5" font-weight="700">RAG Pipeline</text>
  <text x="112" y="394" text-anchor="middle" fill="#c7d2fe" font-size="8">mxbai-embed-large · 1024-dim</text>
  <text x="112" y="406" text-anchor="middle" fill="#c7d2fe" font-size="8">5 tickers · 1,640 chunks · pgvector</text>

  <!-- Memory -->
  <rect x="220" y="364" width="160" height="50" rx="7" fill="#1e293b" stroke="#10b981" stroke-width="1.2"/>
  <text x="300" y="380" text-anchor="middle" fill="#6ee7b7" font-size="9.5" font-weight="700">Mem0 Memory</text>
  <text x="300" y="394" text-anchor="middle" fill="#a7f3d0" font-size="8">Long-term user context</text>
  <text x="300" y="406" text-anchor="middle" fill="#a7f3d0" font-size="8">vector search · dedup</text>

  <!-- Temporal -->
  <rect x="398" y="364" width="160" height="50" rx="7" fill="#1e293b" stroke="#f59e0b" stroke-width="1.2"/>
  <text x="478" y="380" text-anchor="middle" fill="#fcd34d" font-size="9.5" font-weight="700">Temporal Workflows</text>
  <text x="478" y="394" text-anchor="middle" fill="#fde68a" font-size="8">Durable · crash-safe</text>
  <text x="478" y="406" text-anchor="middle" fill="#fde68a" font-size="8">Morning briefing · 8 AM cron</text>

  <!-- Observability -->
  <rect x="576" y="364" width="260" height="50" rx="7" fill="#1e293b" stroke="#ef4444" stroke-width="1.2"/>
  <text x="706" y="380" text-anchor="middle" fill="#fca5a5" font-size="9.5" font-weight="700">Observability  ×  3 Layers</text>
  <text x="706" y="394" text-anchor="middle" fill="#fecaca" font-size="8">LangSmith  ·  AgentOps  ·  W&amp;B Weave</text>
  <text x="706" y="406" text-anchor="middle" fill="#fecaca" font-size="8">pipeline traces · agent decisions · eval scores</text>

  <!-- Arrow down -->
  <line x1="430" y1="416" x2="430" y2="434" stroke="#475569" stroke-width="1.2" stroke-dasharray="3,2"/>

  <!-- ── ROW 6: DATA LAYER ── -->
  <text x="430" y="446" text-anchor="middle" fill="#94a3b8" font-size="10" letter-spacing="1">DATA LAYER</text>

  <rect x="30" y="453" width="145" height="32" rx="6" fill="#1e293b" stroke="#38bdf8" stroke-width="1"/>
  <text x="102" y="468" text-anchor="middle" fill="#7dd3fc" font-size="9" font-weight="600">PostgreSQL + pgvector</text>
  <text x="102" y="480" text-anchor="middle" fill="#bae6fd" font-size="7.5">9 tables · IVFFlat index</text>

  <rect x="193" y="453" width="120" height="32" rx="6" fill="#1e293b" stroke="#f87171" stroke-width="1"/>
  <text x="253" y="468" text-anchor="middle" fill="#fca5a5" font-size="9" font-weight="600">Redis Cache</text>
  <text x="253" y="480" text-anchor="middle" fill="#fecaca" font-size="7.5">15-min TTL · pub/sub</text>

  <rect x="331" y="453" width="130" height="32" rx="6" fill="#1e293b" stroke="#34d399" stroke-width="1"/>
  <text x="396" y="468" text-anchor="middle" fill="#6ee7b7" font-size="9" font-weight="600">Composio</text>
  <text x="396" y="480" text-anchor="middle" fill="#a7f3d0" font-size="7.5">Gmail · WhatsApp alerts</text>

  <rect x="479" y="453" width="130" height="32" rx="6" fill="#1e293b" stroke="#a78bfa" stroke-width="1"/>
  <text x="544" y="468" text-anchor="middle" fill="#c4b5fd" font-size="9" font-weight="600">Ollama (local)</text>
  <text x="544" y="480" text-anchor="middle" fill="#ddd6fe" font-size="7.5">qwen2.5:7b · zero cost</text>

  <rect x="627" y="453" width="200" height="32" rx="6" fill="#1e293b" stroke="#fb923c" stroke-width="1"/>
  <text x="727" y="468" text-anchor="middle" fill="#fdba74" font-size="9" font-weight="600">E2B Sandbox</text>
  <text x="727" y="480" text-anchor="middle" fill="#fed7aa" font-size="7.5">safe Python execution · DCF · Monte Carlo</text>

  <!-- ── FOOTER METRICS ── -->
  <rect x="22" y="500" width="816" height="42" rx="8" fill="#0f172a" stroke="#334155" stroke-width="1"/>
  <text x="430" y="516" text-anchor="middle" fill="#64748b" font-size="8.5" letter-spacing="0.5">KEY METRICS</text>
  <text x="100" y="533" text-anchor="middle" fill="#38bdf8" font-size="9" font-weight="700">7 Agents</text>
  <text x="100" y="543" text-anchor="middle" fill="#7dd3fc" font-size="7.5">5 frameworks</text>

  <text x="215" y="533" text-anchor="middle" fill="#38bdf8" font-size="9" font-weight="700">43 MCP Tools</text>
  <text x="215" y="543" text-anchor="middle" fill="#7dd3fc" font-size="7.5">7 servers</text>

  <text x="330" y="533" text-anchor="middle" fill="#38bdf8" font-size="9" font-weight="700">60–90s Pipeline</text>
  <text x="330" y="543" text-anchor="middle" fill="#7dd3fc" font-size="7.5">vs 120s sequential</text>

  <text x="445" y="533" text-anchor="middle" fill="#38bdf8" font-size="9" font-weight="700">1,640 Chunks</text>
  <text x="445" y="543" text-anchor="middle" fill="#7dd3fc" font-size="7.5">5 tickers · RAG</text>

  <text x="560" y="533" text-anchor="middle" fill="#38bdf8" font-size="9" font-weight="700">$0.00 Embedding</text>
  <text x="560" y="543" text-anchor="middle" fill="#7dd3fc" font-size="7.5">fully local Ollama</text>

  <text x="675" y="533" text-anchor="middle" fill="#38bdf8" font-size="9" font-weight="700">9 DB Tables</text>
  <text x="675" y="543" text-anchor="middle" fill="#7dd3fc" font-size="7.5">pgvector + pgcrypto</text>

  <text x="780" y="533" text-anchor="middle" fill="#38bdf8" font-size="9" font-weight="700">3-Layer Observability</text>
  <text x="780" y="543" text-anchor="middle" fill="#7dd3fc" font-size="7.5">LangSmith·AgentOps·Weave</text>
</svg>
```

---

## Agents

| Agent | Framework | Key Capability | Output |
|---|---|---|---|
| Finance Agent | Pure Python | z-score anomaly detection (σ = 1.5) | Health Score 0–100, surplus, subscriptions |
| Research Agent | asyncio | parallel fetch — market + news + SEC | Sentiment, macro context |
| Data Agent | PydanticAI | schema-validated numbers, Redis 15-min TTL | `FinancialSnapshot` with confidence flag |
| Risk Agent | CrewAI | 3-node multi-agent debate | Bull / Bear / Moderator verdict |
| Code Agent | Smolagents + E2B | sandbox-executed Python | DCF intrinsic value, Monte Carlo distribution |
| Rebalancing Agent | Pure Python | >40% sector concentration warning | Rebalance recommendation |
| Writer Agent | DSPy + LangGraph | compiled few-shot prompt, Guardrails AI validated | Final investment memo |

---

## MCP Servers

| Server | Tools | Data Source |
|---|---|---|
| `market_server` | 10 | yfinance — price, P/E, market cap, historical |
| `sec_edgar_server` | 3 | SEC EDGAR — 10-K / 10-Q filing URLs |
| `news_server` | 3 | Financial headlines + sentiment |
| `finance_server` | 5 | PostgreSQL — transactions, anomalies, subscriptions |
| `calculator_server` | 13 | DCF, WACC, CAGR, capital gains, tax comparison |
| `tax_server` | 5 | 80C / HRA / slab — old vs new regime |
| `portfolio_server` | 4 | Holdings, P&L, sector allocation |

**Total: 43 tools across 7 servers**

---

## RAG Pipeline — Phase 2

| Property | Value |
|---|---|
| Embedding model | `mxbai-embed-large` via Ollama (1,024-dim) |
| Vector store | pgvector inside PostgreSQL — IVFFlat, 100 lists |
| Generation model | `qwen2.5:7b` via Ollama |
| Tickers indexed | AAPL, MSFT, TSLA, GOOGL, AMZN |
| Total chunks | ~1,640 |
| Embedding cost | **$0.00** — fully local |
| Chunking strategy | HTML-parsed 10-K/10-Q with XBRL tags stripped |

---

## DSPy Prompt Optimization — Phase 5

| Property | Value |
|---|---|
| Algorithm | BootstrapFewShot |
| Training examples | 15 input → output pairs |
| Optimizer target | Writer Agent prompt |
| Validation | Guardrails AI — blocks `risk_score > 10`, invalid recommendations |
| Output | Compiled program saved to JSON, loaded at runtime |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Orchestration | LangGraph (8-node state machine) + Temporal (durable workflows) |
| Agents | PydanticAI · CrewAI · Smolagents · Pure Python |
| LLM | qwen2.5:7b via Ollama (local) · Groq fallback |
| RAG | Custom indexer · pgvector · mxbai-embed-large |
| Memory | Mem0 (long-term, cross-session) |
| Prompt Optimization | DSPy BootstrapFewShot |
| Output Validation | Guardrails AI + Pydantic v2 |
| Code Execution | E2B Sandbox |
| Database | PostgreSQL 16 — 9 tables, pgvector, pgcrypto |
| Cache | Redis — 15-min TTL, pub/sub price alerts |
| Notifications | Composio → Gmail + WhatsApp |
| Observability | LangSmith · AgentOps · W&B Weave |
| Backend | FastAPI |
| Frontend | Streamlit |

---

## Screenshots Needed

> Take these **in this order** to match the README sections.

| # | Screenshot | What to capture |
|---|---|---|
| 1 | **Streamlit — Full Analysis** | Run a query like `"Should I invest ₹20,000 in RELIANCE.NS?"` · capture the full memo output |
| 2 | **LangSmith — Pipeline Trace** | Open a completed run · show the 8-node tree with latency per node |
| 3 | **AgentOps Dashboard** | Session view showing tool calls, LLM calls, and agent decisions |
| 4 | **W&B Weave — Eval Scores** | Show DSPy baseline vs compiled comparison table |
| 5 | **Portfolio P&L View** | Streamlit section with holdings, current value, sector allocation pie |
| 6 | **Code Agent — DCF Output** | Terminal or UI showing Monte Carlo distribution and DCF intrinsic value |
| 7 | **FastAPI Swagger UI** | `localhost:8000/docs` — show all endpoints |
| 8 | **Morning Briefing** | Gmail or WhatsApp screenshot of the 8 AM Composio notification |
| 9 | **Mem0 Memory Read** | Console or UI showing past memories retrieved at pipeline start |
| 10 | **DB Schema** | `psql \dt` or pgAdmin showing all 9 tables |

---

## Quick Start

```bash
git clone https://github.com/YOUR_USERNAME/WealthOS
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
```

---

## Project Status

| Phase | Description | Status |
|---|---|---|
| 0 | Foundation — PostgreSQL · Redis · project structure | ✅ |
| 1 | 7 MCP servers — 43 tools | ✅ |
| 2 | RAG pipeline — 1,640 chunks · pgvector | ✅ ~80% |
| 3 | 7 agents standalone — Finance · Research · Data · Risk · Code · Rebalancing · Writer | ✅ |
| 4 | LangGraph orchestrator — 8 nodes · FastAPI | ✅ |
| 5 | DSPy optimization · Guardrails AI validation | ✅ |
| 6 | Mem0 memory · Temporal workflows · Morning briefing · Composio | ✅ |
| 7 | Observability — LangSmith · AgentOps · W&B Weave | ✅ |
| 8 | Streamlit frontend | ✅ |
| 9 | Docker + CI/CD → AWS | 🔄 Planned |

---

<div align="center">
<sub>Built by Aman · <a href="https://github.com/AmanDataGuy">GitHub</a> · <a href="https://linkedin.com">LinkedIn</a></sub>
</div>
