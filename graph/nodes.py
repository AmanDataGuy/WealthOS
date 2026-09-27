# graph/nodes.py
"""
One async function per agent.
Each node pulls what it needs from state, calls the agent, writes result back.
Errors are caught per-node so one failure doesn't kill the whole pipeline.

## Phase 5 addition
`validation_node` added between risk_and_code and rebalancing. It now
actually gates the pipeline: a failed check routes to `error_node` instead
of just logging a warning and continuing.

## Phase 6 addition
`finance_node` now reads Mem0 memory at the start.
`writer_node` now writes results to Mem0 at the end.
"""

import os
import time
import asyncio
from agents.data_agent        import run_data_agent
from agents.risk_agent        import run_risk_agent
from agents.code_agent        import run_code_agent
from agents.rebalancing_agent import run_rebalancing_agent, NewInvestment
from agents.writer_agent      import run_writer_agent
from agents.tax_agent         import run_tax_agent, involves_taxable_decision
from graph.state              import WealthOSState
from validation.validators     import validate_all, validate_memo
from observability.langsmith_config import trace_node
from harness.risk_policy       import validate_recommendation, PolicyDecision


# ── Helper: past decisions retrieval ──────────────────────────────────────────

def _embed_text(text: str) -> list:
    from sentence_transformers import SentenceTransformer
    m = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    return m.encode(text, normalize_embeddings=True).tolist()


async def _get_past_decisions(user_id: str, ticker: str, query: str = "") -> str:
    """
    Retrieve relevant past decisions from Qdrant user_analyses.

    Two retrieval paths, merged:
    1. Semantic search embedding the user's actual QUESTION (not just the
       current ticker) — so "what did you tell me about GOOGL" while
       analyzing MSFT searches for GOOGL-relevant history, not MSFT's.
    2. Exact ticker-filter lookup for any other ticker symbols explicitly
       named in the question text — semantic search alone isn't reliable
       enough to guarantee recall when a specific ticker is named outright.
    """
    try:
        import os, re, asyncio as _asyncio
        from qdrant_client import QdrantClient
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        qc = QdrantClient(url=os.getenv("QDRANT_URL", "http://localhost:6333"))

        search_text = query.strip() or ticker or "general"
        vec = await _asyncio.to_thread(_embed_text, search_text)
        semantic_results = qc.search(
            collection_name="user_analyses",
            query_vector={"name": "dense", "vector": vec},
            query_filter=Filter(must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]),
            limit=3,
            with_payload=True,
        )

        mentioned = set(re.findall(r"\b[A-Z]{2,5}(?:\.[A-Z]{2})?\b", query.upper()))
        mentioned.discard((ticker or "").upper())
        exact_results = []
        for t in list(mentioned)[:3]:
            hits, _ = qc.scroll(
                collection_name="user_analyses",
                scroll_filter=Filter(must=[
                    FieldCondition(key="user_id", match=MatchValue(value=user_id)),
                    FieldCondition(key="ticker", match=MatchValue(value=t)),
                ]),
                limit=2,
                with_payload=True,
            )
            exact_results.extend(hits)

        seen  = set()
        lines = []
        for r in list(exact_results) + list(semantic_results):
            p   = r.payload
            key = (p.get("ticker"), p.get("analysis_date"))
            if key in seen:
                continue
            seen.add(key)
            lines.append(
                f"- {p.get('analysis_date','?')}: {p.get('ticker','?')} "
                f"→ {p.get('verdict','?')}: {p.get('verdict_text','')[:120]}"
            )
        return "\n".join(lines[:5])
    except Exception:
        return ""


# ── Helper: user_risk_profiles upsert ─────────────────────────────────────────

