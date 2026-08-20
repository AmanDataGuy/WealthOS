# WealthOS — Change Report

**Session date:** 2026-08-19
**Commits:** `29a3468..00407ab` on `main`, pushed to GitHub
**Scope:** README architecture diagram + fact-check → live pipeline outage found & fixed → DeepEval CI gate implemented → earnings call transcript indexing built

This report covers four related pieces of work, done in sequence, each verified against the real codebase or real live API calls rather than assumed. Where a claim below says "verified," it means an actual command was run and its output is what's described — not an inference from reading code.

---

## Commit 1 — `e79238a` — docs: restore mermaid architecture diagram, fact-check README

**What triggered this:** the README's architecture section had been simplified to a plain ASCII box diagram in an earlier pass; asked to revert to the detailed color-coded mermaid flowchart and re-verify the surrounding claims against the actual code.

### Change
- Restored the mermaid `flowchart LR` diagram (input → FastAPI → 5 MCP server groups → 8 agents → LangGraph orchestrator → RAG/memory/Temporal → storage → output), color-coded by layer via `classDef`.

### Fact-check corrections applied (verified against source, not the old README text)
| Claim | Was | Now | Verified via |
|---|---|---|---|
| Finance Agent anomaly detection | "z-score anomaly detection (σ = 2.0)" | "Ratio-based spending anomaly detection (current month > 2× category average)" | Read `mcp_servers/finance_server.py::analyze_spending()` — it's `avg > 0 and current > avg * 2`, not a z-score |
| Rebalancing Agent threshold | "5% drift threshold" | "Flags any sector exceeding 40% of portfolio value" | Read `mcp_servers/portfolio_server.py::get_allocation()` — `concentration_warning = top_sector_pct > 40` |
| DeepEval CI gate roadmap status | "✅ Done" | "🔄 Workflow defined, entry script pending" (later flipped back to Done once fixed in commit 3) | Read `.github/workflows/eval.yml` — it called `eval/run_deepeval.py`, which didn't exist anywhere in the repo |
| "LangSmith custom evaluators" roadmap line | claimed `langsmith_evaluators.py` with 3 named evaluators | replaced with what actually exists: the 4-metric structured LLM-judge in `eval/evaluate.py` | Grepped repo — no file named `langsmith_evaluators.py` exists |
| BSE indexer roadmap status | "🔄 Planned (30 companies)" | "✅ Done (29 companies)" | `rag/bse_indexer.py` already exists and is functional; counted `BSE_SCRIP_MAP` via AST parse — 29 entries, not 30 (docstring was off by one) |
| `GEMINI_API_KEY` required-vars row | listed as "DeepEval CI judge" | removed (nothing in the codebase reads it) | Grepped for `GEMINI_API_KEY` usage — zero `.py` files reference it |

**Not caught as wrong, kept as-is:** `user_analyses` Qdrant collection claimed "read + write" — this was double-checked against `graph/nodes.py::_get_past_decisions()` and found genuinely correct (a dual retrieval path: semantic search on the query text + exact ticker-filter scroll), correcting an earlier, less careful pass that had wrongly called it "write-only."

---

## Commit 2 — `eaa3bd2` — fix: swap deprecated Groq model IDs for live models

**This was the most consequential finding of the session.** While smoke-testing the (at that point still broken) DeepEval script, every single Groq API call failed with `model_not_found`. Rather than assume this was a fluke, queried Groq's live `/v1/models` endpoint directly with the project's real API key.

### Finding
`llama-3.3-70b-versatile` and `llama-3.1-8b-instant` — the two models referenced everywhere in the codebase — **do not exist on Groq's API anymore**. They've been retired. This meant **the entire live pipeline was broken end to end**: every agent call (finance classification, research synthesis, risk debate, DCF/code generation, writer) would have failed on every single request.

Groq's current model list (confirmed live): `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.6-27b`, `groq/compound`, `groq/compound-mini`, plus some audio/guard models — no `llama-3.x` family remains.

### Fix
Replaced across **11 files**:
- Primary/reasoning role (`llama-3.3-70b-versatile`) → `openai/gpt-oss-120b`
- Fast/cheap classification role (`llama-3.1-8b-instant`) → `openai/gpt-oss-20b`

Files touched: `services/llm_client.py`, `agents/data_agent.py`, `agents/risk_agent.py`, `agents/router_agent.py`, `agents/writer_agent.py`, `workflows/morning_briefing.py`, `observability/weave_config.py`, `eval/evaluate.py`, `eval/ragas_eval.py`, `eval/dspy_optimizer.py`, `.env.example`.

