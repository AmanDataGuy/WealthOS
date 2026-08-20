# WealthOS — Technology Fit Assessment

**Date:** 2026-08-19
**Question asked:** of Docker, Kubernetes, caching, load balancing, rate limiting, Redis, Kafka, DB indexing, sharding, CI/CD, LLM fine-tuning, RAG, context engineering, MCP, CoT, token optimization, MoE, prompt chaining, authentication, authorization, JWT, rate limiting, load balancing, caching(Redis), websockets, Docker, API gateway, DB indexing, SSL/TLS, CORS, SQL injection prevention, RBAC, ABAC, API keys, DB migrations, reverse proxy, system design, git, cloud, distributed systems, clean architecture — what actually fits this project, and what's premature or wrong for its scale?

**Ground truth for "this project's scale":** a single-service personal-finance demo/portfolio app. One FastAPI backend, one Streamlit frontend, Postgres + Redis + Qdrant, deployable via `docker-compose up` on one host. Real traffic today: one developer's local testing, not production users at any volume. Every recommendation below is calibrated against that, not against "what would a hyperscale fintech need" — the two are genuinely different questions, and conflating them is how projects like this end up with Kubernetes manifests for an app three people have ever run.

The list had several duplicate entries (rate limiting, load balancing, caching/Redis, Docker, DB indexing each appear twice) — each is covered once below.

---

## Already in use, and genuinely the right call

### RAG
Core to the product, not a bolt-on. Hybrid dense+sparse retrieval with server-side RRF fusion in Qdrant, Cohere cross-encoder reranking, parent/child hierarchical chunking, and a staleness-decay layer on top (verified this session: 725 pre-existing SEC-filing points, plus 58 real earnings-call chunks I indexed and confirmed retrievable). This is the single most sophisticated piece of engineering in the codebase and it's appropriately central.

### Context engineering
What the RAG staleness system, the writer's source trust-hierarchy rules ("live earnings > analyst consensus > 10-K guidance > 10-K historical"), and the parent/child chunk retrieval *are*, functionally, even though the codebase doesn't use that label. This is a genuine strength — the project thinks carefully about what context the LLM sees and why, not just "stuff everything into the prompt."

### Prompt chaining
The entire LangGraph 8-node pipeline is prompt chaining with structure — router feeds finance, finance+research feed risk+code, everything feeds the writer. This is already the architecture, done as a proper DAG with real parallelism (`asyncio.gather`) rather than a flat sequence of calls.

### MCP
7 tool servers, 45 tools, genuine stdio subprocess transport (verified earlier this session — not a direct-import shortcut). Fits the project's positioning as a multi-agent tool-calling system. One caveat: applying the same subprocess-isolation ceremony to `calculator_server.py` (pure math, zero I/O) is real overhead for zero benefit — see the earlier critique. MCP itself is the right choice; its uniform application to every tool regardless of what the tool does is the one thing worth trimming.

### Docker
`Dockerfile.api` / `.frontend` / `.mcp` + `docker-compose.yml` bringing up Postgres, Redis, Qdrant, Temporal, API, and frontend. Exactly the right level of infrastructure-as-code for this project's scale — reproducible for a reviewer to run, no more than that.

### Redis (caching + now rate limiting)
Market-data/snapshot/macro-data TTL caching, and — as of this session — the sliding-window rate limiter. Redis is doing real, load-bearing work here, not decoration.

### Authentication, API keys, JWT
Now genuinely real (this session): bcrypt password hashing, HS256 JWTs verified per-request against the resource being accessed, an optional `X-API-Key` gate on the expensive `/analyze` endpoint. This is the right-sized auth model for a multi-user app with one role — not over- or under-built.

### SQL injection prevention
Already solid before this session — every query across `finance_server.py`, `portfolio_server.py`, `api/main.py` uses `asyncpg`'s parameterized `$1`/`$2` syntax, never string interpolation. Verified by reading every DB-touching file earlier this session. Nothing to add here.

### CORS
Already present — `CORSMiddleware` configured via an `ALLOWED_ORIGINS` env var in `api/main.py`. Correctly scoped, nothing to change.

### Git
In use as expected; no project-specific gap here.

---

## Would genuinely help — worth adding, right-sized for this project