async def _upsert_risk_profile(user_id: str, verdict: str, risk_score: float, sector: str):
    try:
        import asyncpg, os
        db_url = os.getenv("WEALTHOS_DB_URL", "").replace("postgresql+asyncpg://", "postgresql://")
        if not db_url:
            return
        conn = await asyncpg.connect(db_url)
        await conn.execute(
            """
            INSERT INTO user_risk_profiles
                (user_id, total_analyses, buy_count, hold_count, avoid_count, preferred_sectors, last_updated_at)
            VALUES ($1, 1, $2, $3, $4, ARRAY[$5], NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                total_analyses = user_risk_profiles.total_analyses + 1,
                buy_count   = user_risk_profiles.buy_count   + $2,
                hold_count  = user_risk_profiles.hold_count  + $3,
                avoid_count = user_risk_profiles.avoid_count + $4,
                preferred_sectors = CASE
                    WHEN $5 = ANY(user_risk_profiles.preferred_sectors) THEN user_risk_profiles.preferred_sectors
                    ELSE array_append(user_risk_profiles.preferred_sectors, $5)
                END,
                last_updated_at = NOW()
            """,
            user_id,
            1 if verdict and verdict.upper() == "BUY" else 0,
            1 if verdict and verdict.upper() == "HOLD" else 0,
            1 if verdict and verdict.upper() == "AVOID" else 0,
            sector or "Unknown",
        )
        await conn.close()
    except Exception as e:
        print(f"  [profile] ⚠️  user_risk_profiles upsert failed: {e}")


def log(state: WealthOSState, msg: str) -> list[str]:
    messages = state.get("messages", [])
    messages.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    return messages


# ── Router Node ───────────────────────────────────────────────────────────────

@trace_node("router_node")
async def router_node(state: WealthOSState) -> dict:
    print("\n[Graph] Router Node running...")
    ticker  = state["tickers"][0] if state.get("tickers") else "UNKNOWN"
    user_id = state.get("user_id") or "00000000-0000-0000-0000-000000000001"
    try:
        from agents.router_agent import run_router_agent
        result = await run_router_agent(
            query=state.get("query", ""),
            ticker=ticker,
            user_id=user_id,
            investment_horizon=state.get("investment_horizon"),
        )
        return {
            **result,
            "messages": log(state, f"Router Node ✅ horizon={result.get('investment_horizon')} tier={result.get('company_tier')}"),
        }
    except Exception as e:
        return {
            "investment_horizon": "long",
            "messages": log(state, f"Router Node ⚠️ {e} (defaulting to long)"),
        }


# ── Finance Node ───────────────────────────────────────────────────────────────
# Phase 6: reads Mem0 memory before doing anything else.
# The user_memory string flows into every downstream agent via state.

@trace_node("finance_node")
async def finance_node(state: WealthOSState) -> dict:
    print("\n[Graph] Finance Node running...")
    try:
        # Phase 6 — pull long-term memory for this user
        user_id = state.get("user_id") or "00000000-0000-0000-0000-000000000001"
        user_memory = ""
        memory_unavailable = False
        try:
            from memory.mem0_client import read_memory
            user_memory, memory_unavailable = read_memory(user_id, query=state.get("query", ""))
            if user_memory:
                print(f"  [mem0] Loaded memory for {user_id}")
            elif memory_unavailable:
                print(f"  [mem0] ⚠️  Memory read failed for {user_id} — proceeding without personalization history")
        except Exception as e:
            # Belt-and-suspenders — read_memory itself never raises, but
            # keep this in case that contract ever changes.
            print(f"  [mem0] ⚠️  Could not load memory: {e}")
            memory_unavailable = True

        # Actually run the Finance Agent instead of returning hardcoded data
        try:
            from agents.finance_agent import run_finance_agent
            snapshot = await run_finance_agent(user_id)
            personal_finance = snapshot.model_dump()
            print(f"  [finance] Agent returned: status={snapshot.status}, confidence={snapshot.data_confidence}")
        except Exception as e:
            print(f"  [finance] ⚠️  Agent failed ({e}), using minimal defaults")
            personal_finance = {
                "monthly_income":    0,
                "monthly_surplus":   0,
                "debt_burden_ratio": 0,
                "health_score":      {"overall": 50, "grade": "C"},
                "risk_capacity":     "unknown",
                "investable_monthly": 0,
                "goals": [],
                "anomalies": [],
                "data_confidence":   "none",
                "status":            "error",
                "message":           f"Finance Agent failed: {e}",
            }

        # Retrieve past decisions from Qdrant user_analyses collection
        past_decisions_ctx = ""
        try:
            past_decisions_ctx = await _get_past_decisions(
                user_id,
                state["tickers"][0] if state.get("tickers") else "",
                state.get("query", ""),
            )
            if past_decisions_ctx:
                print(f"  [past_decisions] Loaded {past_decisions_ctx.count(chr(10)) + 1} past analyses")
        except Exception as e:
            print(f"  [past_decisions] ⚠️  Could not load past decisions: {e}")

        memory_status = "yes" if user_memory else ("failed" if memory_unavailable else "none")
        return {
            "user_memory":         user_memory,
            "memory_unavailable":  memory_unavailable,
            "personal_finance":    personal_finance,
            "past_decisions_ctx":  past_decisions_ctx,
            "messages": log(state, f"Finance Node ✅ (confidence={personal_finance.get('data_confidence', 'unknown')}, memory={memory_status})"),
        }
    except Exception as e:
        return {
            "error": f"Finance Node failed: {e}",
            "messages": log(state, f"Finance Node ❌ {e}"),
        }