Also fixed:
- Stale per-token cost constants in `llm_client.py` (`$0.05`/`$0.08` per 1M → `$0.15`/`$0.60` per 1M, matching `openai/gpt-oss-120b`'s actual published Groq pricing — confirmed via web search, [cloudzero.com](https://www.cloudzero.com/blog/groq-pricing/))
- A leftover "DeepSeek R1" comment/log-line in `risk_agent.py` that no longer matched the model actually being called

### Verification
- Queried Groq's `/v1/models` with the real project key — confirmed the old models are absent, the new ones present.
- Sent a real chat completion to both `openai/gpt-oss-120b` and `openai/gpt-oss-20b` — both returned `200`.
- Confirmed `eval/compiled_writer.json` (the DSPy-compiled prompt) stores only text examples, no embedded model config — so it needed no recompilation after this swap.

### Known follow-on friction (documented, not silently hidden)
`openai/gpt-oss-120b`'s free tier caps at **8,000 TPM** (tighter than the old model's 12,000). Running many judge-metric calls back to back on one memo can exceed that. It's also a reasoning-style model that sometimes wraps output in reasoning tokens that DeepEval's plain-JSON-parsing prompt can't always extract cleanly — this surfaces as "Evaluation LLM outputted an invalid JSON" on some metric calls. Both are noted directly in `eval/run_deepeval.py`'s comments rather than glossed over; see the mitigation in commit 3.

---

## Commit 3 — `86ef8d4` — eval: add missing DeepEval CI gate script, fix Groq credentials

### Finding
`.github/workflows/eval.yml` was configured to run `python eval/run_deepeval.py --limit 5` on every PR touching writer/eval code — but **that file did not exist anywhere in the repository.** The workflow would fail immediately on its first real trigger. Compounding it, the workflow installed `langchain-google-genai` and provisioned `GEMINI_API_KEY`/`GOOGLE_API_KEY` secrets, but the only judge actually implemented in `eval/deepeval_metrics.py` is `GroqJudge` (Groq-only) — the provisioned credentials didn't match what the (missing) script would have needed even if it existed.

### Fix
- **Wrote `eval/run_deepeval.py`** — the missing entry point. Loads the golden dataset, scores each entry against all 9 metrics from `eval/deepeval_metrics.py` (8 LLM-judged + 1 deterministic `VerdictConsistencyMetric`), writes `eval/results/deepeval_{date}.json` in the exact shape the workflow's pass-rate check expects. Mirrors the conventions already established in `eval/evaluate.py`.
- **Fixed `eval.yml`**: swapped `langchain-google-genai` + Gemini env vars for `langchain-groq` + `GROQ_API_KEY`, matching the real judge implementation.
- **Added rate-limit pacing**: a 30-second sleep between each of the 9 metric calls per example (matches the math: 8,000 TPM ÷ ~4,500 tokens/call ≈ needs ~30s spacing), with an explicit code comment naming both this and the separate JSON-parsing quirk as known, understood limitations rather than silent gaps.
- **Bumped the workflow timeout** from 15 to 40 minutes to accommodate the added pacing (9 metrics × 5 examples × 30s ≈ 22.5 min minimum, plus real call latency).
- **Added `eval/results/` to `.gitignore`** (per-run artifacts, not source).
- Fixed two stale docstrings found in the process: `deepeval_metrics.py`'s metric table and `tests/test_deepeval.py`'s `test_verdict_consistency` docstring both described the wrong threshold (`risk_score >= 6` for a BUY-verdict failure) — the actual code checks `risk_score >= 9`. Also corrected a stale "15 golden dataset entries" reference; the real dataset has 28.

### Verification
- Syntax-checked the new script and the YAML.
- **Ran it three times against the real golden dataset with a real Groq key**, iterating based on what actually happened:
  1. First run (no pacing): confirmed `model_not_found` was gone entirely — the swap from commit 2 works. Surfaced the TPM/JSON-parsing friction described above.
  2. Increased pacing from 5s → 30s based on the actual TPM math from the observed rate-limit error messages.
  3. Confirmed the script completes, produces a valid `eval/results/deepeval_{date}.json`, and correctly computes `overall_pass` per entry.

---

## Commit 4 — `00407ab` — feat: index earnings call transcripts, wire router fetch_plan flag

### Finding
`agents/router_agent.py` has set `fetch_plan["use_earnings_transcript"] = horizon in ("short", "mid")` for as long as that function has existed — but grepping the entire codebase for `use_earnings_transcript` showed it was **read nowhere**. A fully dead flag: the router computed it, nothing downstream ever consumed it.

### What was built
**`rag/earnings_indexer.py`** (new) — finds and indexes the latest earnings call transcript for a ticker:
- `find_transcript_url()` — calls Firecrawl's `/v1/search` endpoint with `site:fool.com/earnings/call-transcripts {ticker}`, returns the first matching real transcript URL. Uses the same `FIRECRAWL_API_KEY` already configured for `news_server.py`'s Reddit-sentiment feature — no new API key needed.
- `_extract_transcript_body()` — isolates the real dialogue from a scraped page's markdown, trimming everything before `## Full Conference Call Transcript` (site chrome, ticker widgets) and everything from `## Read Next` onward (related-content boilerplate).
- `index_earnings_call()` — orchestrates search → scrape → extract → index, returns the chunk count.