### Database migrations
**Real gap.** Right now: `scripts/init_db.sql` run manually once, plus scattered `CREATE TABLE IF NOT EXISTS` calls at API startup for `users`/`analysis_history` specifically. Nothing tracks schema *changes* over time. This session found a live symptom of exactly that gap: `/portfolio/{user_id}`'s query references a `target_weight` column that doesn't exist in the real table — a schema drifted silently because nothing enforces migrations are applied consistently. **Alembic** (the standard for SQLAlchemy-adjacent/asyncpg Python stacks) is the right-sized fix — not a heavyweight platform change, just tracked, versioned, apply-in-order schema changes. Directly justified by a bug found this session, not speculative.

### Reverse proxy / API gateway (light version)
Not currently used — `uvicorn` is hit directly. The moment this is exposed beyond `localhost`, an **Nginx reverse proxy** in front of it (TLS termination, serving the Streamlit static assets, a single public port instead of two) is standard, low-effort production hygiene — not "API gateway" in the Kong/Apigee enterprise sense (that would be overkill), just the basic reverse-proxy layer every deployed web service has. Fits naturally alongside...

### SSL/TLS
...which reverse proxy work would deliver. Not needed for local dev; needed the moment this is deployed anywhere reachable over the internet. Right-sized as "Nginx + Let's Encrypt on one box," not a certificate-management platform.

### CI/CD (closing the existing gaps, not building new)
Partially real already — `ci.yml` runs on every PR, and `eval.yml`'s DeepEval gate was fixed this week (was calling a script that didn't exist). But `ci.yml` still explicitly excludes `tests/test_e2e.py` and *all* of `tests/test_rag_pipeline.py`, including its cheap, no-LLM-call Layer 1 sanity tests that could plausibly run on every PR for near-zero cost. This is the highest-value-per-effort CI improvement available: promote those specific cheap tests into the gated suite.

### Websockets (or at minimum real SSE)
**Genuine, specific gap.** `/analyze/stream` currently runs the *entire* pipeline to completion first, then drip-feeds the finished text back — it fakes streaming rather than doing it. The UI already has an "Agent log" feature showing which node just finished (`Router Node ✅`, `Finance Agent ✅`, ...) — a real websocket or Server-Sent-Events channel that pushes those exact same log lines to the frontend *as each node actually completes* would be a real UX upgrade that fits a feature this project already has and clearly cares about (progress transparency during a 45-90s wait). This is a good, scoped websocket use case — not "add websockets because it's a modern stack item."

### Token optimization
Partially present already (horizon-based routing skips unneeded work; the router and DSPy compilation deliberately use the cheaper `gpt-oss-20b` model) but there's demonstrated room to go further — this session's own DeepEval testing hit `openai/gpt-oss-120b`'s 8,000 TPM free-tier ceiling repeatedly, a very concrete signal that tighter prompt sizing / more aggressive context trimming / prompt caching would have immediate, measurable value, not theoretical value.

### DB indexing (a real pass, not a new topic)
Some indexing already exists (`idx_portfolio_holdings_user` on `user_id`, seen in `scripts/init_db.sql`), but there's no evidence of a systematic audit — every `{user_id}`-scoped lookup across 11 tables should have a matching index, and that hasn't been explicitly verified end to end. Worth a dedicated pass, but it's a checklist task, not a design decision.

---

## Doesn't fit at this project's actual scale — would be premature, actively wrong to add now

### Kubernetes
Wrong tool for a single-service app on `docker-compose`. K8s earns its complexity when you have multiple services that need independent scaling, rolling deploys across many replicas, and a team operating a cluster — none of which describes this project. Adopting it now would add far more operational surface area (manifests, an ingress controller, cluster management) than the project has traffic to justify. Explicit recommendation: **do not adopt** until there's a real multi-instance scaling need, which would itself first require load actually exceeding what one instance handles.

### Load balancing
Same reasoning, more specifically — there's nothing to balance across. One API instance, one traffic source. This only becomes relevant *after* the app is deployed and *after* it needs more than one instance, which hasn't happened.

### Sharding
Applies to a datastore once it's outgrown a single node's capacity — many millions of rows/vectors, multi-terabyte scale. Postgres here holds demo-scale data (per earlier verification, e.g. one seeded portfolio holding); Qdrant holds ~800 chunks. Neither is within orders of magnitude of needing sharding. Recommend not designing for this at all right now — it would be optimizing for a problem the project doesn't have.