# ── Data Node ──────────────────────────────────────────────────────────────────

@trace_node("data_node")
async def data_node(state: WealthOSState) -> dict:
    print("\n[Graph] Data Node running...")
    ticker = state["tickers"][0] if state.get("tickers") else None
    if not ticker:
        return {"error": "No ticker provided", "messages": log(state, "Data Node ❌ no ticker")}
    try:
        snapshot = await run_data_agent(ticker, use_rag=True)
        return {
            "financial_snapshot": snapshot.model_dump(),
            "messages": log(state, f"Data Node ✅ {ticker} — confidence {snapshot.confidence}"),
        }
    except Exception as e:
        return {
            "error": f"Data Node failed: {e}",
            "messages": log(state, f"Data Node ❌ {e}"),
        }


# ── Research Node ──────────────────────────────────────────────────────────────

@trace_node("research_node")
async def research_node(state: WealthOSState) -> dict:
    print("\n[Graph] Research Node running...")
    ticker = state["tickers"][0] if state.get("tickers") else "Unknown"
    try:
        from agents.research_agent import run_research_agent
        user_id = state.get("user_id") or "00000000-0000-0000-0000-000000000001"
        snapshot = await run_research_agent(user_id, [ticker], fetch_plan=state.get("fetch_plan"))
        return {
            "research_output": snapshot.model_dump() if hasattr(snapshot, "model_dump") else {"summary": str(snapshot)},
            "messages": log(state, f"Research Node ✅ {ticker}"),
        }
    except Exception as e:
        return {
            "research_output": {"summary": f"Research unavailable: {e}"},
            "messages": log(state, f"Research Node ⚠️ {e} (continuing)"),
        }


# ── Risk Node ──────────────────────────────────────────────────────────────────

async def _fetch_user_risk_profile(user_id: str) -> dict | None:
    db_url = os.getenv("WEALTHOS_DB_URL", "")
    if not db_url:
        return None
    try:
        import asyncpg
        conn = await asyncpg.connect(db_url)
        try:
            row = await conn.fetchrow(
                "SELECT * FROM user_risk_profiles WHERE user_id = $1", user_id
            )
            return dict(row) if row else None
        finally:
            await conn.close()
    except Exception:
        return None


@trace_node("risk_node")
async def risk_node(state: WealthOSState) -> dict:
    print("\n[Graph] Risk Node running...")
    ticker   = state["tickers"][0] if state.get("tickers") else None
    snapshot = state.get("financial_snapshot")
    personal = state.get("personal_finance")
    user_id  = state.get("user_id") or "00000000-0000-0000-0000-000000000001"
    if not ticker:
        return {"messages": log(state, "Risk Node ❌ no ticker")}
    try:
        user_risk_profile = await _fetch_user_risk_profile(user_id)
        report = await run_risk_agent(
            ticker=ticker,
            financial_snapshot=snapshot,
            personal_finance=personal,
            past_decisions_ctx=state.get("past_decisions_ctx", ""),
            user_risk_profile=user_risk_profile,
        )
        return {
            "risk_report": report.model_dump(),
            "messages": log(state, f"Risk Node ✅ score={report.risk_score}/10 {report.recommendation}"),
        }
    except Exception as e:
        return {
            "error": f"Risk Node failed: {e}",
            "messages": log(state, f"Risk Node ❌ {e}"),
        }


