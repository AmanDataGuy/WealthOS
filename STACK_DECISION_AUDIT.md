# WealthOS — Full Stack Decision Audit

**Date:** 2026-08-19
**Method:** every entry below is graded on three questions — **Required?** (does the project's actual functionality depend on it), **Working?** (verified live where practical this session, or cited to specific evidence where verified earlier), **Verdict** (keep / fix / replace, with the alternative named and reasoned, not just asserted).

Confidence levels are marked explicitly: **[LIVE]** = tested against a real running service this session, **[VERIFIED]** = confirmed by reading the actual executed code path (not docs/comments), **[ESTABLISHED]** = confirmed earlier this session with equivalent rigor, not re-tested in this pass.

---

## Datastores (covered in depth in the prior two messages — summarized here for completeness)

| Tech | Required? | Working? | Verdict |
|---|---|---|---|
| **PostgreSQL 16** | Yes — exact financial arithmetic, ACID writes, relational queries. No substitute among the alternatives checked (Qdrant, Redis, and even TigerBeetle at this scale). | ✅ **[LIVE]** — pooled connections tested against real seeded data this session. | **Keep.** Industry-consensus correct choice for this workload at this scale. |
| **Qdrant** | Not strictly — Postgres+pgvector could approximate it. But it's doing real, verified-excellent work (hybrid dense+sparse RRF). | ✅ **[LIVE]** — indexed 58 real chunks, confirmed retrievable, 3/5 top hits correct on a real query this session. | **Keep.** The one component where "fewer services" would cost more (rebuilding fusion quality) than it'd save (one container in an already-6-container compose file). |
| **Redis** | Yes for rate limiting's atomic sorted-set ops and TTL expiry; the caching role could technically be in-process at single-instance scale, but Redis is already a hard dependency either way. | ✅ **[LIVE]** — rate limiter tested this session (3 allowed, 2 blocked with correct 429+Retry-After). | **Keep.** |

**Leftover cleanup, not an architecture change:** dead `pgvector` references (`.env.example`'s wrong claim, two stale comments in `research_agent.py`/`agent_cards.py`, unused `pgvector/pgvector:pg16` CI image) from an old migration — still not cleaned up, offered twice, not yet actioned.

---

## Orchestration

### LangGraph
**Required?** Yes — the 8-node parallel pipeline (router → finance → [data+research] → [risk+code] → validation → rebalancing → writer) is the product's core structure.
**Working?** ✅ **[VERIFIED]** — real `asyncio.gather` parallelism, correct state merging, confirmed via direct code reading and multiple live pipeline-adjacent tests this session (portfolio pooling, JWT auth flow, earnings-call indexing all ran through or alongside real graph nodes).
**Verdict: Keep.** No serious alternative changes the calculus — a hand-written async pipeline would lose the declarative graph structure and per-node tracing seam for no benefit.

### Temporal
**Required?** Only partially. `MorningBriefingWorkflow` is genuinely used (`/briefing/send-now` triggers it live). `WealthOSWorkflow` — the full-pipeline mirror — is dormant: `finance_activity` is a hardcoded stub, and nothing in the live `/analyze` path invokes it.
**Working?** ⚠️ **[ESTABLISHED]** — the briefing workflow works; the pipeline-mirror workflow is structurally complete but never exercised by real traffic.
**Verdict: Half-keep.** Keep Temporal for the morning-briefing cron (it's the right tool — durable, crash-safe scheduling — and it's actually used). **Delete `WealthOSWorkflow`** — maintaining a second, unused copy of the pipeline's shape is ongoing cost for zero realized value (already flagged in `plan_ahead.md` Phase 7, item 15, not yet actioned).

---

## LLM & Prompting

### Groq (`openai/gpt-oss-120b` / `-20b`)
**Required?** Yes — every agent call.
**Working?** ✅ **[LIVE]** — fixed this session (the previous models, `llama-3.3-70b-versatile`/`llama-3.1-8b-instant`, were retired by Groq and had broken the entire pipeline; confirmed via a real `/v1/models` call and real completions against both replacement models).
**Verdict: Keep Groq, but the single-provider risk is real and unaddressed.** `OPENROUTER_API_KEY` is documented as a fallback and does nothing (confirmed by grep — zero references in `llm_client.py`). Given Groq has now demonstrably broken once, wiring a genuine fallback (or at minimum keeping the docs honest, which was done) has real, demonstrated justification — this isn't hypothetical risk-aversion.

### DSPy (BootstrapFewShot)
**Required?** Not strictly — a hand-written prompt would still produce a memo. But it's a good, deliberate choice.
**Working?** ✅ **[VERIFIED]** — `eval/compiled_writer.json` exists, loads correctly (confirmed at module-import time this session while testing agent imports), contains real graded few-shot examples.
**Verdict: Keep.** Already the *better* choice versus the alternative the project could have picked (LLM fine-tuning) — see the tech-fit assessment from earlier this session: fine-tuning would need training infra and ongoing retraining for a task where good few-shot examples already do the job. DSPy compilation is right-sized.

### Cohere reranking
**Required?** No — hybrid search works without it (falls back to raw RRF order).
**Working?** ⚠️ **[ESTABLISHED]** — degrades gracefully if the key is missing/fails; per `plan_ahead.md`'s own estimate, retrieval quality drops ~15-20% without it.
**Verdict: Keep.** Cheap, optional, real measured quality uplift, graceful degradation already correct. No better alternative at this cost point.

---

## RAG / Retrieval

### sentence-transformers/all-MiniLM-L6-v2 (embeddings)
**Required?** Yes — the whole RAG pipeline's dense vector side.
**Working?** ✅ **[LIVE]** — used directly in this session's earnings-call indexing (58 real chunks embedded and indexed correctly).
**Verdict: Keep.** Local, free, no API key, "good enough" quality at this corpus size (a few thousand chunks) where a marginally-better paid embedding API wouldn't move retrieval quality meaningfully. Right-sized.

### fastembed (BM25 sparse vectors)
**Required?** Only if hybrid search is kept (recommended: yes, per the Qdrant discussion above).
**Working?** ✅ **[LIVE]** — confirmed working as part of the same earnings-call indexing test.
**Verdict: Keep.**

---

## MCP Servers (7 servers, 45 tools)

**Required?** The *tools themselves* — yes, each one supplies real data (market prices, SEC filings, transactions, tax math, portfolio). The *MCP subprocess architecture specifically* — only partially justified.
**Working?** ✅ **[VERIFIED]** — genuine stdio subprocess transport confirmed earlier this session (not a direct-import shortcut), and `portfolio_server.py` was fixed and live-tested this session.
**Verdict: Keep the tools, question the uniform packaging.** Process isolation is real value for tools touching the DB or external APIs (market, SEC, news). It's pure overhead for `calculator_server.py` — 7 pure-math functions (XIRR, EMI, FIRE, etc.) with zero I/O, running as their own subprocess with connect/retry lifecycle management for no benefit. Recommend consolidating `finance_server` + `portfolio_server` + `calculator_server` (already flagged, `plan_ahead.md` item 16) — same domain, no loss of meaningful isolation, fewer subprocesses to manage.

---

## Memory

### Mem0
**Required?** Not clearly — see below.
**Working?** ⚠️ **[ESTABLISHED]** — reads/writes work, fails open if the API is down.
**Verdict: Genuinely questionable, not a clean keep.** Mem0 and the Qdrant `user_analyses` collection both answer "what has this user tended to do before" — Qdrant's version is more precise (filterable by ticker/sector, genuinely wired via `_get_past_decisions`), while Mem0 is opaque and queried with a fixed generic search string (not even the actual question). Mem0 is a paid external dependency for materially overlapping value. **Recommend evaluating removal**, or demoting it to a single onboarding-only "new user" signal rather than running both indefinitely (flagged, `plan_ahead.md` item 14, still unresolved).

### Postgres `user_risk_profiles`
**Required?** Yes — quantitative aggregate (buy/hold/avoid counts, avg risk score) genuinely feeds the risk scorer's calibration.
**Working?** ✅ **[VERIFIED]**.
**Verdict: Keep.**

---

## Validation

### `guardrails/` (custom Pydantic v2 validators)
**Required?** Yes — deterministic checks (risk score range, verdict enum, section presence) matter precisely because they're the one non-probabilistic safety net.
**Working?** ✅ **[VERIFIED]**.
**Verdict: Keep, but rename.** The folder name implies the third-party Guardrails AI library; it's homegrown. Not wrong to build it yourself — actively better for a fast, deterministic check — but the misleading name should change (cheap fix, not yet done).

---

## Code Execution

### E2B Sandbox
**Required?** Yes — the DCF/Monte Carlo math is LLM-*generated* Python, and running LLM-generated code outside a sandbox is a real remote-code-execution risk, not a theoretical one.
**Working?** ✅ **[LIVE]** — tested directly this session with the exact API `code_agent.py` uses (`Sandbox.create(api_key=...)`, `sandbox.run_code(...)`), confirmed a real sandbox execution round-trip.
**Verdict: Keep.** No credible alternative at this scale — running untrusted generated code in-process would be a security regression, not a simplification.

---

## Observability

### LangSmith
**Required?** No — the pipeline runs without it. But it's real, working instrumentation, not decoration.
**Working?** ✅ **[ESTABLISHED]** — `verify_langsmith()` called at boot, `trace_node()` genuinely wraps LangGraph nodes with a real no-op-when-disabled fallback, PII masking on `user_id` confirmed via direct code reading.
**Verdict: Keep.**

### W&B Weave
**Required?** No.
**Working?** ⚠️ Was initializing at boot for zero live-traffic benefit — **fixed this session** (removed from `api/main.py`'s lifespan; only `eval/evaluate.py`, which self-initializes, actually uses it).
**Verdict: Keep for offline eval scoring, correctly no longer paying its init cost on every live request.**

---

## External Data / Scraping

### yfinance
**Required?** Yes — primary source for live price/financials for both US and Indian tickers.
**Working?** ✅ **[ESTABLISHED]** — used throughout, including this session's real portfolio data test.
**Verdict: Keep.** Free, no key, covers both markets. No better free alternative at this scope.

### Firecrawl
**Required?** Yes, now — powers Reddit sentiment scraping *and* (as of this session) earnings-call transcript discovery/scraping via `/v1/search` + `/v1/scrape`.
**Working?** ✅ **[LIVE]** — the *old* key in `.env` returned `401` (discovered this session — meaning Reddit sentiment had been silently broken); the *new* key was verified live against real fool.com pages, confirmed real transcript discovery and full-text extraction.
**Verdict: Keep — but verify the key isn't stale again periodically.** This is the second time this session an "optional, fails gracefully" dependency turned out to be silently dead (the first was the Groq model IDs) — worth a periodic live smoke test rather than trusting "it fails open" to mean "it's fine."

### newspaper3k
**Required?** Only as the primary path for full news-article-body fetching (Firecrawl is the fallback).
**Working?** ⚠️ Not independently re-tested this session.
**Verdict: Keep, low-risk.** Free, no key, standard library for this exact job.

### Composio
**Required?** Nominally yes (email/WhatsApp notifications), but the implementation doesn't actually deliver on the "per-user" promise.
**Working?** ⚠️ **[ESTABLISHED]** — `send_notification()`'s `mock_db` has exactly one hardcoded UUID→contact mapping; every other user's notification falls through to the same global env-var destination. This is a documented, self-aware stub (explicit `TODO` comment), not a silent bug.
**Verdict: Fix or descope.** If notifications matter to the product, this needs a real DB-backed contact lookup (small, well-scoped fix — a few lines against the existing `users` table). If they don't matter for the current stage, remove the `COMPOSIO_API_KEY` requirement from the "required" framing rather than leaving a half-built feature implying more than it does.

---

## Backend / Frontend

### FastAPI
**Required?** Yes.
**Working?** ✅ **[LIVE]** — extensively tested this session (real server boot, real HTTP round-trips for the JWT auth flow, real rate-limiter behavior).
**Verdict: Keep.** Correct choice for an async Python API with this shape.

### Streamlit
**Required?** Yes, for the current single-user-at-a-time demo UI.
**Working?** ✅ **[VERIFIED]** — updated and confirmed syntactically correct this session (JWT token wiring).
**Verdict: Keep for now, with an honest ceiling.** Streamlit is the right choice for a fast, demo-quality UI — it is not the right choice if this ever needs a real multi-user production frontend with proper routing/state management (that's a different, bigger rebuild, not a "swap the library" change). Not a problem today; worth knowing the ceiling exists.

### Auth: bcrypt + JWT
**Required?** Yes.
**Working?** ✅ **[LIVE]** — built and fully verified over real HTTP this session (login issues a real signed token; wrong `user_id` + valid token → `403`; no token → `401`; correct token + correct `user_id` → `200`).
**Verdict: Keep.** Right-sized for a single-role app. (RBAC/ABAC explicitly assessed as premature in the tech-fit report — no second role exists yet to justify either.)

---

## Testing & Evaluation

Five genuinely distinct layers exist (RAGAS, DeepEval, LLM-as-judge `evaluate.py`, reliability/BERTScore, Promptfoo) — each targets a different failure mode, which is real stratification, not redundancy, **except**:

### DeepEval vs `evaluate.py`
**Required?** DeepEval yes (now CI-gating, fixed this session). `evaluate.py`'s 4 metrics genuinely overlap with DeepEval's 9.
**Verdict:** Keep DeepEval as primary; narrow `evaluate.py`'s stated scope to the one thing it uniquely does — the baseline-vs-DSPy-compiled A/B comparison — rather than also functioning as a second general-purpose judge (flagged, `plan_ahead.md` item 13, unresolved).

### CI/CD (GitHub Actions)
**Required?** Yes.
**Working?** ✅ **[VERIFIED]** — `eval.yml` was broken (missing entry script, wrong judge credentials), fixed this session, live-tested against the real golden dataset.
**Verdict: Real gap still open** — `ci.yml` excludes `tests/test_e2e.py` and *all* of `tests/test_rag_pipeline.py`, including cheap Layer-1 sanity tests that need no LLM call and could run on every PR for near-zero cost. Highest-value-per-effort CI improvement still on the table.

---

## Containerization

### Docker + docker-compose
**Required?** Yes — the only way this project runs reproducibly for anyone but its author.
**Working?** ✅ **[LIVE]** — Docker Desktop and all 3 core containers (Postgres, Redis, Qdrant) were started, verified ready, and used for real testing this session.
**Verdict: Keep.** Right-sized — see the tech-fit assessment for why Kubernetes/load-balancing on top of this would be premature at current traffic.

---

## Summary — what actually needs action, ranked

1. **Composio** — the notification stub, if the feature matters, needs a real fix (currently sends every user's notification to one hardcoded destination).
2. **Delete `WealthOSWorkflow`** — dormant Temporal pipeline mirror, zero realized value, real maintenance cost.
3. **Mem0 vs `user_analyses`** — genuine architectural decision still open, not a quick fix.
4. **Consolidate `calculator_server` into a Postgres-domain MCP server** — real overhead for zero isolation benefit on pure-math tools.
5. **Promote cheap RAG sanity tests into `ci.yml`** — highest value-per-effort CI fix available.
6. **Clean up dead pgvector references** — offered twice already, still not done.
7. **Rename `guardrails/`** — cosmetic but actively misleading.
8. **Periodically re-verify "optional, fails-open" keys aren't silently dead** — this happened twice this session (Groq model IDs, Firecrawl key) with graceful-degradation code masking it until directly tested.

Everything not listed above passed the audit as-is: required, working, and the right tool for this project's actual scale — not because nothing was questioned, but because the alternatives were checked and didn't hold up.