### Kafka
No event-streaming use case exists in this app's actual design — it's a request/response pipeline (a user asks a question, the pipeline runs, a memo comes back), not an event-driven system with multiple independent consumers reacting to a stream. Worth naming explicitly: **this project already tried this and walked it back** — `plan_ahead.md`'s own audit history notes `docker-compose.yml` referenced Kafka/Zookeeper containers that were never actually wired to any consumer, and they were removed. Re-adding it now would repeat a mistake the project already made and corrected once.

### Distributed systems (as literal multi-node architecture)
The project already benefits from distributed-systems *thinking* — async parallelism via `asyncio.gather`, the MCP subprocess model, retry/reconnect logic in `MCPClient` — without literally *being* a distributed system (multi-host, multi-region, consensus protocols). That's the right level for now. Becoming an actual distributed system is a consequence of needing horizontal scale, which circles back to the load-balancing/K8s point above: not needed until traffic demands it.

### MoE (Mixture of Experts)
This is a *model architecture* concept — something the LLM provider (or a from-scratch model trainer) implements inside the model, not something an application calling a hosted API can adopt. Groq's `openai/gpt-oss-120b` may or may not use MoE internally; that's entirely opaque to and irrelevant for this codebase. There's no action to take here at the application layer — it doesn't apply.

### LLM fine-tuning
The project already made the *better* choice available at this scale: **DSPy prompt compilation** (`BootstrapFewShot`) instead of fine-tuning. Fine-tuning needs a training data pipeline, GPU infrastructure or a fine-tuning API, ongoing retraining as requirements shift, and meaningfully more engineering investment — for a task (writing a structured, well-cited memo) where good few-shot examples already do the job, as this session's own analysis confirmed (the compiled prompt embeds real graded examples directly). Recommend explicitly: **do not pursue fine-tuning**; DSPy compilation is the right-sized tool the project already chose, and switching to fine-tuning would be a strict downgrade in iteration speed for this use case.

### RBAC / ABAC
The application has exactly one user role today — there's no admin, analyst, or read-only viewer distinction anywhere in the code. The JWT-subject check built this session ("is this request's token for the same user_id being accessed") is a correctly-scoped authorization model for a single-role app. **RBAC** would become relevant the moment a second role exists (e.g., an admin dashboard viewing all users' data) — worth planning for, not worth building speculatively. **ABAC** (attribute-based, policy-language-driven authorization) is enterprise-scale machinery for organizations with complex, many-dimensional access rules; nothing about a personal-finance app with one user type calls for it. Recommend: neither now; RBAC if/when a second role is actually introduced.

---

## Present already, different shape than the label suggests

### CoT (Chain of Thought)
Not implemented as literal "let's think step by step" prompting, but the same underlying idea shows up structurally and arguably better: the Risk Agent's 3-node debate (macro analyst + stock analyst in parallel, then a scorer that synthesizes both) is explicit multi-agent reasoning decomposition, and the DeepEval GEval metrics deliberately put an `explanation` field before the boolean verdict to force reasoning-before-conclusion. This is CoT's actual goal (make the model reason before answering) achieved via agent decomposition rather than a single verbose chain — a reasonable, arguably stronger, alternative implementation already in place.

### Clean architecture
Partially achieved, with specific, real violations already identified and worth naming plainly rather than glossing over: the `agents/` / `graph/` / `mcp_servers/` / `rag/` layering is a genuine separation of concerns, but this session found and fixed one real violation (the Groq model string duplicated across 5+ files instead of one source of truth) and the earlier critique found others still open (two different DB-connection idioms that existed until this session fixed one of them; the ReAct tool dispatch in `query_engine.py` parsing raw text instead of using the structured-output pattern already proven elsewhere in the same codebase). "Clean architecture" isn't a single feature to add — it's an ongoing discipline, and this project is midway there: real layering exists, but a few single-source-of-truth violations still need closing (tracked in `plan_ahead.md` Phase 7, items 7-9, 12).

---

## Bottom line

The project's actual gaps cluster around **operational hygiene at the scale it's already at** — database migrations, a reverse proxy + TLS for when it's deployed, closing out the CI test-exclusion gaps, real streaming instead of faked streaming — not around adopting distributed-systems/hyperscale infrastructure it has no present need for. The single clearest anti-pattern to avoid is the one the project already lived through once (Kafka, added speculatively, never wired to anything, later removed) — every item flagged "doesn't fit" above is a specific way to repeat that same mistake with a different piece of infrastructure.