# ── Code Node ──────────────────────────────────────────────────────────────────

@trace_node("code_node")
async def code_node(state: WealthOSState) -> dict:
    print("\n[Graph] Code Node running...")
    ticker   = state["tickers"][0] if state.get("tickers") else None
    snapshot = state.get("financial_snapshot")
    if not ticker:
        return {"messages": log(state, "Code Node ❌ no ticker")}
    try:
        result = await run_code_agent(
            ticker=ticker,
            financial_snapshot=snapshot,
            fetch_plan=state.get("fetch_plan"),
        )
        return {
            "code_output": result.model_dump(),
            "messages": log(state, f"Code Node ✅ DCF=${result.dcf.intrinsic_value:.2f}" if result.dcf else "Code Node ✅"),
        }
    except Exception as e:
        return {
            "error": f"Code Node failed: {e}",
            "messages": log(state, f"Code Node ❌ {e}"),
        }


# ── Validation Node ────────────────────────────────────────────────────────────

@trace_node("validation_node")
async def validation_node(state: WealthOSState) -> dict:
    print("\n[Graph] Validation Node running...")
    valid, error = validate_all(state)
    if valid:
        return {
            "validation_passed": True,
            "validation_issues": [],
            "messages": log(state, "Validation Node ✅ all agent outputs passed checks"),
        }
    else:
        # ponytail: validate_all returns a flat (bool, str) — no soft/hard
        # severity split. Its checks (missing financial data, out-of-range
        # risk_score, non Buy/Hold/Avoid recommendation, etc.) are all things
        # that would make rebalancing/writer operate on garbage, so treat
        # every failure as hard and route to error_node. If validate_all ever
        # grows genuinely cosmetic checks, give it a severity field instead of
        # inferring severity here.
        print(f"  [validation] ❌ {error}")
        return {
            "validation_passed": False,
            "validation_issues": [error],
            "error": error,
            "messages": log(state, f"Validation Node ❌ {error} (routing to error)"),
        }


# ── Rebalancing Node ───────────────────────────────────────────────────────────

@trace_node("rebalancing_node")
async def rebalancing_node(state: WealthOSState) -> dict:
    print("\n[Graph] Rebalancing Node running...")
    user_id = state.get("user_id") or "00000000-0000-0000-0000-000000000001"
    ticker  = state["tickers"][0] if state.get("tickers") else None

    new_inv  = None
    snapshot = state.get("financial_snapshot")
    if ticker and snapshot:
        sector  = snapshot.get("sector") or "Technology"
        amount  = state.get("invest_amount") or 20000.0
        new_inv = NewInvestment(ticker=ticker, amount=amount, sector=sector)

    try:
        suggestion = await run_rebalancing_agent(
            user_id=user_id,
            new_investment=new_inv,
            risk_report=state.get("risk_report"),
            risk_capacity=(state.get("personal_finance") or {}).get("risk_capacity"),
        )
        return {
            "rebalance_suggestion": suggestion.model_dump(),
            "messages": log(state, f"Rebalancing Node ✅ {len(suggestion.actions)} actions"),
        }
    except Exception as e:
        return {
            "rebalance_suggestion": None,
            "messages": log(state, f"Rebalancing Node ⚠️ {e} (continuing)"),
        }


# ── Writer Node ────────────────────────────────────────────────────────────────
# Phase 6: writes results to Mem0 after memo is complete.