**`rag/indexer.py`** (refactored) — `index_filing()`'s embed/upsert tail (clear old chunks → embed in batches → build Qdrant points → upsert) was extracted into a shared `_embed_and_upsert()` helper, and a new `index_text()` method added so raw scraped text can go through the same hierarchical-chunking pipeline as a file on disk, without duplicating ~60 lines of embed/upsert logic.

**`scripts/index_earnings_calls.py`** (new) — CLI batch indexer, mirrors the existing `scripts/index_indian_stocks.py` pattern.

**`agents/research_agent.py`** — `run_research_agent()` gained a `fetch_plan` parameter; when `fetch_plan["use_earnings_transcript"]` is set, a new `ensure_earnings_transcripts_indexed()` function checks whether each symbol already has `EARNINGS_CALL` chunks in Qdrant and, if not, fires on-demand indexing as a background `asyncio.create_task` — same non-blocking pattern as the router's existing on-demand SEC-filing indexing. The current request never waits on it; the *next* request benefits.

**`graph/nodes.py`** — `research_node` now passes `state.get("fetch_plan")` through to `run_research_agent`, closing the loop from router → research agent → indexer.

**`tests/test_rag_pipeline.py`** — two new deterministic tests (no network, no LLM) verifying `_extract_transcript_body()` correctly isolates real content and falls back safely if the page structure ever changes. Both pass.

### Blocker encountered and resolved mid-session
The `FIRECRAWL_API_KEY` initially in `.env` returned `401 Unauthorized` against Firecrawl's real API — meaning the scraping approach couldn't be verified against real data, and (as a side discovery) the *existing* Reddit-sentiment feature in `news_server.py` was silently broken by the same bad key. Paused the feature entirely rather than writing unverified scraping code against a guessed page structure. The user provided a working key; work resumed and every subsequent step was verified live before being written.

### Verification (all live, all real data, run in this session)
1. **Search discovery** — `find_transcript_url("NVDA", "Nvidia")` and `find_transcript_url("AAPL", "Apple")` both returned real, correct fool.com URLs.
2. **Extraction correctness** — scraped the real NVDA Q1 FY2027 transcript page (60,820 chars total), confirmed `_extract_transcript_body()` isolates exactly the 8,072-word dialogue, correctly excluding the page's navigation/ticker-widget chrome and the "Read Next"/"Premium Investing Services" footer.
3. **Chunking** — fed the real extracted text through `build_hierarchical_chunks()`: produced 1 parent chunk + 57 child chunks, each tagged with a sensible `info_type`/`half_life_days`.
4. **Full pipeline, end to end** — started Docker Desktop and the `wealthos-qdrant` container (neither was running at session start), ran `index_earnings_call("NVDA", "Nvidia")` for real: **58 chunks indexed successfully.**
5. **Storage confirmed** — queried Qdrant directly (`POST /collections/wealthos_docs/points/count` filtered by `ticker=NVDA, form_type=EARNINGS_CALL`) — **count: 58**, exactly matching.
6. **Retrieval confirmed** — ran a real hybrid search for *"What did management say about data center revenue growth?"* against the now-populated collection: **3 of the top 5 results were the newly-indexed earnings-call chunks**, with real, on-topic content ("Data center revenue of $75 billion was up 92% year over year...").

This is the one piece of work this session verified genuinely end-to-end, from a cold "nothing indexed" state through to confirmed retrieval — not just unit-level checks.

---

## Documentation updates bundled into the above
- `README.md`: LLM model references updated (`openai/gpt-oss-120b`), DeepEval CI gate and BSE/earnings-call roadmap rows corrected to match real status, `FIRECRAWL_API_KEY` added to the required-env-vars table.
- `.env.example`: model reference updated, `GEMINI_API_KEY` comment corrected to state it's currently unused (not "free eval judge" — that claim became false once the CI gate was fixed to use Groq), `FIRECRAWL_API_KEY` comment expanded to mention its second use case.
- `.gitignore`: added `eval/results/` (per-run artifacts) and `INTERVIEW_DOSSIER.md` (local-only doc, per explicit request).

## What was explicitly *not* done
- The residual DeepEval rate-limit/JSON-parsing friction (commit 3) is mitigated (30s pacing, 40-min timeout) but not eliminated — a genuinely flaky metric call is still possible on a bad run. This is documented in code comments, not hidden.
- `GROQ_API_KEY` needs to be added as a **GitHub Actions repository secret** for the DeepEval CI gate to actually run in GitHub's CI — that's an external, one-time setup step on GitHub's side that I can't perform (documented in the README as a note under the required-env-vars table).
- No changes were made to the two other known-weak areas surfaced during the earlier fact-check pass (unsigned-cookie auth, in-memory-only rate limiting) — out of scope for this session's requests.
