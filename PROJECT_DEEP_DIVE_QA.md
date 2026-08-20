# WealthOS — Deep Dive Q&A

> A from-first-principles walkthrough of WealthOS: what it is, how every piece works, why it was built this way, what was tested, what broke, and how it was fixed. Written in plain language, verified against the actual code (not the README's marketing version).
>
> Format: every question has a short "TL;DR" answer plus a longer plain-English explanation. File paths and line numbers are cited so you can jump to the real code.

---

## Table of Contents

1. [Big Picture & Motivation](#section-a-big-picture--motivation)
2. [Architecture & LangGraph Orchestration](#section-b-architecture--langgraph-orchestration)
3. [Agents Deep Dive](#section-c-agents-deep-dive)
4. [MCP Protocol & Servers](#section-d-mcp-protocol--servers)
5. [RAG Pipeline](#section-e-rag-pipeline)
6. [Memory Systems — Mem0 vs Qdrant vs DSPy](#section-f-memory-systems--mem0-vs-qdrant-vs-dspy)
7. [DSPy Prompt Optimization](#section-g-dspy-prompt-optimization)
8. [Evaluation & Testing Methodology](#section-h-evaluation--testing-methodology)
9. [Observability](#section-i-observability)
10. [Security, Auth & Rate Limiting](#section-j-security-auth--rate-limiting)
11. [Infra & Deployment](#section-k-infra--deployment)
12. [What Went Wrong — Bugs Found & Fixed](#section-l-what-went-wrong--bugs-found--fixed)
13. [Case-Based Scenarios](#section-m-case-based-scenarios)
14. [Comparison Questions](#section-n-comparison-questions)

---

## Section A: Big Picture & Motivation

### Q1. What does WealthOS actually do, in one sentence?
**TL;DR:** It turns a question like "Should I invest ₹20,000 in Reliance right now?" into a personalized, evidence-backed investment memo in under 90 seconds.

It is not a generic stock-tip generator. It reads the user's actual bank transactions, EMIs, and savings goals from Postgres, pulls live market data and SEC filings, runs a real DCF valuation in a sandboxed Python interpreter, debates the risk from two angles, checks the recommendation against the user's portfolio concentration, and writes a memo that cites its numbers and their source. The output changes based on who is asking — the same "should I buy NVDA" question gets a different answer for someone with ₹5,000 surplus and high debt than for someone with ₹50,000 surplus and no debt.

### Q2. Why build this as a multi-agent pipeline instead of one big prompt to an LLM?
**TL;DR:** Because the task naturally decomposes into independent sub-problems (financial health, market research, risk, valuation math, portfolio fit, writing) that need different tools, different data sources, and — critically — some of them need to run in parallel to keep latency down.

A single mega-prompt would need to: query a database, call 45 different external tools, run actual Python code for DCF/Monte Carlo, and reason about risk from two different angles (a stock-specific analyst and a macro analyst) — all in one LLM call. That doesn't work well in practice: the model would need every tool's schema in context at once, its reasoning would be a black box (no intermediate outputs to trace or test), and there's no way to parallelize independent work. Splitting it into 8 focused nodes lets each one have a narrow, testable responsibility, lets independent nodes run concurrently via `asyncio.gather`, and lets you evaluate/debug each stage in isolation (e.g. you can unit-test the Risk Agent's scoring logic without touching the Writer Agent).

### Q3. What frameworks does the project actually combine, and why so many?
**TL;DR:** LangGraph (orchestration), FastAPI (backend), Streamlit (frontend), MCP (tool protocol), DSPy (prompt compilation), Mem0 (cross-session memory), Qdrant (vector store), PostgreSQL (structured data), Redis (cache), E2B (sandboxed code execution), Temporal (durable cron), LangSmith + W&B Weave (observability/eval).

Each one earns its place by solving a problem the others don't:
- **LangGraph** — state machine with built-in parallel fan-out/fan-in (`asyncio.gather` semantics baked into graph edges).
- **MCP** — a standard protocol so "tools" aren't just Python function calls; they're processes you can swap, sandbox, or reuse across agents/languages.
- **DSPy** — treats the prompt as a compiled artifact optimized against a metric, not hand-tuned text.
- **Mem0** — cross-session personalization without building a memory system from scratch.
- **Qdrant** — hybrid (dense + sparse) vector search with server-side fusion, used for both document RAG and "semantic memory of past decisions."
- **E2B** — running LLM-generated financial math (DCF, Monte Carlo) safely, outside the API process.
- **Temporal** — a cron job (morning briefing) that survives crashes and retries automatically, instead of a fragile `cron` + `try/except`.

### Q4. Is this project "production-grade," or is it a portfolio/demo project? Be honest.
**TL;DR:** It's a demo project built to *look and behave* like a production system — the architecture is real and mostly correctly wired, but several pieces are explicitly stubbed, hardcoded, or dead-wired (not actually running in CI). It's best described as "production-shaped, demo-grade."

Concrete evidence for both sides:
- **Real:** the MCP stdio subprocess transport genuinely spawns real subprocesses and talks JSON-RPC (`services/mcp_client.py`), the LangGraph parallel fan-out is real `asyncio.gather`, the hybrid RAG search genuinely does server-side RRF fusion in Qdrant, DSPy's `BootstrapFewShot` genuinely compiles a real few-shot prompt from a graded dataset.
- **Demo-grade:** default seeded credentials (`admin/wealthos123`) baked into every startup, a "Mock mode" checkbox in the Streamlit UI that fabricates a canned memo without touching the backend, `/analyze/stream` fakes token streaming by running the full pipeline first and then drip-feeding the finished string, the DeepEval CI gate references a script (`eval/run_deepeval.py`) that doesn't exist in the repo, and cookie-based "auth" that's just two unsigned strings a user could set by hand.

Knowing exactly where that line is — and being able to say precisely which parts are real — is itself the useful skill this project demonstrates.

### Q5. Who is this for — what's the actual user story?
**TL;DR:** A retail investor (the demo skews toward the Indian market) who wants investment advice that accounts for their actual cash flow, not just the stock's fundamentals.

The differentiator vs. a plain "analyze AAPL" chatbot is the Personal Finance Fit section and the Rebalancing Agent — WealthOS reads real Postgres transaction data (or the demo seed data) to compute a monthly surplus and health score, then folds that into the verdict. A memo for someone with a ₹30,000 EMI and ₹18,000 surplus explicitly says how much of a ₹20,000 investment they can actually absorb without going cash-negative that month.

### Q6. What's the actual latency/cost budget, and why does it matter?
**TL;DR:** Full pipeline first run: 45–90 seconds, ~6,000 tokens, roughly $0.0005 per analysis on Groq. Cache hits (Redis) bring that down to 8–15 seconds.

This matters because the whole design is shaped around Groq's free-tier token-per-minute limits (12K TPM for the 70B model). That's *why* the LLM client has key rotation across up to 3 keys, why DSPy compilation deliberately uses the smaller/cheaper `llama-3.1-8b-instant` model (30K TPM) rather than the 70B judge model, and why the eval scripts sleep between calls (`eval/dspy_optimizer.py` sleeps 15s between dev-set eval calls specifically to dodge rate limits).

### Q7. What's the single most impressive piece of engineering in this codebase?
**TL;DR:** The hybrid RAG retrieval pipeline in `rag/query_engine.py` — dense + sparse search fused server-side inside Qdrant via native RRF, then reranked by Cohere, with a staleness-decay annotation layered on top.

It's impressive not because any one piece is exotic, but because the whole pipeline is coherent: two independent retrieval signals (semantic similarity via MiniLM embeddings, keyword relevance via BM25 sparse vectors) are combined using Qdrant's `Prefetch` + `FusionQuery(fusion=Fusion.RRF)` API — meaning the fusion math runs inside the vector database, not hand-rolled in Python. Then a cross-encoder reranker (Cohere) re-scores the fused candidates for actual relevance to the query. Then, on top of retrieval quality, a staleness half-life score is computed per chunk and injected as a warning into the LLM's context, so a 3-year-old "risk factors" chunk doesn't get treated the same as last quarter's financials. That's three independent, well-reasoned retrieval-quality mechanisms stacked correctly.

### Q8. What's the single weakest piece of engineering, and why keep it that way for now?
**TL;DR:** The DeepEval CI gate (`.github/workflows/eval.yml`) — it calls a script (`eval/run_deepeval.py`) that doesn't exist and installs Gemini credentials for a judge model that isn't the one actually implemented (`GroqJudge`). It would fail immediately if it ever ran.

This is left as-is (rather than silently "fixed" by deleting the workflow) because it's a genuinely useful artifact for demonstrating awareness: recognizing "this CI gate is decorative, not functional" during a walkthrough is a stronger signal of engineering maturity than pretending everything is wired up. The fix is well-understood (rename the entry point, swap the judge), it's just not been prioritized over other work.

---

## Section B: Architecture & LangGraph Orchestration

### Q9. Walk me through the exact node order in the LangGraph pipeline.
**TL;DR:** `router_node → finance_node → [data_node + research_node in parallel] → [risk_node + code_node in parallel] → validation_node → rebalancing_node → writer_node`.

- **router_node** — classifies investment horizon (short/mid/long), checks how well-indexed the ticker is in Qdrant, checks how experienced the user is, and builds a `fetch_plan` dict that later nodes read.
- **finance_node** — reads Mem0 memory, runs the Finance Agent (health score, anomaly detection).
- **data_node** and **research_node** run concurrently via `asyncio.gather` — one pulls structured market data (price, financials), the other pulls SEC filings via RAG + news sentiment.
- **risk_node** and **code_node** run concurrently — risk runs a 3-node internal debate (macro analyst, stock analyst, scorer), code runs a real DCF/Monte Carlo simulation inside an E2B sandbox.
- **validation_node** — runs Guardrails-style output checks.
- **rebalancing_node** — checks portfolio concentration against the proposed action.
- **writer_node** — the DSPy-compiled writer produces the final 7-section memo, then writes to Mem0 and (best-effort) indexes the verdict into Qdrant's `user_analyses` collection.

### Q10. Why put the parallel fan-out where it is (data+research, then risk+code) instead of running everything in parallel from the start?
**TL;DR:** Because later stages depend on earlier ones — risk analysis needs the financial snapshot from `data_node`/`research_node` to reason about, and the writer needs literally everything. The two parallel pairs are the two places in the dependency graph where truly independent work exists.

`data_node` (structured numbers) and `research_node` (qualitative filings/news) don't depend on each other — they can run at the same time and both feed into risk/code. Similarly `risk_node` (LLM reasoning about danger) and `code_node` (deterministic DCF math in a sandbox) are independent of each other but both depend on the data/research stage completing first. This is roughly a 2x speedup over a fully linear chain for those two stages, which is significant given LLM calls dominate latency.

### Q11. What is the router_node actually doing, mechanically?
**TL;DR:** Three cheap classifications, then a plan. It classifies the query's time horizon with a small/fast LLM call, checks how many Qdrant chunks exist for the ticker (well/thin/not-indexed), checks how many past analyses the user has (new/returning/power), and builds a `fetch_plan` dict that downstream nodes consult to decide what to fetch or skip.

Concretely: `_classify_horizon()` calls Groq's `llama-3.1-8b-instant` (a smaller, faster model — this classification doesn't need the 70B model), asking for a horizon plus a confidence score. If confidence is below 0.65, the router *overrides* the model's answer and forces `"long"` — a deliberate "safer default" bias, since a wrong "short-term" classification skips the DCF entirely, which is a bigger miss than unnecessarily running one. Company tier thresholds: ≥100 Qdrant chunks = `well_indexed`, ≥10 = `thin_indexed`, else `not_indexed`. User tier: ≥6 past analyses = `power`, 1–5 = `returning`, 0 = `new`.

### Q12. What happens when a user asks about a ticker that's never been indexed into Qdrant?
**TL;DR:** The router fires off SEC EDGAR (or BSE, for Indian tickers) indexing as a **background task** — `asyncio.create_task()` — so the current request doesn't block waiting for it. The current analysis proceeds with whatever thin data is available (or none), but the *next* analysis of that ticker benefits from the freshly indexed filing.

This is a "fire-and-forget with eventual consistency" pattern: rather than making the user wait 20-40 seconds for a full 10-K to download, chunk, embed, and index before their memo can even start, the system accepts a lower-quality first answer and pre-warms the cache for next time. It's the same trade-off CDNs make with cache misses.

### Q13. List every field in the WealthOSState TypedDict.
**TL;DR:** State carries the full life of a request: identity/query fields, router outputs, per-node outputs, and final artifacts.

Grouped by who writes them:
- **Input:** `query`, `tickers`, `user_id`, `investment_amount`, `investment_horizon` (optional override).
- **Router-written:** `investment_horizon` (final), `company_tier`, `user_tier`, `fetch_plan`.
- **Finance-written:** `financial_snapshot`/health score data, `user_memory` (from Mem0).
- **Data/Research-written:** market data snapshot, RAG context, news sentiment.
- **Risk-written:** `risk_report` (risk score, verdict lean, macro context).
- **Code-written:** DCF intrinsic value, Monte Carlo outputs, or a placeholder if E2B is unavailable.
- **Rebalancing-written:** concentration warnings/actions.
- **Writer-written:** `final_memo`, plus side-effects (Mem0 write, Qdrant `user_analyses` write).
- **Cross-cutting:** `messages` (a human-readable log list like `"Router Node ✅"` shown in the Streamlit "Agent log" expander), `error` (if any node fails).

### Q14. Why use LangGraph specifically instead of just writing an `async def pipeline()` function with `await`s?
**TL;DR:** Because LangGraph gives you a declarative graph (nodes + edges) that's independently testable, traceable per-node, and where the parallel fan-out/fan-in is expressed structurally rather than as ad-hoc `asyncio.gather()` calls scattered through a monolithic function.

A hand-written async function *would* work functionally, but you'd lose: (1) the ability to visualize/trace the pipeline as a graph, (2) a clean seam for LangSmith's `@trace_node` per-node instrumentation, (3) the ability to swap/reorder nodes without touching a giant function body, and (4) LangGraph's built-in state-merging semantics for parallel branches (each parallel node returns a partial state dict, and LangGraph merges them without you writing manual dict-merge code).

### Q15. What happens if one node in the middle of the graph throws an exception?
**TL;DR:** In practice, most nodes wrap their core logic in `try/except` and write into `state["error"]` rather than letting the exception propagate and kill the whole graph run — the pipeline is built to degrade, not crash. (E.g. `data_node` failing doesn't stop `research_node`'s parallel branch; a failed `code_node` still lets the writer produce a memo, just without a Valuation Analysis section.)

This is the same "fail-open, log the gap" philosophy seen throughout the codebase — Mem0 read failures return `""`, Qdrant write failures are swallowed, RAG failures return empty context. The tradeoff: the system almost never hard-crashes, but a silently degraded memo (missing a section, or citing stale data) looks the same to the end user as a fully-successful one unless you specifically go looking at `state["error"]` or the `messages` log.

### Q16. How does the graph decide what fetch_plan each node should follow for different investment horizons?
**TL;DR:** `_build_fetch_plan(horizon, company_tier)` returns a dict of booleans that nodes read to skip/include work — e.g. short-term skips the 5-year DCF and includes technicals + options; long-term does the opposite.

Short-term: `use_dcf=False`, `use_technicals=True`, `use_options=True`, `use_news_full=True`. Mid-term: DCF included but shortened, technicals still on. Long-term (the default): full 5-year DCF + Monte Carlo, no technicals/options, heavier weight on 10-K structural risk factors. This is what lets the same 8-node graph serve a day-trader's question and a "should I hold this for retirement" question without duplicating the whole pipeline.

### Q17. Does validation_node use Guardrails AI, or something homegrown?
**TL;DR:** `guardrails/validators.py` implements custom Pydantic v2 validators — not the third-party Guardrails AI library, despite the directory name suggesting it. It's a homegrown "guardrails" module.

The checks are things like: risk score must be in [1,10], verdict must be one of {Buy, Hold, Avoid}, and required memo sections must be present. This is deliberately simple/deterministic validation (no LLM call), which matters because it needs to be fast and 100% reliable — you don't want your *safety check* to itself be probabilistic.

### Q18. What's the actual mechanism for "8 agents" vs "7 agents" — why do different parts of the codebase disagree?
**TL;DR:** It's a genuine inconsistency, not a trick question. `api/main.py`'s `/health` endpoint and module docstring say "7-agent," but the Streamlit sidebar and Analyze page caption say "8 agents active" / "8 AI agents." The router agent was added later (Phase 4) and some UI copy was updated to count it while the backend docstring/health payload wasn't.

If you count distinct `agents/*.py` files with a `run_*_agent()` entry point: router, finance, data, research, risk, code, rebalancing, writer = **8**. The "/health" endpoint's hardcoded `"agents": 7` is stale relative to that.

### Q19. Where does the Router Agent sit relative to Mem0 memory retrieval — does it use past-session context to classify horizon?
**TL;DR:** No — `_classify_horizon()` only looks at the current query + ticker; Mem0 read happens later, inside `finance_node`. The router's classification is stateless per-request.

This is a deliberate ordering choice: horizon classification is meant to be a fast, cheap, and *objective* read of what the user is literally asking right now ("quick trade" vs "long hold" language), not colored by their historical risk appetite. Personalization (via Mem0, the risk profile table, and past decision retrieval) is layered in downstream, in finance/risk/writer nodes — where it can meaningfully shape the *content* of the analysis, not the *shape* of the pipeline that runs.

### Q20. What does `messages` in state actually get used for, end to end?
**TL;DR:** It's a running human-readable log (`"Router Node ✅"`, `"Data Agent ✅"`, etc.) appended to by every node, ultimately shown to the end user in Streamlit's "Agent log" expander under the rendered memo — a transparency feature, not a debugging log.

It's also parsed (fragilely, via string-splitting on `"] "` and `" ✅"`/`" ❌"`) in `api/main.py` to extract which agents ran, for the `agents_invoked` column saved to `analysis_history`. This string-parsing approach is called out later as a code smell (see Q88) — a structured list of agent names would be far more robust than parsing a display string.

### Q21. Is there any circuit breaker or retry logic at the graph level (not per-tool)?
**TL;DR:** Not in `graph/graph.py`/`graph/nodes.py` itself — retries live at lower layers (Groq key rotation in `llm_client.py`, one MCP reconnect-and-retry in `mcp_client.py`). The graph-level Temporal mirror (`workflows/temporal_workflows.py`) *does* have retry policies (3 attempts, exponential backoff) per activity, but that's a separate, parallel implementation of the pipeline for durable/cron use — the live `/analyze` request path through `graph/graph.py` has no such retry wrapper.

This means a transient failure deep in, say, `research_node`'s RAG call during a live user-facing `/analyze` request has no automatic graph-level retry — it degrades to an empty context for that node and the pipeline continues. Only the Temporal-scheduled morning briefing path gets real retries.

### Q22. Why does the Temporal-mirrored pipeline exist separately from the LangGraph one — isn't that duplicated logic?
**TL;DR:** Yes, it's genuinely duplicated logic, and yes, that's a real design smell — but the intent is that Temporal's version exists for durable, crash-safe, *scheduled* execution (the morning briefing), while LangGraph's version exists for interactive, low-latency, *request/response* execution. They optimize for different things (durability vs. speed) and Temporal's retry/checkpoint machinery would add unwanted latency to a live user-facing request.

In its current state, `temporal_workflows.py`'s `finance_activity` is an explicit stub (hardcoded numbers, ignores `user_id`) — so in practice, only the *shape* of the pipeline is duplicated for demonstration purposes; the Temporal path isn't yet fully wired to the same real logic as the LangGraph path. A cleaner long-term design would have both call into the same underlying agent functions rather than maintaining two separate copies of the orchestration logic.

---

## Section C: Agents Deep Dive

### Q23. How does the Finance Agent's anomaly detection actually work — walk through the real numbers.
**TL;DR:** It flags a spending category as anomalous if the current month's total is more than 2x the historical monthly average for that category — implemented in `mcp_servers/finance_server.py`'s `analyze_spending()` tool (`avg > 0 and current > avg * 2`).

This is a simple ratio-based anomaly rule, not a statistical z-score model. It's cheap to compute (pure SQL aggregation + Python comparison), doesn't need a training period, and is easy to explain to a non-technical user ("you spent 2x more on dining than usual this month") — a deliberate readability-over-sophistication tradeoff for a personal-finance feature where false positives are annoying but false negatives are low-stakes.

### Q24. What exactly is the "health score" and how is it computed?
**TL;DR:** A composite 0–100 score derived from the user's actual transaction data — savings rate, spending anomalies, EMI/debt burden, and goal progress feed into it, computed inside the Finance Agent's logic (backed by `finance_server.py`'s `get_surplus`, `analyze_spending`, `get_emis` tools).

The debt-burden piece specifically flags risk when `total_monthly_emi / monthly_income > 0.5` (more than half of income going to loan payments) — a standard personal-finance rule of thumb. The health score is what feeds the "Personal Finance Fit" section of the memo, and is the mechanism by which the same stock recommendation differs across users with different financial situations.

### Q25. Describe the Risk Agent's "debate pattern" in detail — who are the two debaters and how do they resolve disagreement?
**TL;DR:** It's a 3-node internal LangGraph sub-graph: a **macro analyst** (reasons about interest rates, VIX, S&P 500 trend, Fed policy) and a **stock analyst** (reasons about the specific company's fundamentals/filings) run **in parallel**, then a **scorer** node reads both outputs plus the user's historical risk profile and produces the final 1–10 risk score and lean.

The macro analyst pulls real numbers via `_get_macro_context()` — live VIX, 10-year treasury yield, S&P 500 level, and Fed Funds Rate (via FRED if `FRED_API_KEY` is set, falling back to yfinance derivatives otherwise) — and classifies the environment into regimes (`vix_regime`: low/normal/high/crisis based on VIX thresholds 15/25/35; `rate_environment`: ultra_low/low/normal/elevated based on rate thresholds 1.5%/3.0%/5.0%). This means the macro analyst isn't just vibes — it's grounded in actual current market conditions, and it's cached for an hour so repeated analyses in the same hour don't re-fetch it. The scorer then injects the user's own historical Buy/Hold/Avoid pattern (from the `user_risk_profiles` Postgres table) so risk scoring is calibrated partly to the *individual user's* demonstrated risk tolerance, not a generic rubric.

### Q26. Why does the Code Agent use E2B specifically, instead of just running `eval()` or `exec()` on LLM-generated Python in the API process?
**TL;DR:** Security and blast-radius containment. E2B runs the DCF/Monte Carlo Python code in an isolated cloud sandbox (a separate container), so a malformed or malicious code snippet the LLM generates can't touch the API server's filesystem, environment variables, or network.

This matters specifically because the DCF/Monte Carlo code is *generated*, not hand-written — the pipeline effectively lets an LLM write and execute arbitrary Python. Running that with a bare `exec()` in-process would be a textbook remote-code-execution vulnerability (anyone who can influence the prompt, even indirectly via a crafted query, could potentially escape into the host process). E2B's sandboxing turns "arbitrary code execution" from a security incident into an expected, contained feature.

### Q27. What happens to the memo if the E2B sandbox is unavailable (key missing, service down)?
**TL;DR:** `code_node` returns an error state and the Writer Agent silently skips the Valuation Analysis section rather than crashing — though per `plan_ahead.md`'s own failure registry (F1), this gap is *not* always clearly labeled in the memo (a "[DCF not available]" placeholder is a documented fix, only "partially mitigated").

This is a good illustration of the project's general failure philosophy: prefer a degraded-but-complete output over a hard failure, but the tracking of *which* failures are cleanly surfaced to the end user vs. silently absorbed is inconsistent across the codebase — some failures get explicit placeholder text, others just leave a gap.

### Q28. What does the Rebalancing Agent actually check, and what's the exact threshold?
**TL;DR:** It flags sector concentration risk if any single sector exceeds 40% of the portfolio's total value — implemented in `mcp_servers/portfolio_server.py`'s `get_allocation()` tool (`262`).

If a user already holds 45% of their portfolio in IT-sector stocks and asks about buying another IT stock, the Rebalancing Agent surfaces that concentration risk so the memo can factor it into the Portfolio Impact section — buying more of an already-overweight sector should be flagged even if the stock itself looks fundamentally sound.

### Q29. Walk through exactly how the Writer Agent decides what goes in each of the 7 memo sections.
**TL;DR:** The DSPy `WriteMemo` signature's docstring (which DSPy treats as the instruction to optimize prompts around) explicitly enumerates the rules: 7 sections in a fixed order, lead with the verdict word, cite the user's actual numbers (surplus, health score) by value, never invent figures, bold key numbers in markdown, and the Final Verdict section must contain exactly 3 numbered reasons.

The 7 sections (for long-term horizon) are: Executive Summary, Financial Snapshot, Valuation Analysis, Risk Assessment, Portfolio Impact, Personal Finance Fit, Final Verdict. For short-term horizon, "Valuation Analysis" is replaced by "Trading Setup" (support/resistance, technical levels) and "Recent Catalysts"/"Options Activity" sections appear instead of a 5-year DCF discussion — the writer's behavior branches on `investment_horizon` from state.

### Q30. What is the "trust hierarchy" the Writer Agent uses when two data sources disagree on a number?
**TL;DR:** Live earnings > analyst consensus > 10-K guidance > 10-K historical. If a news article reports Q1 actual revenue that differs from what the last 10-K's guidance section projected, the writer is instructed to cite the actual/recent number and explicitly note the beat/miss, always labeling every number with its source and date (e.g. "Revenue $19.8B (Q1 2025 per Reuters)" vs "Revenue $19.3B (10-K FY2024)").

This rule exists because the RAG pipeline can retrieve chunks from filings that are months or years old alongside same-day news, and without an explicit precedence rule the LLM has no principled way to pick — it might silently average two conflicting numbers, or cite whichever appeared first in the context window (a positional bias risk).

### Q31. Explain the router's "on-demand indexing" flow in full detail — what triggers it and what happens under the hood.
**TL;DR:** Triggered when `company_tier == "not_indexed"` (fewer than 10 Qdrant chunks exist for the ticker). The router calls `asyncio.create_task(_on_demand_index(ticker))`, which runs SEC EDGAR (or BSE for `.NS` tickers) filing download → text extraction → hierarchical chunking → embedding → Qdrant upsert, all in the background while the rest of the pipeline proceeds with whatever's already available.

This is the single most interesting async pattern in the codebase: it's a genuine "populate the cache for next time, don't block this request" design. The tradeoff is honest — the *current* user gets a thinner-context memo for a never-before-seen ticker, but the *next* person (or the same person asking again) gets full RAG-quality retrieval, because by then the background task has completed and Qdrant holds the indexed chunks.

### Q32. Why did the Data Agent get described as "PydanticAI" in an earlier version of the README when it's actually raw asyncpg/httpx?
**TL;DR:** This was a documentation drift issue caught in an earlier audit — the README claimed PydanticAI was used for schema validation in the Data Agent, but the actual implementation uses plain `asyncpg` for Postgres queries and `httpx` for HTTP calls, validated with hand-written Pydantic models rather than the PydanticAI *framework* (which adds agentic tool-calling on top of Pydantic — a different, heavier thing than "just validate this dict against a schema"). This has since been corrected in the current README.

The lesson generalizes: READMEs describing an AI project's stack are exactly the kind of doc that rots fastest, because the stack for demo/portfolio projects tends to get simplified during iteration ("I don't need the full PydanticAI agent machinery, plain Pydantic models are enough") without anyone circling back to update the marketing copy.

### Q33. Does the Data Agent actually call the MCP servers via the MCP protocol, or does it import them directly as Python modules?
**TL;DR:** As of the current codebase, `services/mcp_client.py` is a genuine MCP stdio subprocess client (confirmed by reading it — it spawns `python <server_script.py>` as a real subprocess and speaks JSON-RPC over stdin/stdout using the official `mcp` SDK's `ClientSession`). This was previously a known gap (an earlier audit found agents doing `from mcp_servers.market_server import get_price` — a direct import bypassing the protocol entirely) that has since been fixed for at least the finance agent's migration to stdio.

The distinction matters a lot for an interview: "we use MCP" is a much weaker claim than "we spawn the server as an actual subprocess and negotiate the MCP handshake" — the former could be marketing-speak for "we imported the function," the latter is verifiable by reading `mcp_client.py`'s `connect()` method, which literally does `StdioServerParameters(command=sys.executable, args=[self.server_script])`.

### Q34. What's the resilience story if an MCP server subprocess crashes mid-call?
**TL;DR:** `MCPClient.call_tool()` has exactly **one** automatic retry: if the first `_do_call()` raises, it closes and reconnects the whole session (respawning the subprocess) and retries once. A second consecutive failure propagates up uncaught.

This is a deliberate, bounded resilience choice — enough to survive a transient hiccup (a subprocess that briefly stalls, a dropped pipe) without adding unbounded retry loops that could mask a genuinely broken server or blow the latency budget. It's not infinite-retry-with-backoff, which would be inappropriate for a user-facing, latency-sensitive request path.

### Q35. Explain the LLM client's Groq key rotation logic precisely — including its one notable quirk.
**TL;DR:** Up to 3 Groq API keys are loaded from env vars. The rotation loop tries each key in order, but **only rotates to the next key on an HTTP 429 (rate limit)**. Any other error — a 500, a timeout, a malformed response — breaks the loop immediately and returns an empty string, without trying the remaining keys.

This is a genuine, non-obvious behavioral quirk worth understanding: it means "rate limit" is treated as the *only* recoverable failure mode across keys, while every other failure is treated as fatal for that call. In practice this is a reasonable simplification (a 500 or malformed response from key #1 probably means something is wrong with the request itself, not the key, so trying key #2 with the same bad request likely wouldn't help) — but it's not documented anywhere as an explicit design decision, so a naive reader might assume *any* failure triggers rotation.

### Q36. How is LLM cost tracked, and is it accurate?
**TL;DR:** `_track_usage()` in `services/llm_client.py` accumulates token counts into a module-global dict `_session_cost`, using hardcoded per-million-token pricing constants (`$0.05` input / `$0.08` output — Groq's advertised llama-3.3-70b rates). It's accurate as of when those constants were written, but it will silently drift wrong if Groq changes pricing, since nothing re-fetches or validates the rate.

Also worth noting: this tracking is **in-memory and per-process** — it resets on every restart and isn't persisted anywhere, so it's useful for "cost of this one dev session" but not for cumulative billing/reporting across restarts or multiple API replicas.

---

## Section D: MCP Protocol & Servers

### Q37. What is MCP, in plain language, and why does this project use it instead of just calling functions directly?
**TL;DR:** MCP (Model Context Protocol) is a standard way for an AI application to discover and call "tools" that live in a separate process, over a well-defined JSON-RPC interface — instead of the AI app importing and calling Python functions directly in the same process.

The practical benefit here: each MCP server (market data, SEC filings, news, finance, calculator, tax, portfolio) is its own standalone process with its own dependencies, and any agent can talk to it the same way regardless of what language or framework wrote the server. It also gives you a natural sandbox boundary (a server crash doesn't take down the API process) and — because tools are declared with typed schemas via `@mcp.tool()` — the LLM can be handed a machine-readable list of available tools and their parameters without hand-written glue code.

### Q38. How does `@mcp.tool()` actually make a function "discoverable"?
**TL;DR:** It's a decorator from `fastmcp` that inspects the function's signature (parameter names, types, defaults) and docstring at import time, and auto-generates a JSON schema describing the tool — this is what gets sent to a client (or an LLM) asking "what tools do you have?" The function body runs exactly as normal Python once called; the decorator's job is purely discovery/schema generation plus wiring it into the MCP server's tool registry.

### Q39. How many total MCP tools does this project actually have — and is 45 the right number?
**TL;DR:** Yes — recounting the `@mcp.tool()` decorators directly (not trusting header comments, several of which were stale) gives exactly **45** across 7 servers: market (13), sec_edgar (5), news (4), finance (6), calculator (7), tax (4), portfolio (6).

Interesting detail: `market_server.py`'s own header comment claimed only 10 tools — it was undercounting by 3 (missing `get_technicals`, `get_options_data`, `get_macro_data`, which were added later without updating the comment). This is a small but real example of "trust the code, not the comment" — a recurring theme across this codebase's documentation.

### Q40. What real external data sources does the Market MCP server hit, and what's cached vs. live?
**TL;DR:** Almost everything routes through `yfinance` (Yahoo Finance's unofficial API), with `fredapi` (Federal Reserve Economic Data) as an optional supplement for macro data. Every tool is cached in Redis with a per-tool TTL — prices/history at 300s (5 min), financials/company info at 3600s (1hr), sector/currency data at 900–1800s (15–30 min), macro data at 3600s.

The technical indicators (`get_technicals` — RSI, MACD, Bollinger Bands) are **hand-rolled in pure Python**, not computed via TA-Lib or pandas-ta, directly from raw OHLCV bars pulled from yfinance. This is a deliberate no-extra-dependency choice — the math for RSI/MACD/Bollinger is well-known and not hard to implement correctly in ~50 lines, so pulling in a heavier technical-analysis library wasn't worth it.

### Q41. Explain the SEC EDGAR server's ticker→CIK lookup mechanism.
**TL;DR:** `get_cik()` downloads the *entire* `company_tickers.json` file from SEC's website (a full mapping of every public company's ticker to CIK number) and linear-scans it for a match, caching the result for 24 hours.

This is a simple-but-slightly-wasteful approach — downloading the whole company universe just to look up one ticker — but it's a one-time cost per 24h window thanks to caching, and it avoids needing a separate, potentially-stale local copy of the CIK mapping. The `get_financial_facts()` tool then pulls structured XBRL data and tries multiple tag aliases per metric (e.g. revenue tries `Revenues`, then `RevenueFromContractWithCustomerExcludingAssessedTax`, then `SalesRevenueNet` in order) because different companies/filing years use different XBRL tag conventions for conceptually the same line item — a real-world messiness of financial reporting standards that the code has to work around.

### Q42. The News server's sentiment scorer is described in its own docstring as a placeholder — what does that mean exactly?
**TL;DR:** `score_sentiment()` in `mcp_servers/news_server.py` is pure keyword matching against two hardcoded word lists (20 positive words like "beat"/"record"/"bullish", 19 negative words like "miss"/"downgrade"/"bearish") — whichever list an article's text matches more words from wins that article's sentiment bucket. The function's own docstring explicitly says: *"Lightweight rule-based scorer. In production this gets replaced by a Groq LLM call for better accuracy."*

This is a genuinely honest piece of self-documentation — the code isn't pretending to be more sophisticated than it is. A keyword scorer will misclassify sarcasm, negation ("not a great quarter" might match "great" as positive), and nuanced language, but it's essentially free (no LLM call, no latency, no cost) and good enough to demonstrate the *shape* of a sentiment feature without paying for an LLM call on every headline.

### Q43. How does the Reddit "sentiment" tool work without a Reddit API key?
**TL;DR:** It scrapes Reddit search result pages via Firecrawl (a web-scraping service that returns clean markdown) rather than using Reddit's official API, then extracts post titles/links with a regex against the markdown output: `\[([^\]]{15,200})\]\((https://www\.reddit\.com/r/[^\)]+)\)` — i.e. it's parsing markdown-formatted links, not structured JSON.

It filters out obvious navigation/UI noise ("search", "subscribe", "log in") via a hardcoded skip-word list, then runs the same keyword-based sentiment scorer on post titles only (not full post bodies or comments). This is a workaround-of-a-workaround — no official API, so scrape via a third-party service, then parse loosely-structured markdown with regex — which is fragile (a Firecrawl markdown format change would break the regex) but functional for a demo.

### Q44. Why does `finance_server.py` use a connection pool but `portfolio_server.py` opens a fresh connection per call — is that a bug?
**TL;DR:** It's an inconsistency, not a functional bug — both work correctly, but they're different engineering choices for the same problem (talking to Postgres), and having both patterns in the same codebase without a documented reason is worth flagging as a design smell rather than a deliberate architecture decision.

`finance_server.py` creates a shared `asyncpg.Pool` lazily (`min_size=2, max_size=10`) and reuses it across every tool call — the standard "correct" pattern for a server handling many requests, since connection setup has real overhead. `portfolio_server.py` instead does `asyncpg.connect()` / `conn.close()` per call. Under low load (a demo) this difference is invisible; under real concurrent load, `portfolio_server.py`'s pattern would create noticeably more connection-setup latency and could exhaust Postgres's max-connections limit faster.

### Q45. What's hardcoded and dev-only in the finance/tax/portfolio servers that a reviewer should catch?
**TL;DR:** Several things: `finance_server.py` has a hardcoded default DB credential fallback (`postgresql://wealthos_user:wealthos_pass@localhost:5432/wealthos`); `tax_server.py`'s advance-tax due dates are hardcoded to FY2024-25 specific calendar dates ("15 June 2024" etc.) that will become wrong once that fiscal year passes; `tax_server.py`'s HRA exemption calculation omits one of the three statutory legs (50%/40% of basic salary) so it likely overstates the exemption; and `market_server.py`'s peer-comparison tool (`get_competitors`) uses a hardcoded `PEER_MAP` dict covering only ~14 Indian tickers, falling back to a "please add this ticker manually" message for anything else.

These are all exactly the kind of thing that's fine for a demo (predictable behavior, easy to reason about) but would need real fixes before any production use — the tax dates in particular are a ticking time bomb that will silently produce wrong output once the fiscal year rolls over, with no code path that detects "this date is stale" and warns.

### Q46. Explain the `composio_client.py`'s `mock_db` — what's actually happening when a notification is sent?
**TL;DR:** `send_notification()` contains a hardcoded dict with exactly one entry (a nil UUID `00000000-...-000000000001` mapped to env-var-sourced email/phone), and an explicit `TODO` comment admitting this should really be a database lookup. For every other `user_id`, the lookup misses and falls back to the *same* global `NOTIFY_EMAIL`/`NOTIFY_PHONE` env vars — meaning in the current implementation, all notifications for all users go to one single hardcoded destination.

This is the clearest, most self-aware stub in the whole codebase — the author left a comment explaining exactly what's missing and why ("Phase 6 dev... single global env vars for now"). It's a good example of a *documented* shortcut vs. an *accidental* one — the difference matters a lot when evaluating engineering maturity: a shortcut you've named and explained is a decision; an undocumented one is a landmine for whoever reads the code next.

### Q47. Does the calculator server (XIRR, EMI, FIRE, etc.) call any external API?
**TL;DR:** No — it's the one MCP server that's 100% pure math, no network calls, no database, no caching needed (deterministic functions don't need caching). The one interesting piece is `xirr()`, which uses `scipy.optimize.brentq` (a bisection-style root finder) to solve for the annualized return rate that makes a custom XNPV function equal zero, bounded to a search range of `[-0.999, 10.0]` (i.e., -99.9% to 1000% annual return) with tolerance `1e-6`.

This is the correct way to compute XIRR for irregular cash flows (SIP-style investments with varying dates/amounts) — a closed-form formula doesn't exist for irregular cash flow timing, so you need a numerical root-finder, and `brentq` is a solid, standard choice (guaranteed convergence given a valid bracketing interval, unlike Newton's method which can diverge).

### Q48. What does the finance server's `get_emis` tool do if the `emis` table doesn't exist in the database?
**TL;DR:** It checks `information_schema.tables` first and, if the table is missing, returns a graceful "not yet created" stub response rather than throwing a SQL error — an explicit defensive pattern for a feature (EMI/loan tracking) that was added in a later phase and might not exist in every deployed database.

This existence-check-before-query pattern is a nice example of forward/backward compatibility handling in a codebase without a formal migration-versioning system — rather than assuming the schema is always up to date, the code defends against the specific case where it isn't.

---

## Section E: RAG Pipeline

### Q49. What embedding model does the RAG pipeline use, and why that one specifically?
**TL;DR:** `sentence-transformers/all-MiniLM-L6-v2`, producing 384-dimensional dense vectors, run locally on CPU with no API key required.

MiniLM-L6-v2 is a small, fast, well-established sentence-embedding model — good enough semantic quality for retrieval, small enough to run on CPU without a GPU or inference API cost, and requires zero API key (no OpenAI/Cohere embedding cost per document). Given the project's constraint of running mostly on free tiers, a local no-cost embedding model that's "good enough" beats a marginally-better paid API embedding for this use case.

### Q50. Walk through the exact chunking strategy used for SEC filings.
**TL;DR:** A two-level hierarchy: level-1 "parent" chunks (~1500 words, one per detected filing section) and level-2 "child" chunks (~150 words each, the units that actually get embedded and searched). Retrieval finds level-2 children by similarity, then fetches their level-1 parent for extra context around the match.

Concretely: `chunk_prose()` splits text on sentence boundaries via regex, then greedily packs sentences into a buffer until adding the next one would exceed 150 words, flushes, and starts a new chunk — no sliding-window overlap between chunks. Chunks under 20 words are discarded, and a heuristic (more than 5 words over 30 characters) drops chunks that look like garbled OCR/PDF-extraction noise. As sections change (detected via section-header pattern matching), all buffered children get tagged with a `parent_id` pointing to their now-finalized parent, and the parent itself is the concatenated prose of all its children, capped at 3000 characters.

### Q51. Why parent/child hierarchical chunking instead of a simple fixed-size sliding window with overlap (the "textbook" RAG chunking approach)?
**TL;DR:** Because a 150-word chunk alone often lacks enough surrounding context for an LLM to correctly interpret it (e.g. a chunk that's just a table of numbers, with no label saying what fiscal year or line item it's from) — the parent chunk gives that context back at retrieval time without making the *searched* unit so large that embedding similarity gets diluted.

Sliding-window overlap solves a different problem (avoiding cutting a sentence mid-thought at chunk boundaries) but doesn't solve the "I retrieved a fragment with no context" problem. This project's approach — search small, retrieve small+its-parent — is a deliberate two-birds design: keep the searchable unit small and semantically tight (good embedding quality), but hand the LLM the small unit *plus* its broader section for grounding.

### Q52. Explain hybrid search step by step, exactly as coded.
**TL;DR:** Dense (semantic) + sparse (BM25 keyword) retrieval both run server-side inside Qdrant, fused via native Reciprocal Rank Fusion (RRF), then optionally reranked by Cohere's cross-encoder before being handed to the LLM.

1. The query is embedded twice: once as a 384-dim dense vector (MiniLM), once as a BM25 sparse vector (via `fastembed`'s `SparseTextEmbedding`).
2. Both go to Qdrant in a single `query_points()` call using `Prefetch` for each vector type (fetching 40 candidates each — `top_candidates * 2`) plus a `FusionQuery(fusion=Fusion.RRF)` that fuses the two candidate lists server-side into a single ranked list of 20.
3. A metadata filter restricts results to the requested `ticker` and `chunk_level == 2` (never search parent chunks directly), optionally + a section filter.
4. If a Cohere API key is set, the top 20 fused hits are reranked by Cohere's `rerank-english-v3.0` cross-encoder model down to the top 5 most relevant to the actual query text — a cross-encoder scores query+document pairs jointly (more accurate but slower than embedding similarity alone), which is why it's used as a second-stage refinement over a small candidate set rather than the primary search mechanism.
5. Each hit's level-1 parent is fetched by direct ID lookup (not another vector search).
6. Each hit is annotated with a staleness warning if its computed staleness score has dropped below 0.5.
7. The final assembled context (chunk + staleness note + truncated parent) is handed to the LLM for answer synthesis.

### Q53. Why RRF (Reciprocal Rank Fusion) specifically, instead of just averaging or weighting the two similarity scores?
**TL;DR:** Dense (cosine similarity) and sparse (BM25) scores live on completely different numeric scales and aren't directly comparable — a 0.82 cosine similarity and a BM25 score of 12.4 don't mean "similar amounts of relevance." RRF sidesteps this entirely by only using each result's *rank position* in its own list (1st, 2nd, 3rd...), combining them with the formula `1/(k + rank)` summed across lists — so it never needs the raw scores to be on comparable scales in the first place.

This is the standard, well-established solution to the "how do I fuse two differently-scaled ranked lists" problem, and Qdrant supports it natively server-side, so no custom fusion math needed to be hand-written and validated.

### Q54. What does the Cohere reranking step actually add on top of RRF, and what happens if it's unavailable?
**TL;DR:** RRF fusion is a purely rank-based combination of two *retrieval* signals (semantic similarity, keyword overlap) — neither actually reads the query and document together to judge relevance. Cohere's reranker is a cross-encoder: it takes the query and each candidate document *together* as input and directly scores how relevant that specific document is to that specific query, which tends to be meaningfully more accurate than similarity-based retrieval alone, especially for nuanced or multi-part questions.

If `COHERE_API_KEY` isn't set, or the Cohere API call fails for any reason, the code falls back to simply taking the first 5 of the already RRF-fused 20 hits — i.e., reranking is treated as a valuable-but-optional refinement, never a hard dependency. This is a good "degrade gracefully" pattern: the pipeline never breaks because a paid third-party API is unavailable, it just loses one layer of quality.

### Q55. What's the exact staleness-decay formula, and why floor it at 0.1 instead of letting it go to zero?
**TL;DR:** `score = max(0.1, 0.5 ** (age_days / half_life_days))` — classic exponential half-life decay (at one half-life, score = 0.5; at two half-lives, score = 0.25; etc.), floored at 0.1 so a score is never *exactly* zero.

The floor matters because this score isn't used to filter out old chunks entirely (staleness is a prompt annotation, not a retrieval filter — see Q56) — a genuinely relevant but old chunk should still surface with a clear warning, not vanish from context altogether. A hard floor at 0.1 rather than letting the score asymptote arbitrarily close to zero also avoids any downstream code accidentally treating a near-zero float as falsy/absent.

### Q56. Does the staleness score actually affect what gets retrieved, or just how it's labeled?
**TL;DR:** Purely a labeling/annotation mechanism — it never touches retrieval ranking, RRF fusion, Cohere reranking order, or any filter. A stale chunk (even one past several half-lives) can still be the #1 ranked hit; it just gets an inline warning string appended to its content before being handed to the LLM: `" [⚠️ data may be stale — {age}d old, half-life {half_life}d]"`.

This is a deliberate, honest scoping decision worth understanding: making staleness *change retrieval ranking* would be a bigger, riskier change (you'd need to decide how much to penalize old-but-highly-relevant content vs fresh-but-marginally-relevant content — a genuinely hard tradeoff). Making it a *prompt-level warning* instead punts that judgment call to the LLM itself (which is told, in the writer's system prompt, to prefer more recent sources when data conflicts) — a simpler, lower-risk implementation that still achieves the main goal: the LLM is never fooled into treating stale data as current without at least being told.

### Q57. What's the `info_type` classification and half-life table, and how was it derived?
**TL;DR:** Every level-2 chunk is tagged with an `info_type` based on which filing section it came from, each with a different assumed "shelf life": risk factors and business-model sections get a 365-day half-life (these change slowly), financials and guidance/MD&A sections get a 90-day half-life (quarterly cadence — these go stale fast), and anything unclassified defaults to 180 days.

This reflects a real intuition about how SEC filings age: a company's stated risk factors ("we face competition from X") don't meaningfully change quarter to quarter, but a specific revenue number is only "current" until the next earnings report roughly 90 days later. Tagging chunks this way at index time (rather than computing it generically at query time from just the filing date) lets different *sections of the same document* decay at different rates, which a single filing-level "freshness" score couldn't express.

### Q58. Is Indian stock RAG coverage actually as good as US stock coverage? Be specific.
**TL;DR:** No, and this is a known, tracked gap. US tickers get 180–290 Qdrant chunks each (full 10-K filings via SEC EDGAR); Indian tickers historically got only ~8 chunks (from yfinance's thin `.info` company description, not a real filing). The BSE annual report downloader (`rag/bse_indexer.py`) exists specifically to close this gap, targeting a hardcoded list of 29 major Indian companies (documentation says "30," the actual dict has 29 — a real off-by-one discrepancy).

Concrete numbers from indexing runs: NVDA≈287 chunks, GOOGL≈260, MSFT≈252, TSLA≈282, AMZN≈184, AAPL≈181 — vs. Indian stocks starting around 8 before the BSE indexer runs. This gap was deliberately *not* hidden in the RAGAS evaluation — the eval questions intentionally include Indian tickers specifically to surface the expected `context_recall` gap rather than cherry-picking only US tickers that would score well.

### Q59. What are the known failure modes of the BSE indexer specifically?
**TL;DR:** Several: (1) the PDF download URL is a *guessed* string template with no discovery step, so it breaks silently if BSE changes their file-naming convention; (2) the NSE fallback path, when it *does* find a PDF link, has an explicit `# TODO: download and index NSE PDF` and just returns 0 — it finds the file but never actually downloads/indexes it; (3) the temp file path is hardcoded to POSIX `/tmp/`, which doesn't exist on Windows; (4) the synthetic filing date (`{year}-04-01`, approximating the start of the Indian fiscal year) isn't the real publication date, which will skew staleness-score calculations for those chunks; (5) the scrip-ID map needs manual annual re-verification per its own code comment — there's no dynamic lookup against BSE's live scrip list.

This is a good "known limitations, tracked and documented" example — none of these are hidden; they're either explicit TODOs in the code or explicit comments flagging the maintenance burden.

### Q60. Explain the `user_analyses` Qdrant collection — what's stored, and is it actually ever read back?
**TL;DR:** It stores a single 384-dim dense vector (no sparse vector, unlike the main filing collection) per completed analysis, embedding `"{ticker} {verdict} {verdict_text}"`, with payload `user_id`, `ticker`, `verdict`, `risk_score`, `analysis_date`, and up to 400 characters of the memo's Final Verdict section. It's written at the end of `writer_node`. Based on the RAG-layer files alone, it appears **write-only** — none of `query_engine.py`, `indexer.py`, or the RAG scripts ever query it back; the read side (feeding `past_decisions_ctx` into the risk/writer prompts) lives elsewhere in the graph/API layer, not in the RAG module itself.

This collection is functionally very similar to what Mem0 already provides (both store a compressed summary of past decisions, keyed by user) — see Q65 for the direct comparison of why both exist.

### Q61. How is the "Final Verdict" section actually extracted from a completed memo for indexing — and what happens if the writer's formatting doesn't match?
**TL;DR:** Brittle marker-based string matching. `indexer.py`'s `index_user_analysis()` searches for any of three literal substrings — `"## Final Verdict"`, `"**Final Verdict"`, `"7. Final Verdict"` — takes 500 characters after whichever marker is found, then truncates to 400. If the writer LLM ever emits a different heading style (different casing, no numbering, a synonym like "Verdict" alone), none of the three markers match, and the code silently falls back to using the **entire memo**, truncated to 400 characters, as the "verdict text" — which would just be the Executive Summary, not the actual verdict, with no error raised anywhere.

This is a real, live fragility: the indexing pipeline has an implicit contract with the writer's output formatting that isn't enforced by any schema or validator — a prompt-engineering change to the writer's section headings could silently corrupt what gets stored as "the verdict" without any test catching it (since nothing currently asserts on `user_analyses` payload correctness).

### Q62. Two different modules both have code that can create the `user_analyses` Qdrant collection — why is that a problem?
**TL;DR:** `scripts/init_qdrant.py` (the intended, deliberate setup script) creates the collection *with* payload indexes on `user_id`/`ticker`/`verdict`. But `rag/indexer.py`'s `index_user_analysis()` also has inline logic to create the collection lazily if it doesn't exist yet — and that inline path does **not** add those indexes. Whichever code path happens to run first "wins" — if the first-ever write to this collection happens via a live pipeline run (before anyone's manually run `init_qdrant.py`), the collection permanently lacks payload indexes unless someone notices and adds them later.

This is a classic "two independent code paths that should be one shared source of truth" problem — not a functional bug per se (queries still work without indexes, just slower and without server-side filter optimization), but exactly the kind of config-drift risk that's easy to introduce and easy to miss in review, since both code paths individually look correct in isolation.

### Q63. What's the actual retrieval mechanism when the Query Engine runs in "ReAct agent" mode (`query()`) vs. "one-shot" mode (`search()`)?
**TL;DR:** `search()` is a single hybrid-retrieval-then-synthesize call — used by the Data/Research agents for a direct question. `query()` is a hand-rolled ReAct loop (up to 4 steps) where the LLM can choose, per step, between two tools — `financial_facts_sql` (structured numbers from Postgres) and `hybrid_search` (the vector RAG pipeline) — deciding for itself which data source best answers a sub-question, before producing a `FINAL ANSWER:`.

The tool dispatch in `query()` parses the LLM's raw text output for lines starting with `ACTION:`/`INPUT:` rather than using a structured function-calling API — a fragile, "hope the LLM follows the format" approach rather than a schema-enforced tool call. This is a real design tradeoff worth naming: it's simpler to implement (works with any LLM that can follow instructions, no function-calling API dependency) but strictly less robust than modern structured tool-calling, and if the LLM wraps its response in markdown or varies whitespace, the step is wasted on a parse failure.

### Q64. Why does `populate_facts.py` label all extracted financial figures as `"millions_usd"` even for Indian tickers — is that a bug?
**TL;DR:** Yes, a genuine correctness bug. yfinance reports Indian tickers' (e.g. `TCS.NS`) financials in INR, not USD, but the ingestion script hardcodes the unit label `"millions_usd"` regardless of ticker — so any downstream SQL-tool consumer (the ReAct agent's `financial_facts_sql` tool) reading a Postgres row for an Indian company would see a number that's actually in millions of rupees mislabeled as millions of dollars, a large magnitude error if naively compared against a USD figure.

This is exactly the kind of bug that's easy to miss because each individual piece (the extraction, the storage, the labeling) works "correctly" in isolation — the bug only exists at the intersection of "this pipeline was originally built US-first" and "Indian stock support was added later without revisiting every hardcoded assumption."

---

## Section F: Memory Systems — Mem0 vs Qdrant vs DSPy

### Q65. This is the big one: what is the use of DSPy + Mem0 together — aren't they both "memory"?
**TL;DR:** No — they solve completely different problems despite both sounding like "AI memory" at a glance. **DSPy is a compile-time prompt optimizer** (it improves *how the Writer Agent is instructed*, once, ahead of time, using a graded training set). **Mem0 is a runtime cross-session memory store** (it recalls *what this specific user did in past sessions*, fresh, on every single request). One shapes the prompt template; the other fills in personalized content at request time.

Concretely: DSPy's `BootstrapFewShot` optimizer runs *once, offline* (via `eval/dspy_optimizer.py`), producing a compiled artifact (`eval/compiled_writer.json`) containing a handful of graded few-shot examples baked into the Writer Agent's prompt. This compiled prompt is the same for every user, every request — it makes the *writing quality* better in general (better section structure, better citation habits) by having learned from examples of good memos. Mem0, by contrast, runs on *every single pipeline execution*: `read_memory(user_id)` at the start of `finance_node` pulls a natural-language summary of that specific user's last several analyses ("you tend to be conservative, avoided 3 of 4 high-risk stocks"), and `write_memory(user_id, state)` at the end of `writer_node` records this session's outcome for next time. If you swapped users, the DSPy-compiled prompt stays identical; the Mem0-retrieved context changes completely. They're not redundant — they're orthogonal layers: DSPy makes the *writer smarter in general*, Mem0 makes the *output personal to this user*.

### Q66. If Mem0 already provides cross-session memory, why does the project *also* have a Qdrant `user_analyses` collection storing similar-looking data?
**TL;DR:** They're functionally overlapping in what they store (both hold a compressed record of past verdicts per user) but different in *how* they're meant to be retrieved. Mem0 does its own internal LLM-based fact extraction and semantic retrieval opaquely — you send it a natural-language "fake conversation" and a generic search query, and it returns whatever it decides is relevant. `user_analyses` in Qdrant is meant to support precise, filterable, ticker/sector-scoped semantic search ("find my past decisions specifically about semiconductor stocks") using an embedding you control directly, with structured payload filters (`user_id`, `ticker`, `verdict`) that Mem0's opaque API doesn't expose in the same way.

In practice — per the current code — this potential is only half-realized: `user_analyses` is written on every run but (based on the RAG-layer files) never actually read back anywhere visible, making it currently redundant with Mem0 in effect if not in intent. The design doc explicitly describes the *intended* use (retrieve the 3 most semantically similar past verdicts before a new risk analysis) as a later-phase item — so this is "designed, partially built, not yet wired end-to-end" rather than a mistake.

### Q67. Walk through exactly what Mem0 stores and how, mechanically.
**TL;DR:** Mem0 is not fed a raw dump of pipeline state — `write_memory()` constructs a *synthetic two-turn conversation* (a fabricated user question + assistant answer summarizing the analysis) and sends that to `client.add(messages, user_id=user_id)`. Mem0's own internal LLM then extracts and deduplicates facts from that conversation the same way it would from a real chat log.

This matters because it explains *why* the code goes through the trouble of building a fake conversation rather than just storing a JSON blob: Mem0's product is built around conversational fact extraction — feeding it structured data directly would bypass its actual value-add (deciding what's worth remembering, deduplicating against prior memories, extracting facts in natural language it can later retrieve semantically).

### Q68. What's the exact query Mem0 is searched with at the start of a pipeline run, and why is that itself a limitation?
**TL;DR:** A single hardcoded string: `"financial analysis investment risk portfolio"` — not the user's actual current question or ticker. This means Mem0 retrieval is topic-anchored/generic ("give me anything relevant to finance in general for this user") rather than query-specific ("give me anything relevant to this user's history with semiconductor stocks specifically").

This is a real, acknowledged design limitation, not a bug per se — it works because Mem0's `limit=10` result set is broad enough to often contain something useful, but a smarter implementation would parameterize the search query with the actual ticker/sector/question being asked, likely surfacing more relevant memories for users with a long history across many different sectors.

### Q69. What happens, concretely, if the Mem0 API is down when a request comes in?
**TL;DR:** `read_memory()` catches any exception (auth failure, timeout, service outage) and returns an empty string — the pipeline proceeds exactly as if the user were brand new, with zero personalization context, no error surfaced to the end user. `write_memory()` at the end similarly swallows failures silently — that session's summary is simply never recorded, with no retry queue or dead-letter mechanism, so it's permanently lost if Mem0 happened to be down at that exact moment.

This "fail open, degrade silently" behavior is explicitly commented in the code ("Memory failure should never block the pipeline") — a deliberate prioritization of pipeline availability over memory completeness, appropriate for a feature (personalization) that enhances but isn't essential to producing a valid memo.

### Q70. Does DSPy's compiled prompt get automatically re-validated if the underlying signature (input/output fields) changes?
**TL;DR:** No — this is a documented, unmitigated risk. If someone edits the `WriteMemo` signature's fields (adds/removes an input, changes an output field name) without recompiling, `eval/compiled_writer.json` on disk still reflects the *old* signature's few-shot examples, and nothing checks for a mismatch at load time — the compiled prompt would silently be used with stale example formatting.

The documented fix (not yet implemented) is to store a hash of the signature alongside the compiled artifact and compare it on load, falling back to the hand-written baseline prompt if they don't match — a form of schema-versioning for a prompt artifact, analogous to how you'd version a serialized ML model against its expected feature schema.

---

## Section G: DSPy Prompt Optimization

### Q71. What exact DSPy optimizer is used, and why that one over more advanced options like MIPRO?
**TL;DR:** `dspy.BootstrapFewShot` — the simplest of DSPy's optimizers. It runs the base program over a training set, keeps only the examples where the output passes a defined metric, and folds up to 3 bootstrapped + 3 labeled demonstrations directly into the compiled prompt as in-context few-shot examples. No prompt-instruction rewriting (that's what MIPRO/COPRO do), just example curation.

Given the free-tier Groq rate limits this project operates under, `BootstrapFewShot` is the pragmatic choice — it requires far fewer LLM calls to compile than MIPRO (which searches over both instructions and examples, needing many more evaluation rounds), and for a task like "write a structured financial memo," good examples of the *format and citation style* likely matter more than clever instruction phrasing — few-shot examples are a strong lever here.

### Q72. What metric does DSPy optimize against, and what does it deliberately *not* check?
**TL;DR:** `memo_quality_metric()` is a pure structural check: does the output contain all 7 required section headings (proportional partial credit), plus a small bonus if a verdict word (BUY/HOLD/AVOID) appears in the first 200 characters. Pass bar: score ≥ 0.85, i.e., roughly 6 of 7 sections plus the verdict bonus, or all 7 without it.

It deliberately does **not** check faithfulness, factual correctness, or whether the reasoning is any good — it's fast and free (pure string containment, no LLM judge call needed during compilation) specifically because compilation needs to run this metric on every candidate example in the training set, and paying for an LLM-judge call per candidate would be far more expensive and slow. Correctness/faithfulness checking is left entirely to the separate evaluation layer (DeepEval, RAGAS, etc.) that runs *after* compilation, on the compiled program's actual output — a clean separation between "does this look like a valid memo" (compile-time, cheap, structural) and "is this memo actually good" (post-hoc, expensive, semantic).

### Q73. Why does DSPy compilation use `llama-3.1-8b-instant` instead of the same 70B model used everywhere else (including the eval judges)?
**TL;DR:** Purely a rate-limit/cost tradeoff, made explicit in a code comment: the 8B model has a 30K tokens-per-minute free-tier limit on Groq vs. 12K TPM for the 70B model, and compilation needs many calls across the training set — "good enough for optimization, the compiled few-shot examples are what matter, not the model used during compilation."

This is a genuinely interesting and non-obvious insight: the model quality used *during compilation* doesn't need to match the model quality used *at inference time*, because what compilation produces is a set of concrete example text (the bootstrapped demos), not model weights. A smaller/faster/cheaper model can generate perfectly good training-time samples, as long as the *metric* filtering those samples for inclusion is trustworthy.

### Q74. Where does the golden dataset for DSPy training come from, and how is it split?
**TL;DR:** `eval/writer_golden_dataset.json`, 28 hand-curated examples spanning a mix of US tickers (AAPL, MSFT, NVDA, TSLA, AMZN, GOOGL, META, JPM, XOM, NFLX, AMD, PLTR, WMT, VT), Indian tickers (RELIANCE.NS, INFY, TCS.NS, HDFCBANK.NS, WIPRO.NS), and personal-finance scenarios (debt payoff planning, ELSS/80C tax-saving, NPS/PPF, crypto allocation, index funds). Split: first 10 → training set (used for bootstrapping demos), remaining 18 → dev set (used to evaluate the compiled program's pass rate before shipping it).

The dataset's own comments/docstrings elsewhere in the codebase reference "15 entries" — that's stale; the dataset has grown to 28 since those comments were written, and nothing broke because the code iterates the real list length rather than a hardcoded count — a good example of "the comment rotted, the code didn't."

### Q75. Concretely, what does the compiled program actually contain differently from the hand-written baseline prompt?
**TL;DR:** The hand-written baseline (`eval/evaluate.py`'s `_generate_baseline`) is a static f-string with instructions but zero examples. The compiled artifact (`eval/compiled_writer.json`, ~23KB on disk) embeds up to 3 concrete, real, metric-passing example memos directly into the prompt sent at inference time — the model sees "here's what a well-structured memo for scenario X actually looks like" rather than only being told "write a well-structured memo."

This is the core DSPy value proposition made concrete: few-shot in-context examples usually improve format adherence and style consistency more reliably than instruction text alone, especially for tasks with a rigid structural contract (7 sections, in order, with specific citation habits) — showing beats telling.

### Q76. Why does the DSPy optimizer script save the compiled program to disk *before* running dev-set evaluation, rather than after?
**TL;DR:** An explicit defensive-ordering choice, with a comment stating the reason directly: "so a rate limit crash doesn't lose the compiled program." Compilation itself consumes API calls and can legitimately fail partway through evaluation (Groq's free-tier rate limits are real and get hit during heavy testing) — if the script crashed on a rate-limit error during the *evaluation* phase, you'd lose the successfully-compiled program along with it unless it was already persisted.

This is a small but genuinely good piece of engineering judgment — recognizing that two logically separate steps (compile, then evaluate) have very different failure-cost profiles, and sequencing the durable write before the riskier step, rather than treating "compile and evaluate" as one atomic operation that either fully succeeds or loses everything.

---

## Section H: Evaluation & Testing Methodology

### Q77. How many genuinely distinct evaluation "layers" does this project have, and what does each one check?
**TL;DR:** Five conceptually distinct layers, each answering a different question: (1) **RAGAS** — is the RAG *retrieval* pipeline itself good (precision/recall of what got retrieved, independent of the final memo)? (2) **DeepEval** — is the final *written memo* faithful, non-hallucinatory, relevant, and well-grounded in its retrieved context? (3) **LLM-as-judge (`evaluate.py`)** — a second, structured-output judging layer scoring correctness/groundedness/relevance/structure, plus an explicit baseline-vs-DSPy-compiled A/B comparison. (4) **Reliability (`reliability_eval.py`)** — is the system *consistent* across repeated runs of the identical question (verdict-flip rate + content-drift via BERTScore)? (5) **Promptfoo** — an external, YAML-driven eval CLI that exercises the live deployed HTTP API end-to-end, distinct from every other layer that tests in-process.

Having this many layers is deliberate stratification, not redundancy — each targets a genuinely different failure mode. A memo could pass DeepEval's faithfulness check (it doesn't hallucinate facts) while still failing RAGAS (the underlying retrieval that fed it was actually low-quality/low-recall) or failing reliability (the same question gets a different verdict on a re-run).

### Q78. Why grade the RAG pipeline (RAGAS) *separately* from grading the final memo (DeepEval) — isn't checking the memo's faithfulness enough?
**TL;DR:** No, because "the memo is faithful to what it retrieved" and "what it retrieved was actually good" are independent claims. A writer can be perfectly faithful to garbage context (never inventing facts, only ever citing what it was given) and still produce a bad memo if the retrieval step itself missed the genuinely relevant chunks. RAGAS isolates and measures *that* upstream failure mode specifically — context precision (are retrieved chunks actually relevant) and context recall (did retrieval find everything relevant that exists) — independent of anything the writer LLM does afterward.

This is exactly the kind of "test one behavior at a time" discipline you want in any pipeline with multiple stages that could each independently fail — without RAGAS, a retrieval regression (say, a broken Qdrant filter that returns zero results) could easily hide behind a DeepEval faithfulness score that still looks fine, because "faithful to an empty context" trivially passes a naive faithfulness check.

### Q79. What was the "circular ground_truth bug" in RAGAS eval, precisely — and how do you even find something like that?
**TL;DR:** An early version of `ragas_eval.py` set the `ground_truth` field (the "correct answer" RAGAS compares retrieval/answers against) equal to the RAG pipeline's *own generated answer* — `"ground_truth": answer` — rather than an independently authored correct answer. Since `context_recall` and `answer_correctness` are both computed *relative to* ground truth, using the system's own output as its own reference guarantees those metrics trend toward a perfect score regardless of actual quality — the system literally could not fail its own recall/correctness check by construction.

This was found by reading the file's git history (`git log -p`) — the original commit's code comment even said the quiet part out loud: *"ground_truth is required by context_recall; we use the answer as proxy since we don't have human-annotated gold answers."* The fix (a later commit) replaced this with human-authored ground-truth strings per question (e.g., specific facts about NVDA's competition risks, MSFT's Azure growth drivers) — a real, independent reference the system's answer is actually judged against. This is a great example of a subtle-but-serious eval design bug: the code ran without error, produced numbers that *looked* like a real score, and the mistake was purely conceptual (measuring nothing meaningful) rather than a crash or exception — the kind of bug that only surfaces from understanding what the metric is supposed to mean, not from any test failing.

### Q80. What exact metrics and thresholds does DeepEval check, and what's notable about the threshold choices?
**TL;DR:** Nine metrics total, eight LLM-judged plus one purely deterministic:

| Metric | Threshold |
|---|---|
| Faithfulness | 0.75 |
| Hallucination | 0.80 |
| Answer Relevancy | 0.70 |
| Contextual Precision | 0.70 |
| Contextual Recall | 0.60 |
| Contextual Relevancy | 0.60 |
| Financial Verdict (GEval) | 0.70 |
| Task Completion (GEval) | 0.80 |
| Verdict Consistency (deterministic) | 1.0 |

Notable: Contextual Recall has the lowest threshold (0.60) of the LLM-judged metrics — an implicit acknowledgment that recall is the hardest dimension to score well on (did retrieval find *everything* relevant, including things that might be phrased very differently from the query) and that this pipeline's known thin-Indian-stock-coverage issue makes a stricter recall bar unrealistic to enforce uniformly. The one deterministic metric — Verdict Consistency — is graded at a strict 1.0 threshold specifically because it's not probabilistic: it's a pure Python rule check (risk_score ≥ 9 shouldn't produce BUY, risk_score ≤ 4 shouldn't produce AVOID), so there's no reason to tolerate any failure rate on it the way you would for an inherently-fuzzy LLM judgment.

### Q81. Explain the Task Completion GEval metric's exact scoring rubric.
**TL;DR:** 1.0 if all 7 required memo sections are present and substantive; 0.0 flat if the Final Verdict section specifically is missing (treated as a hard failure regardless of anything else); otherwise, deduct roughly 0.14 (1/7) per missing or empty non-verdict section. Threshold is 0.80, meaning one missing non-verdict section still passes (1.0 − 0.14 ≈ 0.86), but two missing sections fails (≈0.71).

The design choice to hard-fail specifically on a missing Final Verdict (rather than just deducting its 1/7 share like every other section) reflects that this one section is non-negotiable for the product to be useful at all — a memo with 6 great sections but no actual recommendation has failed at its core job, in a way that's qualitatively different from "one supporting section was thin."

### Q82. What is the exact rule the deterministic Verdict Consistency metric enforces, and why is there a documented discrepancy between the code and its own docstring?
**TL;DR:** The *executed* code (source of truth): BUY fails only if `risk_score >= 9`; AVOID fails only if `risk_score <= 4`. Everything else (including a BUY at risk_score 6, 7, or 8) passes. But the module's own docstring, and a separate test file's docstring, both describe a stricter cutoff (e.g. "BUY with risk_score >= 6 should fail") that doesn't match what the code actually checks.

The comments explain the *reasoning* behind the looser, actual rule pretty clearly: risk 5-8 doesn't automatically rule out BUY, because things like a contrarian dip-buy or a small high-conviction allocation can legitimately carry moderate/high risk while still being a reasonable BUY call — position sizing, not verdict avoidance, is how a sophisticated investor handles that risk level. This is a real, live example of comment/code drift worth internalizing: when two sources of truth disagree, the code that actually runs is authoritative, and a good engineer reads the implementation before trusting a docstring's summary of it.

### Q83. What does the "Reliability Eval" measure that no other eval layer catches, and how exactly does it work?
**TL;DR:** LLM non-determinism — running the *exact same* (ticker, question) pair repeatedly and checking whether the system gives a consistent answer. No other eval layer in this project re-runs the same input multiple times; every other layer scores single runs against a reference.

Two independent checks, both must pass: (1) **pass^k** — run the pipeline k=5 times, extract the verdict from each, and require that at least 80% (4 of 5) agree with whichever verdict was most common (the mode) — not agreement with a fixed "correct" answer, agreement with each other. (2) **BERTScore semantic similarity** — beyond just the headline verdict word, compare the *actual memo content* of runs 2 through 5 against run 1 (not all-pairs, just vs. the first run) using BERTScore's F1, requiring the *worst* (minimum) pairwise F1 across those comparisons to clear 0.85. The reasoning for needing both: pass^k alone would call two runs "consistent" even if the risk factors cited, the specific numbers mentioned, or the reasoning completely changed, as long as the headline BUY/HOLD/AVOID word matched — BERTScore catches that deeper content drift that a single categorical verdict comparison can't see.

### Q84. Why does the E2E test never actually run in CI — walk through the exact gating logic.
**TL;DR:** `tests/test_e2e.py` is skipped at *import time* (`pytest.skip(..., allow_module_level=True)`) if either `GROQ_API_KEY` is unset **or** the `SKIP_E2E` env var is set — and the project's `ci.yml` workflow explicitly sets `SKIP_E2E: "1"`, guaranteeing this file is always skipped in CI regardless of whether a Groq key is available there.

This means the full pipeline end-to-end test (a real AAPL run through all 8 nodes, asserting memo length, required sections, valid risk score, verdict presence) is a *local-only, developer-run* check — it exists and is genuinely useful for verifying nothing broke before a manual push, but it provides zero automated protection against a regression that only shows up in CI. This is a real, honest gap worth naming directly rather than glossing over: "we have an E2E test" is a materially different (weaker) claim than "we have an E2E test that runs on every PR."

### Q85. What other evaluation scripts exist but are similarly never wired into any automated CI pipeline?
**TL;DR:** `eval/ragas_eval.py`, `eval/reliability_eval.py`, `eval/dspy_optimizer.py`, `eval/evaluate.py`, and `eval/promptfoo_provider.py` — none of these five are referenced by any of the three GitHub Actions workflow files (`ci.yml`, `eval.yml`, `deploy.yml`). Each is a fully-built CLI tool with its own `argparse` entry point and detailed docstring, but all five exist purely for manual/local execution.

Compounding this, `tests/test_rag_pipeline.py`'s "Layer 2" (7 RAGAS threshold assertions) is conditionally skipped at collection time because it depends on a results file (`eval/results/ragas_*.json`) that only gets produced by manually running `ragas_eval.py` first — and that file doesn't currently exist in the working tree, so those tests are presently skipped even locally, not just in CI. And `ci.yml` also explicitly `--ignore`s `tests/test_e2e.py` and `tests/test_rag_pipeline.py` entirely (both files, including the cheap Layer-1 RAG sanity checks that don't need an LLM judge and could plausibly run cheaply in CI). Net effect: the only tests that genuinely execute automatically on every PR are the deterministic parts of `tests/test_deepeval.py` (specifically `test_verdict_consistency`, which needs no API key) — everything else is a real, working eval suite that simply isn't yet load-bearing for CI gating.

### Q86. Is the DeepEval CI gate (`eval.yml`) actually functional? Trace through exactly why or why not.
**TL;DR:** No — it would fail immediately if triggered, for two independent reasons. First, it calls `python eval/run_deepeval.py --limit 5`, but no file named `eval/run_deepeval.py` exists anywhere in the repository (only `eval/deepeval_metrics.py` and `tests/test_deepeval.py` exist, neither of which is that script). Second, even setting that aside, the workflow installs `langchain-google-genai` and sets `GEMINI_API_KEY`/`GOOGLE_API_KEY` — implying it expects a Gemini-based LLM judge — but the actual `GroqJudge` class implemented in `deepeval_metrics.py` only reads `GROQ_API_KEY` and has zero references to Gemini anywhere; the credentials this workflow provisions don't match what the (nonexistent) script it's calling would actually need.

This is a genuinely useful thing to be able to explain clearly in an interview: it demonstrates you can trace a CI config through to what it *actually* calls and verify the claim ("we have a DeepEval CI gate") against ground truth, rather than taking a workflow file's *existence* as proof it works. The honest fix is well-scoped (write the missing entry-point script, and either implement a Gemini judge to match the workflow's provisioned credentials or update the workflow to install Groq credentials instead) — it just hasn't been prioritized.

### Q87. What exactly does the `test_verdict_consistency` test check, and why is it the *only* unconditional test in the DeepEval test file?
**TL;DR:** It runs the deterministic `VerdictConsistencyMetric` (see Q82) against **every** entry in the 28-item golden dataset (not just a 3-example sample like the other 8 LLM-judged tests), collecting *all* failures into a list and asserting the list is empty at the very end — giving one comprehensive failure report rather than stopping at the first bad entry.

It has no `skipif(not _HAS_KEY)` guard because it needs no LLM call at all — it's pure Python logic parsing pre-written memo text with a regex and checking a numeric rule. That's precisely why it's the one test that survives running in CI even without a `GROQ_API_KEY` configured there — every other DeepEval test in that file is gated behind an API-key check and silently skips in the current CI setup.

### Q88. What's the actual mechanism `test_rag_pipeline.py`'s Layer 1 sanity tests check, and why are they valuable even without an LLM judge?
**TL;DR:** 10 parametrized (question, ticker) pairs across AAPL/MSFT/GOOGL check five properties with zero LLM-judge involvement: retrieval returns *something* non-empty, retrieval returns at least 1 chunk, every returned chunk has non-empty content, ticker filtering is actually respected (no cross-ticker leakage — e.g. an AAPL-filtered query never returns a Google chunk), and querying a nonsense ticker (`"FAKE_XYZ_999"`) returns an empty list gracefully rather than raising an exception.

These are valuable specifically *because* they're cheap and fast (real embedding + Qdrant calls, but no LLM judge call) — they catch entire classes of pipeline breakage (a broken Qdrant connection, a broken filter, an indexing regression that wipes content) that would otherwise only surface indirectly through a degraded DeepEval faithfulness score, at far higher cost and latency than these direct checks. The fact that `ci.yml` currently excludes this whole file despite these specific tests being cheap enough to plausibly run on every PR is one of the clearer "quick win, not yet taken" opportunities in the project's CI setup.

### Q89. How does `evaluate.py`'s LLM-as-judge layer differ structurally from DeepEval's approach, beyond just using different metric names?
**TL;DR:** `evaluate.py` uses `ChatGroq(...).with_structured_output(schema, method="json_schema")` for all 4 of its metrics — meaning the judge's output is *type-enforced* directly by the LLM provider's structured-output feature (a `TypedDict` schema), with zero manual JSON parsing or regex fallback needed. Every schema deliberately puts the `explanation` field *before* the boolean pass/fail field, forcing the model to articulate its reasoning before committing to a verdict — a form of prompted chain-of-thought embedded directly into the output schema's field order, rather than relying on separate "think step by step" prompt instructions.

This is architecturally cleaner than DeepEval's approach in one specific way (no parsing fragility — structured output either validates against the schema or the call fails clearly) but is also a second, mostly-independent reimplementation of "grade this memo with an LLM" logic living in a separate file from DeepEval's metrics — a case where genuine engineering value (comparing two independent judging approaches) and duplication cost (two codebases doing conceptually similar work) both exist simultaneously.

### Q90. What's the actual A/B comparison `evaluate.py`'s `run_compare()` does between the baseline and DSPy-compiled writer, and why is that comparison the real payoff of building DSPy at all?
**TL;DR:** For each of 5 held-out golden-dataset examples, it generates a memo with the raw hand-written baseline prompt *and* a memo with the DSPy-compiled program, scores both with all 4 structured-output graders, computes a pass rate (fraction of 4 metrics passed) for each side, and prints the per-ticker delta plus an averaged percentage improvement.

This is the concrete, measurable answer to "did DSPy compilation actually help, or did we just add complexity for nothing" — without this comparison script, you'd be trusting DSPy's value on faith; with it, you get an actual before/after number on the same held-out test set, graded by the same judge, which is the only way to responsibly justify keeping a compiled-prompt system in the pipeline over just hand-tuning the prompt directly.

### Q91. Why does `promptfoo_provider.py` exist as a *separate* eval mechanism when the project already has E2E tests and DeepEval — what does it uniquely cover?
**TL;DR:** It's the only evaluation layer in the entire project that exercises the **live, deployed HTTP API** (`POST /analyze` over the network) rather than calling in-process Python functions directly. Every other eval script (DeepEval, RAGAS, reliability, DSPy) invokes the graph or query engine as a Python import within the same process — none of them test that the FastAPI server is actually up, correctly routing, correctly authenticating, and correctly serializing responses over HTTP.

Concretely, `call_api()` distinguishes `ConnectionError`, `Timeout`, and `HTTPError` into separate structured outputs (rather than one generic catch-all), which is specifically useful for a deployed-service health check: Promptfoo's report can tell you "the server is down" vs. "the server is up but too slow" vs. "the server returned an error status" — three genuinely different operational problems that an in-process test could never distinguish, because in-process tests don't go over a network boundary at all.

### Q92. Given how many eval layers exist but aren't wired into CI, what would you actually prioritize fixing first, and why?
**TL;DR:** Fix `eval.yml`'s DeepEval CI gate first (it's broken in two independently fixable ways — missing script, mismatched judge credentials — and is the one workflow explicitly *intended* to be a merge-blocking quality gate), then promote `tests/test_rag_pipeline.py`'s Layer 1 sanity tests into `ci.yml` (they're cheap, fast, and catch real regression classes, and the only reason they're excluded appears to be that they were bundled in the same `--ignore` decision as the genuinely-expensive E2E test rather than evaluated on their own cost/benefit).

The reasoning for that order: a broken *intended* gate is worse than a missing optional check, because it creates false confidence — anyone glancing at the workflow list sees "Eval Gate" and assumes quality is being enforced on every PR, when in fact it would crash on its first invocation. Fixing the cheap RAG sanity tests second is the best cost-per-fix ratio available: minimal work (just remove them from the ignore list, since the tests themselves already work), meaningful regression coverage, no new infrastructure needed.

---

## Section I: Observability

### Q93. Is LangSmith tracing actually active, or is it another example of "defined but never called"?
**TL;DR:** Genuinely active, unlike some other pieces in this project. `verify_langsmith()` is called at API startup inside the `lifespan` context manager, and `trace_node()` (a decorator factory meant to wrap LangGraph node functions) is a real, functional no-op-when-disabled wrapper — it only activates `@traceable` instrumentation if `LANGCHAIN_API_KEY` is set, otherwise it calls the wrapped function directly with zero overhead. This graceful-degradation design means the decorator is always safe to leave in code regardless of whether tracing is configured in a given environment.

### Q94. How is PII actually masked in LangSmith traces, and how strong is that masking really?
**TL;DR:** `user_id[:8] + "****"` — keep the first 8 characters, append a literal 4-asterisk mask (or leave unmasked entirely if the ID is 8 characters or shorter). Since `user_id`s in this system are UUIDs (36 characters), this reveals the first UUID segment (e.g. `00000000`) unmasked, appends `****`, and drops the remaining ~28 characters.

Honestly assessed, this is a **truncation mask, not a cryptographic one** — it's not a hash or HMAC, so it provides no protection if you actually needed the full UUID to be unrecoverable (e.g. under a strict privacy requirement); it's more of a "don't show the whole ID in a trace dashboard at a glance" convenience than real PII protection. It also only masks `user_id` specifically — other potentially-identifying fields like `tickers` and `input_source` are logged into trace metadata unmasked.

### Q95. Is W&B Weave actually initialized and logging real data from live user requests, or is it eval-only?
**TL;DR:** Nuanced answer: `init_weave()` genuinely *is* called at API startup (confirmed in `api/main.py`'s `lifespan`) — so a Weave session does get initialized when the API boots. But the file's own docstring explicitly states "Pipeline tracing is handled by LangSmith. This file owns eval scoring only," and neither `score_memo()` nor `log_eval_result()` (the two functions that actually log data to Weave/W&B) are called anywhere in the live `/analyze` request path — they're only invoked from the offline eval harness (`eval_runner.py`).

So the precise, correct claim is: Weave *initializes* on every API boot, but nothing about a live user's `/analyze` call ever logs data to it — the initialization is essentially inert from the perspective of production traffic, only becoming useful when someone separately runs the offline eval scripts. This is a subtle but important distinction from "Weave isn't wired up at all" — it's *half*-wired: the session exists, but nothing from live traffic feeds it.

### Q96. There's a comment claiming `score_memo()` is traced "via `init_weave()`" — is that actually true?
**TL;DR:** No — checked directly against the code, `score_memo()` has no `@weave.op()` decorator anywhere in the file, so despite what the comment claims, nothing about that function is actually instrumented for Weave tracing. Only the explicit `wandb.log()` calls inside the separate `log_eval_result()` function actually reach W&B. This is another example of a comment describing intended/aspirational behavior that doesn't match what the code does — worth explicitly verifying rather than trusting.

### Q97. Why does the project use *two* separate observability platforms (LangSmith and W&B Weave) instead of just one?
**TL;DR:** They're used for genuinely different purposes, per the codebase's own stated intent: LangSmith owns **pipeline tracing** (what happened during a live request — which nodes ran, in what order, with what inputs/outputs/latency/cost), while Weave owns **eval scoring** (offline quality measurement — is this memo good, scored 1-5 across structure/accuracy/personalization/actionability dimensions, tracked across eval runs over time).

This is a defensible separation of concerns even though it means two vendor integrations to maintain — LangSmith's product is built around per-request execution tracing, while Weave's product is built around eval-run tracking and comparison over time; using each for what it's actually good at, rather than forcing one tool to do both jobs, is a reasonable call, though it does add operational surface area (two API keys, two dashboards, two things that can silently fail).

### Q98. What's the actual scoring rubric `score_memo()` uses, and what model grades it?
**TL;DR:** An LLM-as-judge call to Groq (`llama-3.3-70b-versatile`, `temperature=0.0` for reproducibility, `max_tokens=100`) scores four dimensions — structure, accuracy, personalization, actionability — each on a 1-5 scale, summed into a `total` out of 20. The memo is truncated to its first 3000 characters before being shown to the judge (to keep the judge prompt within a reasonable token budget). On any failure (missing API key, judge response that doesn't parse as valid JSON, any exception), it returns an all-zero score dict rather than raising — consistent with this codebase's general "never let an observability/eval failure break anything downstream" philosophy.

### Q99. If you had to add one new piece of observability that's currently missing, what would it be and why?
**TL;DR:** A structured, queryable log of exactly which failure/degradation paths fired on each request — right now, degraded runs (Mem0 down, E2B unavailable, Cohere reranking skipped, RAG returned empty context) are handled by silent fallback almost everywhere, which is great for availability but means there's no single place to answer "how often is this pipeline actually running in a degraded mode, and which dependency is flakiest." A lightweight structured event (e.g. `{"node": "risk_node", "degradation": "cohere_rerank_skipped", "reason": "no_api_key"}`) emitted alongside the existing `messages` log, aggregated over time, would turn "the system silently tolerates failures" into "the team can see exactly how often, and act on the worst offenders" — currently that visibility gap is real.

---

## Section J: Security, Auth & Rate Limiting

### Q100. Describe the actual authentication model end to end, and be specific about where it's weak.
**TL;DR:** Two separate, weak layers, neither a real session/token scheme. (1) User login (`/auth/login`) verifies a bcrypt password hash and returns `user_id`/`username` in a plain JSON body — no JWT or signed session token is issued at all. The Streamlit frontend then stores those two raw values as unsigned browser cookies for 30 days. Anyone who can set a cookie value (trivial in a browser dev console) can "log in" as any `user_id` string, because no backend endpoint ever verifies a cookie or token against anything — every endpoint just trusts whatever `user_id` is handed to it in the URL path or POST body. (2) A separate, optional API-key check (`X-API-Key` header vs. `WEALTHOS_API_KEY` env var) is applied *only* to `/analyze` and `/analyze/stream` — and if that env var isn't set, the check is a silent no-op, meaning API-key auth is effectively **off by default**.

Every other endpoint (`/history/{user_id}`, `/portfolio/{user_id}`, `/memory/{user_id}`, `/user-profile/{user_id}`, `/user-analyses/{user_id}`) has zero auth dependency at all — they're fully open to anyone who can guess or enumerate a `user_id`. This is squarely "demo-grade auth" — fine to demonstrate the *feature surface* of a personalized system, not remotely appropriate to expose with real user data.

### Q101. What's the exact rate-limiting mechanism, and what are its real-world limitations?
**TL;DR:** An in-memory sliding-window counter, keyed by `user_id`, default 10 requests per 60-second window (`ANALYZE_RATE_LIMIT` env var, hardcoded 60s window), applied *only* to `POST /analyze` — returning HTTP 429 when exceeded.

Three concrete limitations worth naming: (1) it's **in-memory**, not Redis-backed or otherwise shared, so it resets on every process restart and provides zero protection if the API is ever run as multiple replicas behind a load balancer — each replica would have its own independent counter, silently multiplying the effective limit by the replica count; (2) `/analyze/stream` — the streaming variant of the exact same expensive operation — has **no rate limit at all**, a straightforward bypass for anyone hitting the limit on `/analyze`; (3) it's keyed by client-supplied `user_id`, which (per Q100) is unauthenticated — an attacker can trivially rotate through fake `user_id` values to sidestep the limit entirely, since nothing verifies the `user_id` belongs to whoever is making the request.

### Q102. What does the prompt-injection guard actually catch, and how would you defeat it?
**TL;DR:** `_sanitize_query()` regex-replaces four specific phrase patterns (case-insensitive) with `[filtered]`: variations of "ignore previous/prior/above instructions," "you are now a/an...," "disregard previous...," and "forget previous...". It's applied only to the free-text `query` field, only on `/analyze` and `/analyze/stream`.

This is a narrow denylist, and denylists for prompt injection are well-known to be trivially bypassable: rewording ("please set aside your earlier guidance and instead..."), any non-English phrasing, unicode homoglyphs, splitting the trigger phrase across multiple sentences/lines, or simply not using any of those four specific patterns at all (there are effectively infinite ways to phrase an injection attempt) would all sail through unfiltered. It's genuinely better than nothing (it blocks the most common, laziest injection attempts you'd see from casual poking), but it should never be represented as robust protection — a proper mitigation would combine this with output-side guardrails (already partially present via `guardrails/validators.py`) and, ideally, structural prompt design that keeps user input clearly delineated from system instructions so the model is less susceptible regardless of phrasing.

### Q103. Are the seeded demo accounts (`admin/wealthos123`, `demo/demo123`) a real security problem?
**TL;DR:** Yes, if this were ever deployed with real user data attached — they're bcrypt-hashed and seeded into the `users` table on *every* API startup (`ON CONFLICT DO NOTHING`, so they persist forever once created), meaning any deployment of this exact code, anywhere, ships with two publicly-known working credentials by default unless someone explicitly removes that seeding logic before a real deployment.

For a portfolio/demo project, this is a completely reasonable and even helpful choice (recruiters/reviewers can log in immediately without you needing to share credentials separately) — but it's exactly the kind of thing that needs a very clear, loud "REMOVE BEFORE REAL DEPLOYMENT" flag, and ideally should be gated behind an explicit `DEMO_MODE=true` env var rather than being unconditional, so it can never accidentally ship into a context with real user data.

### Q104. Is SQL injection a realistic risk anywhere in this codebase?
**TL;DR:** No — every database query across `finance_server.py`, `portfolio_server.py`, and `api/main.py` uses `asyncpg`'s parameterized query syntax (`$1`, `$2`, etc.), never raw string interpolation into SQL. Additionally, `finance_server.py` validates every `user_id` input as a well-formed UUID via `uuid.UUID()` *before* it ever reaches a query — which is defense-in-depth (rejecting malformed input early) rather than the actual SQL-injection prevention mechanism (which is the parameterization itself).

This is one of the cleaner, more consistently-applied security practices in the codebase — worth contrasting directly with the much weaker auth/rate-limiting stories above, to show the security posture isn't uniformly weak, just unevenly prioritized (the team clearly knew to parameterize every query, but didn't extend the same rigor to session/auth design).

### Q105. What's the actual risk if the E2B sandbox were somehow misconfigured to allow filesystem/network access — walk through the threat model.
**TL;DR:** The whole point of E2B is that the DCF/Monte Carlo Python code being executed is *LLM-generated*, not hand-written and reviewed — meaning the code that runs there is influenced (even if only indirectly, via the prompt describing the calculation to perform) by an LLM whose behavior isn't fully deterministic or guaranteed safe. If the sandbox weren't properly isolated (no network egress restriction, shared filesystem with the host, credentials mounted into the container), a cleverly-crafted prompt (or even an unlucky, non-adversarial LLM hallucination that happens to generate code doing something unexpected) could exfiltrate data, make unauthorized network calls, or interfere with the host process.

The mitigating factor here is that E2B's entire product is built around exactly this isolation guarantee (each run is a genuinely separate, ephemeral cloud sandbox/container) — so as long as it's configured with its defaults (no unusual shared volumes or credential injection into the sandbox), the actual residual risk is low. This question is really testing whether you understand *why* the sandboxing exists in the first place (see Q26) — the risk model, not just "we use E2B for safety" as a slogan.

---

## Section K: Infra & Deployment

### Q106. What Docker artifacts actually exist, and does `docker-compose up` genuinely work end to end?
**TL;DR:** `Dockerfile.api`, `Dockerfile.frontend`, and `Dockerfile.mcp` all exist at the repo root (an earlier audit found these missing entirely — that gap has since been closed), and `docker-compose.yml` defines 6 services: API, frontend, Postgres, Redis, Qdrant, and Temporal server. It should stand up successfully as far as those 6 services go.

One real gap: **no Temporal worker process is defined in compose at all** — `workflows/temporal_worker.py` and the morning-briefing cron (`workflows/morning_briefing.py start`) would need to be run manually, separately, outside of `docker-compose up`. Compose only stands up the Temporal *server* (the durable-state backend), not any of the worker processes that actually execute workflow activities — so out of the box, `docker-compose up` gives you a fully running API + frontend + all datastores, but the Temporal-based morning briefing feature would silently do nothing until a worker is started by hand.

### Q107. Why does `Dockerfile.api` explicitly install a CPU-only build of PyTorch rather than letting `pip install -r requirements.txt` pull in the default?
**TL;DR:** `sentence-transformers` (used for the local MiniLM embedding model) depends on PyTorch, and PyTorch's default PyPI package for GPU support pulls in CUDA libraries that can add several gigabytes to the image — entirely wasted weight for a container that will only ever run embedding inference on CPU (there's no GPU in this deployment target). Installing explicitly from PyTorch's CPU-only wheel index first, before the rest of `requirements.txt`, keeps the image dramatically smaller and the build faster, with zero functional loss since the embedding model was never going to use a GPU here anyway.

### Q108. What's the significance of pre-downloading the embedding model at Docker build time, and what happens if that step fails?
**TL;DR:** `Dockerfile.api` runs a step that pre-downloads `sentence-transformers/all-MiniLM-L6-v2`'s weights during the image build (baking the model into the image layer, cached via a named volume `model_cache` mounted to `/root/.cache/huggingface`), specifically so the *first real request* to the API doesn't pay a one-time multi-second download-and-load penalty — that cost is paid once, at build time, instead of unpredictably on whichever user's request happens to be first.

Notably, this step is suffixed with `|| true` — if the pre-download fails for any reason (network hiccup during build, HuggingFace Hub outage), the Docker build **doesn't fail**; it just proceeds without the pre-cached model, meaning the first real request would then pay that download cost lazily instead. This is a deliberate "best-effort optimization, not a hard requirement" choice — reasonable, since a build failing entirely over an optional warm-up step would be a worse outcome than just accepting one slow first request.

### Q109. Explain the two separate Temporal workflow/queue setups and why they don't share a task queue.
**TL;DR:** `WealthOSWorkflow` (task queue `"wealthos-queue"`) mirrors the full 8-node analysis pipeline as a durable workflow, meant for scenarios where crash-safety/retries matter more than raw latency. `MorningBriefingWorkflow` (task queue `"wealthos-briefing-queue"`) is a separate, simpler 3-step sequential workflow (fetch data → generate briefing text → send notification) specifically for the daily 8 AM cron job.

They're on different queues because they're operationally distinct workloads with different scaling/priority needs — you might want dedicated worker capacity for the latency-sensitive interactive pipeline (if it were ever actually driven by Temporal in production, which it currently isn't — see Q110) separate from the low-priority, once-a-day briefing job, and putting them on separate queues lets you scale/monitor/prioritize workers independently rather than having one queue's backlog affect the other's SLA.

### Q110. Is `WealthOSWorkflow` (the Temporal-mirrored pipeline) actually used by any live request path, or is it dormant?
**TL;DR:** Dormant, as far as the live user-facing system goes — nothing in `api/main.py`'s `/analyze` endpoint invokes it; the live path always goes through `graph/graph.py`'s LangGraph execution directly. `WealthOSWorkflow` is only invoked via its own module's `_cli()` function (a manual command-line entry point) — it exists, is correctly structured, and would genuinely work if triggered, but isn't part of the request/response flow a real user experiences.

Combined with `finance_activity`'s explicit stub status (hardcoded numbers regardless of user, tagged "Phase 8 TODO" — see Q22), the honest characterization is: this is a well-built *demonstration* of "here's how you'd make the pipeline durable/crash-safe via Temporal," built to the same structural fidelity as the real pipeline, but not yet load-bearing for actual traffic. `MorningBriefingWorkflow`, by contrast, genuinely *is* wired to a live trigger — `/briefing/send-now` in `api/main.py` executes it synchronously on demand, and its cron schedule (`0 8 * * *`) would fire it automatically once `start_cron()` is invoked.

### Q111. What are the most operationally risky hardcoded values across the infra layer, ranked?
**TL;DR:** In rough order of "how badly would this bite you in a real deployment":
1. **Temporal server address hardcoded to `localhost:7233`** in three separate files (`api/main.py`, `temporal_worker.py`, `morning_briefing.py`) rather than reading `TEMPORAL_HOST` from env — `.env.example` itself documents this exact gap and flags it as needing a future refactor. In any real multi-host deployment, this would simply not work until fixed in three places at once.
2. **Default seeded admin credentials** (Q103) — a real production-safety issue if not gated behind a demo-mode flag.
3. **DB credential fallback hardcoded** in `finance_server.py` (`wealthos_user`/`wealthos_pass`) — fine as a local-dev default, dangerous if anyone forgets to override `WEALTHOS_DB_URL` in a real environment and it silently "works" against a weak default credential.
4. **Tax server's fiscal-year-specific dates** (Q45) — not infra per se, but similarly a "silently becomes wrong on a schedule nobody's watching" risk.

The common thread across all of these: none of them fail loudly. Each one "just works" until the exact moment its hardcoded assumption stops holding (a second host, a forgotten env override, a new fiscal year), at which point it either breaks in a confusing way or — worse, as with the tax dates — keeps running and silently produces wrong output.

---

## Section L: What Went Wrong — Bugs Found & Fixed

### Q112. Summarize the single most important bug this project ever had, and why it mattered so much.
**TL;DR:** The circular `ground_truth` bug in `ragas_eval.py` (full detail in Q79) — using the RAG pipeline's own generated answer as its own "ground truth" reference, making the recall/correctness metrics structurally incapable of failing. It mattered because it's the kind of bug that produces a plausible, official-looking number (a RAGAS score) that actively misleads anyone who trusts it — worse than a crash, because a crash at least tells you something is wrong.

### Q113. What was the MCP protocol gap found in an earlier audit, and what does "fixed" actually mean here?
**TL;DR:** An earlier audit found agents calling `from mcp_servers.market_server import get_price` — a direct Python import — instead of connecting through the actual MCP client/protocol, meaning the "MCP servers" were unreachable as standalone processes during normal pipeline execution; the protocol was declared in code (servers existed, decorated with `@mcp.tool()`) but never actually exercised end-to-end. The current codebase's `services/mcp_client.py`, read directly, confirms a genuine fix: it spawns the server as a real subprocess (`StdioServerParameters(command=sys.executable, args=[server_script])`) and performs the actual MCP handshake (`session.initialize()`) before calling tools via `session.call_tool()`.

"Fixed" here specifically means: at least the finance agent's migration to stdio transport is real and verifiable by reading the client code — not just a README claim. Whether *every* agent uses `MCPClient` rather than a direct import wasn't independently re-verified across all 8 agent files in this pass, so the precise claim to make is "the MCP client itself is a real, working implementation," which is a meaningfully strong, verified fact, distinct from "every single agent definitely uses it for every single tool call" (which would need a full agent-by-agent grep to assert with equal confidence).

### Q114. What was the "@trace_node bug" mentioned in project notes, and why is this exact class of bug easy to miss in review?
**TL;DR:** Per project change logs, the `@trace_node` decorator was originally applied to a helper function (`_fetch_user_risk_profile`) instead of the actual graph node function (`risk_node`) it was meant to instrument — meaning LangSmith was faithfully tracing a small internal helper while the actual node-level execution (inputs, outputs, latency of the real unit of work) went untraced. It was later moved to the correct function.

This class of bug is easy to miss specifically because it doesn't cause any visible failure — the decorator runs, LangSmith receives *some* trace data, dashboards show *something*, and nothing errors. The only way to catch it is to actually look at what's being traced and ask "is this the granularity I actually care about," not just "is tracing happening at all" — a good illustration of why "it's instrumented" and "it's instrumented correctly" are different claims that need separately verifying.

### Q115. What was wrong with the original `.env.example`, and why does that matter more than it sounds?
**TL;DR:** An earlier audit found it completely empty — 15+ required environment variables (API keys, DB URLs, feature toggles) were used throughout the codebase but nowhere documented in the one file meant to onboard a new contributor. It's since been filled in with every var, including "Required"/"Recommended" annotations per key.

This matters more than it sounds like a minor doc gap: an empty `.env.example` means literally no one — not a new team member, not a future version of yourself six months later, not an interviewer trying to run your demo — can get the project running without either already knowing every required key by heart or reverse-engineering them one crash at a time by grepping `os.getenv()` calls across the codebase. It's the single highest-leverage, lowest-effort fix available in a project like this, and its absence (or presence) is often a fast proxy for "how much this team actually thought about someone else needing to run this."

### Q116. Trace through the personal-document RAG bug that was "fixed" — what broke, and how was it diagnosed?
**TL;DR:** Per project notes, `research_agent.py` was calling `qc.query_points()` on the Qdrant client for personal document retrieval — a method that doesn't exist on that client object (an `AttributeError`), meaning every attempt to retrieve a user's uploaded personal documents (salary slips, loan statements) silently failed inside a broad exception handler, so the writer never received `personal_docs_ctx` at all, and every memo produced for a user who'd uploaded personal documents simply ignored them without any visible error. It was fixed by switching to `qc.scroll()` (the correct Qdrant client method for retrieving points by filter rather than by vector similarity — appropriate here since personal-doc retrieval is closer to "get everything tagged with this user's ID" than a semantic search).

This is a good example of a failure mode enabled by *overly broad* exception handling elsewhere in the pipeline: the same "never let one node's failure crash the whole pipeline" philosophy that makes the system resilient also means a straightforward `AttributeError` — the kind that would normally be caught instantly by any smoke test — instead degraded silently into "the feature just doesn't work," discoverable only by specifically testing the personal-document-upload flow and noticing the memo never referenced anything from the uploaded file.

### Q117. What was the writer-side half of the personal-docs bug, and why did fixing only the retrieval side (Q116) not fully solve the problem?
**TL;DR:** Even after `research_agent.py`'s retrieval bug was fixed and `personal_docs_ctx` started actually populating in state, `writer_agent.py` needed a *second*, independent fix — it wasn't extracting that field from the RAG context and injecting it into the Personal Finance Fit section of the prompt. Two separate bugs, in two separate files, both had to be fixed before personal documents actually showed up in a memo end to end.

This is a good illustration of why "fix the bug" in a multi-stage pipeline often means "trace the *entire* path the data was supposed to take, not just the first broken link you find" — fixing retrieval alone would have made the data *available* in state without it ever actually reaching the final output, and a shallow verification (does `personal_docs_ctx` get populated?) could have declared victory prematurely without checking whether the writer actually consumed it.

### Q118. The `test-user` UUID mismatch bug — explain exactly what happened and its downstream effect.
**TL;DR:** `finance_server.py`'s `get_transactions()` validates `user_id` as a well-formed UUID before querying. The literal string `"test-user"` (used as a convenient placeholder ID in early development/testing) is not a valid UUID, so validation rejected it — `get_transactions()` silently returned an empty result rather than raising a clear "invalid user ID" error, which then cascaded into the health score defaulting to zero (since a health score computed from zero transactions has nothing to compute from), producing a Personal Finance Fit section that looked like a user with literally no financial data, rather than surfacing "this user ID was malformed" anywhere.

The documented fix options are either relaxing `finance_server.py`'s validation to accept non-UUID demo identifiers, or standardizing the demo user ID to an actual UUID (which is in fact what the rest of the codebase settled on — the `00000000-0000-0000-0000-000000000001` nil-UUID convention seen throughout `test_e2e.py`, `promptfoo_provider.py`, `reliability_eval.py`, and the seed data in `init_db.sql`). This is a nice example of a bug whose real fix was "standardize on one convention everywhere" rather than a narrow patch to one function.

### Q119. What's the current status of the "MCP tools claim" discrepancy — was it a lie, or a stale count?
**TL;DR:** Stale count, not a lie — but the distinction matters for how you'd describe it. An earlier README claimed 13 calculator tools when the code actually had 7; recounting the current codebase directly from `@mcp.tool()` decorators (not trusting any header comment or README claim) gives an accurate total of 45 across all 7 servers today, and the calculator server specifically has exactly 7. Separately, `market_server.py`'s own in-file header comment (not the README) currently undercounts itself at 10 tools when it actually has 13 — a newer example of the same underlying pattern (documentation not updated when tools were added) recurring at a smaller scale, inside the code itself rather than just in the README.

The generalizable lesson: any claimed count (tool count, dataset size, agent count) in this codebase's comments or docs should be treated as a *hypothesis to verify*, not a fact — and the actual, repeatable way to verify it is grepping the real decorators/entries, exactly as was done to produce this document.

### Q120. Two module-level crash bugs were found in an earlier audit — what were they, and why is a *module-level* bug worse than a bug inside a function?
**TL;DR:** `agentops_config.py` used the name `wraps` (from `functools`) without importing it, and `weave_config.py` had an unconditional `import wandb` at the top of the file — both would raise an exception the instant the *module itself* was imported, regardless of whether any function inside it was ever called.

A module-level bug is categorically worse than a bug inside a function body because a function-level bug only manifests when that specific code path actually executes (so it can hide for a long time if that path is rarely hit) — but a module-level import error kills the *entire process* at startup, the moment anything anywhere tries to import that module, even transitively. If `weave_config.py` is imported by `api/main.py` (which it is, since `init_weave()` is called at startup), an unconditional `import wandb` with `wandb` not installed would crash the *entire API server on boot*, not just disable one optional feature — turning "W&B logging isn't configured" (a graceful degradation) into "the whole product is down" (a total outage), purely because of where the `import` statement was placed. This is exactly why the current `weave_config.py`, as read in this pass, wraps its `weave.init()` call in try/except that separately catches `ImportError` — the fixed version specifically protects against the *class* of failure the original bug represented, not just that one instance of it. Since `agentops` has since been fully removed from the project (replaced by LangSmith for tracing, per `requirements.txt`'s comment), that specific bug is now moot — the file it lived in no longer exists in the dependency tree at all.

### Q121. Was the docker-compose gap (3 missing Dockerfiles) actually fixed, and how would you verify that yourself rather than trust the claim?
**TL;DR:** Yes, verified directly by checking the filesystem: `Dockerfile.api`, `Dockerfile.frontend`, and `Dockerfile.mcp` all exist at the repo root today. The verification method here is the important part of the answer, not just the yes/no: rather than trusting a status table in a planning doc that says "DONE," the actual check is "does the file exist, and does it contain something plausible" — which is exactly the kind of low-effort, high-confidence verification step that should always be the first thing you do before repeating any "X is fixed" claim as fact.

---

## Section M: Case-Based Scenarios

### Q122. A user asks "Should I invest ₹20,000 in Reliance for a quick 2-week trade after the earnings beat?" Walk through exactly what happens differently than a long-term query.
**TL;DR:** The router classifies this as short-term horizon (explicit time language: "2-week", "quick trade") with high confidence, setting `fetch_plan = {use_dcf: False, use_technicals: True, use_options: True, use_news_full: True}`. Downstream, `code_node` skips the 5-year DCF/Monte Carlo entirely and instead computes RSI/MACD/Bollinger Bands and options put/call ratio + IV. `research_node` deprioritizes 10-K structural risk-factor chunks in favor of recent news and earnings-related content. The writer produces a "Trading Setup" section (support/resistance levels) instead of "Valuation Analysis," and the memo's verdict language shifts toward TRADE/PASS with a 1-week target rather than a long-horizon Buy/Hold/Avoid framing.

If the router's confidence in this classification were below 0.65 (e.g. an ambiguously-worded query), it would override its own classification and default to "long" instead — meaning a genuinely short-term question, asked ambiguously enough, could still get the long-term DCF-heavy treatment as a deliberate safety bias rather than guessing wrong in the riskier direction (skipping a DCF that was actually wanted is a smaller miss than running one that wasn't).

### Q123. A user uploads a salary slip PDF, then asks about investing. Walk through the full data path from upload to memo, including where it could silently fail.
**TL;DR:** The file is POSTed to `/upload-personal-doc`, saved permanently to `data/personal_docs/{user_id}/{filename}` (not a tempfile, so it survives across sessions), and indexed into Qdrant via `FilingIndexer.index_personal_doc()` under a synthetic `PERSONAL_{user_id}` ticker namespace — using a cruder, flat 150-word chunker (no sentence-boundary awareness, no hierarchical parent/child structure) than the SEC-filing pipeline, since personal docs are shorter and don't need the same treatment. When the user later submits an analysis query, `research_node` needs to retrieve those chunks (via `qc.scroll()`, filtered by the personal ticker namespace — a fix for the earlier `qc.query_points()` bug, see Q116) and populate `personal_docs_ctx` in state, and `writer_agent.py` needs to specifically extract that field and inject it into the Personal Finance Fit section (the second half of that earlier bug fix, see Q117).

Where it could still silently fail today: if the PDF is a scanned image rather than text-based, extraction cascades through pdfplumber → pypdf → OCR (pytesseract + pdf2image) — if all three somehow produce near-empty text, the chunker's 20-word minimum filter would just drop everything, and the user would get a memo with no error message, simply missing personal-finance-fit context, indistinguishable from "the feature just didn't fire this time" without checking the indexer's logs directly.

### Q124. A user asks about a ticker WealthOS has never seen before — say, a small-cap Indian company not in the BSE scrip map. What actually happens, step by step?
**TL;DR:** Router checks Qdrant chunk count → 0, classifies `company_tier = "not_indexed"`. It fires `asyncio.create_task(_on_demand_index(ticker))` as a background task and proceeds with the rest of the pipeline immediately (not blocking on indexing). But because this ticker isn't a US ticker (no SEC EDGAR data) *and* isn't in the hardcoded `BSE_SCRIP_MAP` (only 29 major companies), the on-demand indexing attempt has essentially nothing to index — no SEC filing exists, and the BSE downloader has no scrip ID to build a URL from. Research falls back to whatever thin content yfinance's `.info` description provides (a few hundred words at most), and the memo is produced with minimal RAG grounding, likely leaning heavily on live price/financials data (which yfinance does cover for most listed tickers) rather than qualitative filing content.

This is the genuine, currently-unaddressed edge of the system's coverage — every graceful-degradation mechanism (background indexing, thin-content fallback) is designed to soften the blow, but for a ticker with literally no indexable source available (not in SEC EDGAR, not in the hardcoded Indian company list), there's no data path that produces rich RAG context, only the live-price/financials floor.

### Q125. Two users ask about the exact same stock at the exact same time — one with a ₹5,000 monthly surplus and high EMI debt, one with a ₹50,000 surplus and no debt. Explain concretely how and where the outputs diverge.
**TL;DR:** The market data, RAG-retrieved filing content, and risk score's *macro* component (VIX, rates) would be **identical** for both users — those are ticker-specific/market-wide, not personalized. Divergence happens in three specific places: (1) `finance_node`'s health score and surplus calculation, computed from each user's own real Postgres transaction/EMI data — the high-debt user likely gets a lower health score and a debt-burden flag if `monthly_emi / income > 0.5`; (2) the risk scorer node, which injects each user's own historical risk profile (buy/hold/avoid pattern from `user_risk_profiles`) — a user with a track record of avoiding high-risk stocks might see a marginally more conservative framing even for the identical stock; (3) the writer's Personal Finance Fit section, which explicitly reasons about whether ₹20,000 is affordable *given this user's specific surplus* — for the ₹5,000-surplus user, the memo would likely flag that a ₹20,000 investment represents multiple months of surplus and suggest a smaller position size or phased entry, while the ₹50,000-surplus user gets no such caveat.

The Valuation Analysis/DCF numbers, risk factors cited from the 10-K, and the underlying BUY/HOLD/AVOID lean driven by company fundamentals would be broadly the same for both — personalization here is layered *on top of* a shared factual core, not a wholesale rewrite of the analysis.

### Q126. Groq goes down entirely (not just rate-limited — a genuine outage) mid-pipeline. What's the user-facing experience?
**TL;DR:** Badly degraded, and this is a real gap worth naming honestly. Because `llm_client.py`'s key rotation only advances to the next key on an HTTP 429 (Q35) — any other failure mode (connection refused, 500, timeout) breaks the rotation loop immediately rather than trying the remaining keys — a genuine Groq outage would fail on the *first* key attempt with a non-429 error and return an empty string without ever trying keys 2 or 3, even though those keys would be equally useless against a full provider outage anyway (a full Groq outage affects all keys identically, so this particular quirk doesn't actually cost anything extra in *this specific* failure mode — it would only matter for a per-key-specific issue).

The bigger gap: there's no fallback to any other LLM provider anywhere in `llm_client.py` — despite the `.env.example` documenting `GEMINI_API_KEY`/`OPENROUTER_API_KEY`/`ANTHROPIC_API_KEY` as "recommended" keys, none of them are actually wired as a Groq fallback in the code that was read. Every LLM-dependent node (finance classification, research synthesis, risk debate, writer) would return empty/degraded output, likely producing a memo with large gaps or, in the worst case, an error surfaced to the user via `state["error"]`. This is a legitimate single-point-of-failure worth flagging explicitly if asked "what's your biggest availability risk."

### Q127. A malicious user crafts a query designed to make the LLM ignore its financial-advisor framing and instead leak the system prompt. Trace exactly what defenses would and wouldn't stop this.
**TL;DR:** `_sanitize_query()`'s narrow denylist (Q102) would only catch an attempt using almost exactly the phrase "ignore previous instructions" or one of its three sibling patterns — any rewording ("disclose your system prompt," "what were you told before this conversation," a foreign-language equivalent, or splitting the request across multiple sentences) sails through untouched. Downstream, there's no explicit output-side check specifically for "did the model leak its system prompt" — `guardrails/validators.py`'s checks are about *memo structure* (valid verdict enum, risk score range, required sections present), not about prompt-leakage detection.

The realistic outcome: whether this specific attack succeeds depends entirely on the underlying model's own resistance to prompt-leaking (a property of Groq's llama-3.3-70b model itself, not anything WealthOS adds), because WealthOS's own defenses don't specifically address this attack class at all — they only catch the crudest, most literal "ignore previous instructions" phrasing. This is a good scenario for demonstrating you understand the difference between "we have a prompt injection guard" (true, narrowly) and "we're protected against prompt injection" (false, meaningfully) — the same distinction that shows up repeatedly in this codebase's honest self-assessment.

### Q128. The Cohere API key expires unnoticed. What breaks, what doesn't, and how would you even notice?
**TL;DR:** Nothing breaks in the sense of an error or crash — `_hybrid_search_sync()`'s Cohere reranking step falls back to simply taking the first 5 of the already-RRF-fused 20 hits whenever the Cohere call fails for any reason, including an auth failure from an expired key. Retrieval *quality* silently degrades (you lose the cross-encoder's more accurate relevance ordering, falling back to pure rank-fusion order), but functionally the pipeline keeps running exactly as before with no visible symptom.

How you'd actually notice: not from any error log (none would be emitted for this specific failure in the live request path, given the broad catch-and-fallback), but from a *downstream* signal — a RAGAS run showing a `context_precision` regression compared to a previous baseline run, or, less rigorously, a "the memos feel a bit less sharp lately" intuition from someone reading output regularly. This is a strong illustration of why layered evaluation (Q77) matters operationally, not just for pre-ship quality gates: RAGAS re-run periodically in production would be one of the only mechanisms that could actually catch this specific, silent degradation.

### Q129. A user runs the exact same query on AAPL five times in a row and gets BUY, BUY, HOLD, BUY, BUY. Is that a passing or failing result, and what would the reliability eval actually report?
**TL;DR:** Passing, by the numbers, but worth understanding *why* precisely. pass^k: mode verdict is BUY (4 of 5 runs), consistency = 4/5 = 0.80, which exactly meets the `CONSISTENCY_THRESHOLD = 0.80` — a pass, right at the boundary (if it had been 3-of-5, consistency would be 0.60 and it would fail). BERTScore would then separately check whether the actual *content* of all 5 memos (not just the verdict word) stayed reasonably similar to the first run — if the HOLD outlier's memo still discusses broadly the same risk factors and numbers, just landing on a marginally different call, BERTScore F1 could plausibly still clear the 0.85 floor and the reliability check overall passes.

This is a good scenario for demonstrating you understand that pass^k alone is a somewhat forgiving metric — it explicitly tolerates a 1-in-5 disagreement by design (an 80% threshold, not 100%), reflecting a realistic acceptance that LLM outputs won't be perfectly deterministic even at low temperature, while still catching genuinely unstable behavior (a 60% or lower consistency rate) as a real problem.

### Q130. A new fiscal year begins and nobody has touched `tax_server.py`. A user asks for their FY tax breakdown in the new year. What exactly goes wrong?
**TL;DR:** `advance_tax_schedule()`'s installment due dates are hardcoded literal strings ("15 June 2024", "15 September 2024", "15 December 2024", "15 March 2025") — there's no date arithmetic deriving these from the current year, so a user in the new fiscal year would receive due dates that are now in the past, silently wrong, with no validation anywhere that checks "are these dates still in the future relative to today." The tax slab percentages themselves (old/new regime brackets) are also hardcoded to FY2024-25/AY2025-26 rules specifically — if the government changes slabs in a subsequent budget (a near-certainty over enough years), `calculate_tax()` would keep computing tax using outdated brackets with equally no warning.

This is a great "what went wrong that hasn't been fixed yet" scenario, distinct from Section L's *already-fixed* bugs — it's a live, ticking, fully-understood-and-documented risk (explicitly called out in this document's own MCP-server analysis) that simply hasn't hit yet because the fiscal year hasn't rolled over during the project's lifetime so far. The fix is conceptually simple (compute installment dates relative to the actual current fiscal year, and version the tax-slab constants by effective date) but hasn't been prioritized, presumably because it doesn't visibly break anything *yet*.

### Q131. A user's browser session is inspected by someone else who copies the `wo_user_id` cookie value and pastes it into their own browser. What can they now do?
**TL;DR:** Effectively everything that user could do, with zero further authentication required — this is the direct, concrete consequence of the unsigned-cookie auth model (Q100). They could view that user's full analysis history (`/history/{user_id}`), portfolio holdings and P&L (`/portfolio/{user_id}`), Mem0 memory contents (`/memory/{user_id}`, including deleting it via the `DELETE` endpoint), risk profile (`/user-profile/{user_id}`), and past investment verdicts (`/user-analyses/{user_id}`) — none of these endpoints have any auth dependency beyond the raw `user_id` value being correct, and it doesn't even need to come from a cookie at all; it could be typed directly into a URL.

This scenario is the clearest, most concrete way to communicate exactly *why* "no signed session token" is a real problem rather than an abstract concern — walking through precisely which endpoints become exploitable, and exactly what data is exposed, is more convincing than saying "the auth is weak" in the abstract.

### Q132. The company decides to onboard a second, brand-new LLM provider (say, adding real Anthropic support as a genuine fallback, not just a documented-but-unused env var). What would actually need to change, concretely?
**TL;DR:** Primarily `services/llm_client.py`'s `call_llm()` function — currently hardcoded to Groq's chat completions endpoint and response format. You'd need to abstract the request/response shape behind a provider-agnostic interface, add a genuine fallback branch (not just another Groq key — an entirely different provider/endpoint/auth scheme) that triggers on *any* Groq failure (not just 429s, addressing the Q35/Q126 gap directly), and decide how cost tracking (`_track_usage()`, currently hardcoded to Groq's specific per-token pricing constants) would need per-provider pricing tables instead of one hardcoded rate.

You'd also need to think about behavioral drift — different providers/models have different instruction-following tendencies, so the DSPy-compiled prompt (optimized specifically against Groq's llama-3.3-70b's behavior via its bootstrapped examples) might not transfer its quality gains cleanly to a different model without recompiling against the new target model. This is a good scenario for demonstrating you understand that "add a fallback provider" isn't just a config change — it touches the LLM client, the cost-tracking pricing model, and potentially invalidates DSPy's compiled optimization work, which was implicitly tuned against one specific model's response characteristics.

### Q133. Someone asks: "your system produced a BUY verdict with risk_score=9 — is that a bug?" How do you actually answer, using what you now know about the codebase?
**TL;DR:** By the `VerdictConsistencyMetric`'s actual (not docstring-claimed) rule, `risk_score >= 9` with a BUY verdict is a **hard fail** — that specific combination is exactly what the deterministic check exists to catch, so if you observed that in a live memo, either the check wasn't actually run against it, or there's a genuine gap between what the eval suite checks and what actually gets shipped to a user.

This is deliberately a "gotcha" scenario testing whether you'd correctly recall the *exact* threshold (9, not the docstring's stale "6") and whether you'd think to ask the follow-up question that actually matters operationally: is this deterministic check merely an *offline eval assertion* (only catches the problem in a test run against a static golden dataset) or a genuine *live guardrail* (runs on every real pipeline output before it reaches a user)? Based on the files reviewed, `VerdictConsistencyMetric` lives in `eval/deepeval_metrics.py` and is exercised by `tests/test_deepeval.py` against the golden dataset — there's no evidence it's wired into `graph/nodes.py`'s `validation_node` as a live check on real-time output, meaning a live risk_score=9 BUY *could* currently reach a real user with nothing stopping it, which is a genuinely important gap between "we have a check for this" and "this check protects live users."

---

## Section N: Comparison Questions

### Q134. DSPy's compiled prompt vs. the hand-written baseline prompt — which actually produces better memos, and how do you know rather than assume?
**TL;DR:** You know via `eval/evaluate.py`'s `run_compare()` function specifically — it's the one script built specifically to answer this, generating both versions of the memo for the same 5 held-out test cases and scoring both with the same 4 structured-output graders, then reporting the delta. Without running that comparison, "DSPy makes it better" would be an assumption, not a verified claim — this is exactly the trap the project explicitly built tooling to avoid falling into (see Q90).

The structural reason to *expect* DSPy to win, even before running the comparison: the hand-written baseline has zero examples (instructions only), while the compiled version embeds real, metric-passing few-shot examples directly in the prompt — in-context learning from concrete examples tends to improve format/style adherence more reliably than instructions alone, especially for a task with a rigid structural contract (7 sections, specific citation style, exactly 3 numbered final-verdict reasons).

### Q135. RAGAS vs. DeepEval — if you could only keep one, which would you keep, and what would you lose?
**TL;DR:** Keep DeepEval, because it evaluates the thing users actually see (the final memo) end to end, including faithfulness/hallucination/relevancy of the *writer's* output — which implicitly reflects retrieval quality too (a memo built on bad retrieval is more likely to be ungrounded or generic, and DeepEval's faithfulness/groundedness checks would tend to catch that downstream, even without directly measuring retrieval precision/recall).

What you'd lose: precise, *isolated* diagnosis of whether a quality problem originates in retrieval specifically vs. the writer's synthesis specifically. Without RAGAS's dedicated context precision/recall metrics, a regression in Qdrant's filter configuration or the Cohere reranker silently failing (Q128) might eventually surface as a DeepEval faithfulness dip, but you'd have to debug backward from "the memo seems worse" to "oh, it's a retrieval problem" rather than seeing it directly in a retrieval-specific metric. In a resource-constrained team, this is a real, defensible tradeoff — DeepEval gives broader end-to-end coverage per eval-run dollar spent; RAGAS gives sharper root-cause localization when something does go wrong.

### Q136. LangSmith vs. W&B Weave — why not just standardize on one?
**TL;DR:** Because they're genuinely optimized for different jobs, per the project's own explicit design intent (Q97): LangSmith for per-request execution tracing (what happened, in what order, how long, how much did it cost), Weave for offline eval-run scoring and comparison over time. You *could* force one tool to do both jobs — LangSmith has some eval features, Weave can technically log arbitrary events — but each is meaningfully better at its primary job than the other is at that same job, and the maintenance cost of two integrations is modest (a few functions each) relative to the value of using the right tool for each concern.

The honest counter-argument, worth acknowledging directly: in *this specific codebase*, Weave's live-request value is currently close to zero (Q95 — it initializes at boot but nothing from live traffic logs to it), so today, practically speaking, you could remove Weave from the live API's startup path with zero loss of live functionality — it would only need to remain wired into the offline eval scripts. That's a case where the *stated design intent* (two tools, two clear jobs) and the *current actual wiring* (one tool half-idle in the live path) have drifted apart — worth calling out as its own small "comment vs. code" gap, in the same family as several others found in this review.

### Q137. Groq vs. a hypothetical OpenAI/Anthropic-only setup — what would this project gain and lose by switching entirely?
**TL;DR:** Gain: likely better raw reasoning quality on some tasks (frontier models from OpenAI/Anthropic are generally considered stronger than llama-3.3-70b on complex multi-step reasoning), and access to genuine structured function-calling APIs that could replace the fragile text-parsing ReAct loop in `query_engine.py`'s `query()` method (Q63) with something far more robust. Lose: the entire free-tier cost model this project is built around — Groq's free tier (12-30K TPM depending on model) is what makes running dozens of eval iterations, DSPy compilation sweeps, and reliability testing (5x repeated runs per reliability check) financially trivial; a paid-only OpenAI/Anthropic setup would turn every one of those (currently essentially free) evaluation runs into a real per-token cost that would meaningfully change how liberally you could afford to test.

Also lose: raw inference *speed* — Groq's custom LPU hardware is specifically known for very fast token generation, which matters directly for this project's ~45-90 second full-pipeline latency budget; a slower-but-smarter model could plausibly make the *overall* pipeline latency worse even if any single call's *reasoning quality* improved, unless the quality gain reduced the total number of calls needed elsewhere in the pipeline.

### Q138. BootstrapFewShot vs. MIPRO (DSPy's more advanced optimizer) — what would actually change if this project switched?
**TL;DR:** MIPRO optimizes *both* the instruction text and the few-shot example selection jointly (via a more sophisticated search over both), whereas BootstrapFewShot only curates examples, leaving the instruction text as originally hand-written in the `WriteMemo` signature's docstring. Switching would mean the compiler could potentially discover better *phrasing* of the writer's rules themselves (not just better example selection) — a strictly larger search space, with a correspondingly higher compilation cost (many more evaluation rounds needed to search that larger space effectively).

Given this project's Groq free-tier rate-limit constraints (the explicit reason BootstrapFewShot + the smaller 8B model was chosen for compilation, Q73), MIPRO would likely need either a paid tier, a much longer/slower compilation run, or a further-reduced training set to stay within the same rate-limit budget — the choice of optimizer here is really a downstream consequence of the same cost/rate-limit constraint that shapes several other decisions across the project (key rotation, sleep-between-calls in eval scripts, cheap classification models for routing).

### Q139. Qdrant's server-side RRF fusion vs. a hand-rolled client-side score-blending approach — what's the practical difference?
**TL;DR:** Server-side RRF (what this project actually uses, Q52-53) means the dense and sparse candidate retrieval *and* their fusion all happen inside Qdrant in a single round trip (`query_points()` with `Prefetch` + `FusionQuery`) — the Python client sends one request and gets back one already-fused ranked list. A hand-rolled client-side approach would instead require two separate round trips (one dense search, one sparse search), pulling both full candidate lists back over the network into Python, then implementing the RRF math (or some ad-hoc score-blending heuristic) yourself before truncating to the final result set.

Practically: server-side fusion is both simpler (no fusion math to write, test, or get subtly wrong — like accidentally blending different-scale raw scores instead of rank positions, a common client-side mistake, see Q53) and faster (one network round trip instead of two, plus Qdrant's fusion implementation is presumably more optimized than an equivalent hand-rolled Python loop over potentially dozens of candidates). This is a good example of "use the platform's native capability instead of reimplementing it" — the kind of choice that's easy to get subtly wrong if built by hand (particularly the score-scale mismatch pitfall) and just correct by construction when delegated to the vector database itself.

### Q140. Mem0's opaque, LLM-driven memory extraction vs. this project's own explicit Qdrant `user_analyses` collection — which approach would you trust more for a feature you need to reason about precisely?
**TL;DR:** The explicit Qdrant collection, specifically because you control exactly what's embedded, exactly what's stored as payload, and exactly what filter conditions retrieval uses — Mem0's internal fact-extraction and retrieval logic is a black box you can't inspect or unit-test the internals of; you can only observe its input/output behavior. For a use case where you need to *guarantee* something precise (e.g. "always retrieve the 3 most recent decisions about this exact sector, filtered by this exact user"), an explicit, self-owned vector collection with your own payload schema gives you that guarantee directly; Mem0's fixed generic search query (Q68) can't express that precision at all today.

The tradeoff, honestly: Mem0 does genuinely valuable work you'd otherwise have to build yourself (deduplication across sessions, natural-language fact extraction from raw conversation, semantic consolidation of overlapping memories) — building an equivalent from scratch on top of raw Qdrant would be significant additional engineering. The right mental model isn't "one is better," it's "Mem0 for broad, low-precision cross-session signal with minimal engineering effort; a self-owned Qdrant collection for anything requiring precise, filterable, guaranteed retrieval behavior" — which is exactly the complementary relationship the project's design docs describe (even though, per Q66, the precision use case isn't fully wired up yet in the live code).

### Q141. A cross-encoder reranker (Cohere) vs. just trusting the RRF-fused order directly — is the extra API call and latency actually worth it?
**TL;DR:** Generally yes for this use case, with a real, quantifiable caveat: the project's own failure-registry notes estimate retrieval quality degrades roughly 15-20% without Cohere reranking — a meaningful, non-trivial hit, not a marginal one. Cross-encoders read the query and document jointly, which typically produces materially better relevance judgments than pure similarity-based (bi-encoder embedding or BM25) retrieval alone, especially for queries where relevance depends on nuanced phrasing rather than just keyword/topic overlap.

The honest tradeoff: it's an extra network round trip (added latency) and a paid API dependency (Cohere's free tier caps at 1000 reranks/month) — for a project already juggling free-tier limits elsewhere (Groq TPM), this is one more rate-limited dependency to manage. But because the fallback (Q54) is graceful (just use the RRF order directly) rather than a hard failure, the cost/benefit tips clearly toward "keep it, use it when available" — you get the quality win when the budget allows, and a documented, understood 15-20% quality cost (not a mystery, not a crash) when it doesn't.

### Q142. Structured function-calling (used in `evaluate.py`'s graders) vs. text-parsed tool calls (used in `query_engine.py`'s ReAct loop) — why does this codebase use both approaches in different places, rather than standardizing on one?
**TL;DR:** They were built for different purposes at different points, and the "right" choice genuinely differs by context. `evaluate.py`'s graders use `with_structured_output(schema, method="json_schema")` because their entire job is producing a small, fixed-shape verdict (a boolean plus an explanation string) — a perfect fit for schema-enforced structured output, with zero ambiguity about what "correct" output looks like. `query_engine.py`'s ReAct loop, by contrast, needs the LLM to make a *multi-turn, open-ended* decision ("should I query SQL or vector search next, and with what input, or am I done and ready to answer?") across up to 4 steps — a more free-form, conversational reasoning pattern that the hand-rolled `ACTION:`/`INPUT:` text-parsing approach was presumably built to support before (or without) reaching for a full structured tool-calling API integration.

The honest assessment: the ReAct loop's text-parsing approach is strictly worse *engineering* than structured tool-calling would be (Q63 already flags its fragility — a markdown-wrapped response or whitespace variance wastes a whole step) — this isn't a case of two equally-valid approaches for different needs, it's a case where one part of the codebase (the eval graders) used the more robust, modern approach and another part (the RAG agent's ReAct loop) didn't, likely because they were written at different times or by different priorities, not because text-parsing was ever the *better* choice for a ReAct loop specifically. If asked to prioritize a refactor, migrating `query_engine.py`'s tool dispatch to structured function-calling would be a clear, well-justified improvement.

### Q143. Delete-then-reinsert re-indexing (used for SEC filings) vs. true upsert-by-ID (not used here) — what did the project actually choose, and why does it matter for correctness?
**TL;DR:** The project chose delete-then-reinsert: before indexing a filing, `index_filing()` deletes all existing Qdrant points matching that `ticker` + `form_type`, then inserts the fresh set. True upsert-by-ID would instead compute a stable ID per chunk (e.g. a hash of its content or position) and let Qdrant's native upsert semantics overwrite only points that changed, leaving unchanged points untouched.

The correctness argument for delete-then-reinsert here: if a re-indexed filing produces a *different number* of chunks than the previous version (a very likely outcome any time chunking logic, section detection, or the source document itself changes even slightly), true upsert-by-ID would need some way to detect and clean up now-orphaned old chunks that no longer correspond to anything in the new chunk set — otherwise you'd accumulate stale, duplicate, or orphaned points over repeated re-indexes. Delete-then-reinsert sidesteps that entire problem by construction: there's no possibility of orphaned leftovers, at the cost of briefly having zero indexed content for that ticker during the reindex window (a real, if usually short, availability gap) and redoing embedding work for chunks that didn't actually change content between versions (a real, if usually acceptable, efficiency cost). For a re-indexing operation that happens infrequently and isn't latency-sensitive (unlike a live user request), trading a small, occasional efficiency/availability cost for guaranteed data-consistency correctness is the right call.

---

## Closing Notes

This document was built by reading the actual source files in `d:\projects\WealthOS` directly — function signatures, exact thresholds, real code paths — rather than summarizing the README or trusting in-code comments at face value. Several genuine comment/code discrepancies were caught precisely *because* of that discipline (the golden dataset's stale "15 entries" comment vs. the real 28, the `VerdictConsistencyMetric` docstring's stale risk-score cutoff, `market_server.py`'s stale tool-count header). That's the single most transferable lesson from this whole exercise: **in any nontrivial codebase, the comment is a claim, and the code is the evidence — always check the evidence.**

If you take one thing from this document into an interview or a design discussion, make it this: knowing precisely where a system is real, where it's a well-labeled shortcut, and where it's an honest-but-unaddressed gap is a stronger signal of engineering judgment than a system with no visible gaps at all — because a system with no visible gaps either hasn't been looked at closely enough, or is lying about something.