@trace_node("writer_node")
async def writer_node(state: WealthOSState) -> dict:
    print("\n[Graph] Writer Node running...")
    ticker = state["tickers"][0] if state.get("tickers") else "Unknown"
    try:
        _writer_uid         = state.get("user_id") or "00000000-0000-0000-0000-000000000001"
        _user_risk_profile  = await _fetch_user_risk_profile(_writer_uid)

        memo = await run_writer_agent(
            ticker=ticker,
            financial_snapshot=state.get("financial_snapshot"),
            risk_report=state.get("risk_report"),
            code_output=state.get("code_output"),
            rebalance_suggestion=state.get("rebalance_suggestion"),
            personal_finance=state.get("personal_finance"),
            research_snapshot=state.get("research_output"),
            user_memory=state.get("user_memory", ""),
            memory_unavailable=bool(state.get("memory_unavailable")),
            investment_horizon=state.get("investment_horizon", "long"),
            past_decisions_ctx=state.get("past_decisions_ctx", ""),
            user_risk_profile=_user_risk_profile,
        )

        valid, error = validate_memo(memo.full_memo)
        if not valid:
            print(f"  [validation] ⚠️  Memo validation: {error}")

        # Mem0 write, Qdrant user_analyses indexing, and the risk-profile
        # upsert are all writes for a *future* run's benefit — nothing later
        # in *this* run (tax_node, policy_node, or the response itself) reads
        # them back. They used to be awaited/called synchronously here, which
        # meant every one of them sat directly on the response's critical
        # path. Fired as background tasks instead — same per-write error
        # handling, just not gating the memo the user is waiting on.
        _uid = state.get("user_id") or "00000000-0000-0000-0000-000000000001"

        async def _write_mem0():
            try:
                from memory.mem0_client import write_memory
                await asyncio.get_running_loop().run_in_executor(
                    None, write_memory, _uid, {**state, "final_memo": memo.full_memo}
                )
            except Exception as e:
                print(f"  [mem0] ⚠️  write failed: {e}")

        async def _index_qdrant():
            try:
                from rag.indexer import index_user_analysis
                _ticker = state["tickers"][0] if state.get("tickers") else "UNKNOWN"
                await index_user_analysis(
                    user_id=_uid,
                    ticker=_ticker,
                    verdict=memo.verdict or "Hold",
                    full_memo=memo.full_memo,
                    risk_score=float(memo.risk_score) if memo.risk_score is not None else None,
                )
            except Exception as e:
                print(f"  [indexer] ⚠️  user_analyses index failed: {e}")

        async def _upsert_profile():
            try:
                _sector = (state.get("financial_snapshot") or {}).get("sector", "Unknown")
                _risk_score = float(memo.risk_score) if memo.risk_score is not None else 5.0
                await _upsert_risk_profile(
                    user_id=_uid, verdict=memo.verdict, risk_score=_risk_score, sector=_sector,
                )
            except Exception as e:
                print(f"  [profile] ⚠️  risk profile upsert failed: {e}")

        for coro in (_write_mem0(), _index_qdrant(), _upsert_profile()):
            asyncio.create_task(coro)

        return {
            "final_memo": memo.full_memo,
            "memo_verdict": memo.verdict,
            "memo_risk_score": memo.risk_score,
            "messages": log(state, f"Writer Node ✅ {len(memo.full_memo)} chars — verdict: {memo.verdict}"),
        }
    except Exception as e:
        return {
            "error": f"Writer Node failed: {e}",
            "messages": log(state, f"Writer Node ❌ {e}"),
        }


# ── Tax Node ───────────────────────────────────────────────────────────────────
# agents/tax_agent.py — India tax regime comparison + 80C headroom. Appended
# as a post-processing step on the already-generated memo rather than fed
# into the DSPy-compiled writer prompt itself: the writer's compiled prompt
# and its 28-example golden dataset are tuned for a fixed 7-section shape,
# and retraining that to add an 8th section is a separate, larger piece of
# work than "wire up the already-built tax tools." This gets a real Tax
# Impact section into the memo today without touching DSPy or eval risk.

@trace_node("tax_node")
async def tax_node(state: WealthOSState) -> dict:
    print("\n[Graph] Tax Node running...")
    query = state.get("query", "")
    if not involves_taxable_decision(query):
        return {"messages": log(state, "Tax Node — query not tax-shaped, skipped")}

    personal_finance = state.get("personal_finance") or {}
    try:
        tax_context = await run_tax_agent(
            monthly_income=personal_finance.get("monthly_income", 0),
        )
    except Exception as e:
        # Never let a tax-calculation bug take down a memo that's otherwise
        # ready — same "errors are caught per-node" contract as every other
        # node in this file.
        print(f"  [tax] ⚠️  Tax Agent failed: {e}")
        return {"messages": log(state, f"Tax Node ❌ {e} (memo unaffected)")}

    if tax_context is None:
        return {"messages": log(state, "Tax Node — no income data to compute against, skipped")}

    regime   = tax_context["regime_comparison"]
    savings  = tax_context["tax_saving_suggestions"]
    section = (
        "\n\n## Tax Impact\n"
        f"- Recommended regime: **{regime['recommendation'].replace('_', ' ').title()}** "
        f"(saves ₹{abs(regime['tax_savings_with_new_regime']):,.0f}/year vs. the alternative)\n"
        f"- Estimated annual tax: old regime ₹{regime['old_regime']['total_tax']:,.0f} · "
        f"new regime ₹{regime['new_regime']['total_tax']:,.0f}\n"
        f"- Unused deduction headroom: ₹{savings['total_potential_tax_saving']:,.0f} in potential "
        f"savings across {len(savings['suggestions'])} sections (80C/80D/HRA/NPS)\n"
    )

    return {
        "tax_context": tax_context,
        "final_memo": (state.get("final_memo") or "") + section,
        "messages": log(state, "Tax Node ✅ Tax Impact section appended"),
    }


# ── Policy Node ────────────────────────────────────────────────────────────────
# harness/risk_policy.py — deterministic gate, no LLM call. The Writer Agent
# proposes a verdict; this is the only thing with authority to allow, deny,
# or escalate it before it reaches the user. Runs even after a Writer
# failure is impossible by construction: writer_node's except branch sets
# "error" and skips straight to error_node via the graph's error routing,
# so policy_node only ever sees a real memo.

@trace_node("policy_node")
async def policy_node(state: WealthOSState) -> dict:
    print("\n[Graph] Policy Node running...")
    verdict = state.get("memo_verdict")
    if not verdict:
        # Nothing to gate (writer_node errored — graph error routing already
        # sent that case to error_node, not here — this is just a safety net).
        return {"policy_status": "allow", "messages": log(state, "Policy Node — no memo to check, skipped")}

    personal_finance   = state.get("personal_finance") or {}
    financial_snapshot = state.get("financial_snapshot") or {}
    user_risk_profile  = await _fetch_user_risk_profile(state.get("user_id") or "00000000-0000-0000-0000-000000000001")

    result = validate_recommendation(
        verdict=verdict,
        risk_score=state.get("memo_risk_score"),
        invest_amount=state.get("invest_amount"),
        monthly_surplus=personal_finance.get("monthly_surplus"),
        data_confidence=financial_snapshot.get("confidence"),
        user_risk_profile=user_risk_profile,
    )

    if result.decision == PolicyDecision.ALLOW:
        return {
            "policy_status": "allow",
            "messages": log(state, "Policy Node ✅ recommendation allowed"),
        }

    banner = f"\n\n---\n\n> ⚠️ **Policy {result.decision.value.upper()}**: {result.reason}\n\n---\n"
    return {
        "policy_status": result.decision.value,
        "policy_reason": result.reason,
        "final_memo": (state.get("final_memo") or "") + banner,
        "messages": log(state, f"Policy Node ⚠️ {result.decision.value} — {result.reason}"),
    }


# ── Error Node ─────────────────────────────────────────────────────────────────

@trace_node("error_node")
async def error_node(state: WealthOSState) -> dict:
    print(f"\n[Graph] Error Node — {state.get('error')}")
    return {
        "final_memo": f"Analysis failed: {state.get('error', 'Unknown error')}. Please try again.",
        "messages": log(state, "Error Node — pipeline terminated"),
    }