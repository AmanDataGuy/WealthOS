# agents/writer_agent.py
"""
Writer Agent — Groq + DSPy
===========================

Synthesizes all agent outputs into a personalized investment memo.

## What changed in Phase 5
- At startup, checks if `eval/compiled_writer.json` exists.
- If yes: uses the DSPy-compiled prompt (one optimized call, all sections at once).
- If no: falls back to the original 7-section approach below. Nothing else changes.

## Memo structure
1. Executive Summary
2. Financial Snapshot
3. Valuation Analysis (DCF + Monte Carlo)
4. Risk Assessment
5. Portfolio Impact (Rebalancing)
6. Personal Finance Fit
7. Final Verdict

## LLM
# Uses Groq llama-3.3-70b for speed and quality.
"""

import os
import asyncio
import httpx
from typing import Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from services.llm_client import call_llm

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = "llama-3.3-70b-versatile"
COMPILED_PROMPT_PATH = "eval/compiled_writer.json"


# --- Check for compiled DSPy prompt at import time ---------------------------
# If the compiled prompt exists we use it. If not we use the hand-written one.
# This means no code change is needed after running the optimizer.

_COMPILED_PROGRAM = None

def _load_compiled_program():
    global _COMPILED_PROGRAM
    if not os.path.exists(COMPILED_PROMPT_PATH):
        return None
    try:
        import dspy
        from eval.dspy_optimizer import MemoWriter
        program = MemoWriter()
        program.load(COMPILED_PROMPT_PATH)
        lm = dspy.LM("groq/llama-3.3-70b-versatile", api_key=GROQ_API_KEY, max_tokens=1500, temperature=0.3)
        dspy.configure(lm=lm)
        print(f"  [writer] ✅ DSPy compiled prompt loaded from {COMPILED_PROMPT_PATH}")
        return program
    except Exception as e:
        print(f"  [writer] ⚠️  Could not load compiled prompt ({e}) — using hand-written fallback")
        return None

_COMPILED_PROGRAM = _load_compiled_program()


# ── Output Schema ─────────────────────────────────────────────────────────────

class InvestmentMemo(BaseModel):
    ticker:             str
    company_name:       Optional[str]  = None
    verdict:            str            # "Buy" | "Hold" | "Avoid"
    risk_score:         Optional[int]  = None

    # Memo sections
    executive_summary:  Optional[str]  = None
    financial_snapshot: Optional[str]  = None
    valuation_analysis: Optional[str]  = None
    risk_assessment:    Optional[str]  = None
    portfolio_impact:   Optional[str]  = None
    personal_fit:       Optional[str]  = None
    final_verdict:      Optional[str]  = None

    # Full memo as one string (for PDF export)
    full_memo:          Optional[str]  = None
    analysis_date:      str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ── Context Builders ──────────────────────────────────────────────────────────
# These convert agent outputs into concise strings for the LLM prompt.
# Handles both Pydantic objects and plain dicts gracefully.

def format_financial_snapshot(snapshot) -> str:
    if not snapshot:
        return "No financial data available."

    d = snapshot.model_dump() if hasattr(snapshot, "model_dump") else snapshot
    inc = d.get("income_statement", {})
    val = d.get("valuation", {})
    bal = d.get("balance_sheet", {})
    cf  = d.get("cash_flow", {})
    gr  = d.get("growth", {})

    lines = [f"**{d.get('company_name', d.get('ticker', 'N/A'))}** ({d.get('ticker', '')})"]
    lines.append(f"Sector: {d.get('sector', 'N/A')}")
    lines.append("")

    if inc.get("total_revenue"):
        lines.append(f"- Revenue (FY{inc.get('fiscal_year', 'N/A')}): **${inc['total_revenue']:,.0f}M**")
    if inc.get("net_income"):
        lines.append(f"- Net Income: **${inc['net_income']:,.0f}M**")
    if val.get("current_price"):
        lines.append(f"- Current Price: **${val['current_price']:.2f}**")
    if val.get("pe_ratio"):
        lines.append(f"- P/E Ratio: **{val['pe_ratio']:.1f}x**")
    if val.get("market_cap"):
        lines.append(f"- Market Cap: **${val['market_cap']/1e9:.1f}B**")
    if bal.get("total_debt"):
        lines.append(f"- Total Debt: **${bal['total_debt']:,.0f}M**")
    if cf.get("free_cash_flow"):
        lines.append(f"- Free Cash Flow: **${cf['free_cash_flow']:,.0f}M**")
    if gr.get("revenue_cagr_3y"):
        lines.append(f"- Revenue CAGR (3Y): **{gr['revenue_cagr_3y']:.1f}%**")

    return "\n".join(lines)


def format_risk_report(report) -> str:
    if not report:
        return "No risk assessment available."

    d = report.model_dump() if hasattr(report, "model_dump") else report

    lines = [
        f"- Risk Score: **{d.get('risk_score', 'N/A')}/10** ({d.get('risk_grade', 'N/A')})",
        f"- Recommendation: **{d.get('recommendation', 'N/A')}**",
        f"- Verdict: {d.get('final_verdict', 'N/A')}",
    ]

    factors = d.get("risk_factors", [])
    if factors:
        lines.append("\nKey Risk Factors:")
        for rf in factors[:3]:
            desc = rf.get("description", "") if isinstance(rf, dict) else rf.description
            sev  = rf.get("severity", "") if isinstance(rf, dict) else rf.severity
            lines.append(f"  - [{sev.upper()}] {desc[:100]}")

    adj = d.get("personal_adjustment")
    if adj:
        base     = adj.get("base_score") if isinstance(adj, dict) else adj.base_score
        adjusted = adj.get("adjusted_score") if isinstance(adj, dict) else adj.adjusted_score
        if base != adjusted:
            lines.append(f"\nPersonal adjustment: {base} → {adjusted}/10")

    return "\n".join(lines)


def format_code_output(code_output) -> str:
    if not code_output:
        return "No valuation models available."

    d = code_output.model_dump() if hasattr(code_output, "model_dump") else code_output

    lines = []
    dcf = d.get("dcf")
    mc  = d.get("monte_carlo")

    if dcf:
        lines.append(f"**DCF Intrinsic Value: ${dcf.get('intrinsic_value', 0):.2f}**")
        lines.append(f"- Current Price: ${dcf.get('current_price', 0):.2f}")
        upside = dcf.get('upside_downside', 0)
        direction = "upside" if upside > 0 else "downside"
        lines.append(f"- Implied {direction}: {abs(upside):.1f}%")
        lines.append(f"- WACC: {dcf.get('wacc_used', 0)*100:.1f}% | Growth: {dcf.get('growth_rate_used', 0)*100:.1f}%")

    if mc:
        lines.append(f"\n**Monte Carlo (1000 paths):**")
        lines.append(f"- Bear case (P10): ${mc.get('percentile_10', 0):.2f}")
        lines.append(f"- Base case (P50): ${mc.get('median_price', 0):.2f}")
        lines.append(f"- Bull case (P90): ${mc.get('percentile_90', 0):.2f}")
        lines.append(f"- Probability of gain: {mc.get('probability_upside', 0):.1f}%")

    return "\n".join(lines) if lines else "Valuation models ran but produced no output."


def format_rebalancing(suggestion) -> str:
    if not suggestion:
        return "No rebalancing analysis available."

    d = suggestion.model_dump() if hasattr(suggestion, "model_dump") else suggestion

    lines = [f"Portfolio Value: **${d.get('total_portfolio_value', 0):,.0f}**"]

    actions = d.get("actions", [])
    if actions:
        lines.append(f"\nRebalancing Actions ({len(actions)}):")
        for a in actions[:4]:
            act    = a.get("action", "") if isinstance(a, dict) else a.action
            sector = a.get("sector", "") if isinstance(a, dict) else a.sector
            amount = a.get("amount", 0) if isinstance(a, dict) else a.amount
            reason = a.get("reason", "") if isinstance(a, dict) else a.reason
            lines.append(f"  - {act.upper()} {sector}: ${amount:,.0f} — {reason[:80]}")
    else:
        lines.append("Portfolio is well balanced. No rebalancing required.")

    if d.get("new_investment_impact"):
        lines.append(f"\nNew Investment Impact: {d['new_investment_impact'][:200]}")

    return "\n".join(lines)


def format_personal_finance(personal) -> str:
    if not personal:
        return "No personal finance context provided."

    d = personal.model_dump() if hasattr(personal, "model_dump") else personal

    lines = []
    if d.get("monthly_income"):
        lines.append(f"- Monthly Income: ₹{d['monthly_income']:,.0f}")
    if d.get("monthly_surplus"):
        lines.append(f"- Monthly Surplus: ₹{d['monthly_surplus']:,.0f}")
    if d.get("debt_burden_ratio"):
        lines.append(f"- Debt Burden: {d['debt_burden_ratio']:.1%}")

    hs = d.get("health_score")
    if hs:
        score = hs.get("total") if isinstance(hs, dict) else hs
        grade = hs.get("grade") if isinstance(hs, dict) else ""
        lines.append(f"- Financial Health Score: **{score}/100** ({grade})")

    if d.get("risk_capacity"):
        lines.append(f"- Risk Capacity: {d['risk_capacity']}")
    if d.get("investable_monthly"):
        lines.append(f"- Investable Monthly: ₹{d['investable_monthly']:,.0f}")

    anomalies = d.get("anomalies", [])
    if anomalies:
        lines.append(f"- Spending Anomalies: {len(anomalies)} flagged categories")

    return "\n".join(lines) if lines else "Limited personal finance data available."


# ── LLM Section Writer ────────────────────────────────────────────────────────

async def write_section(
    section_name: str,
    instructions: str,
    context: str,
    client: httpx.AsyncClient,
) -> str:
    """
    Writes one memo section using Groq.
    """
    system = f"""You are a senior financial analyst writing a professional investment memo.
Write the **{section_name}** section based on the provided data.
{instructions}

## Style guidelines
- Be direct and specific — no generic filler
- Use **bold** for key numbers and conclusions
- Keep it under 200 words
- Write for a sophisticated retail investor
- Reference the actual data provided — do not invent numbers"""

    user = f"""Data for this section:

{context}

Write the {section_name} section now."""

    return await call_llm(
        system=system,
        user=user,
        max_tokens=400,
        temperature=0.3,
        client=client
    )


# ── DSPy path ─────────────────────────────────────────────────────────────────
# Used when compiled_writer.json exists. Generates the full memo in one shot.

def _write_memo_with_dspy(
    ticker:        str,
    fin_context:   str,
    risk_context:  str,
    code_context:  str,
    rebal_context: str,
    personal_ctx:  str,
) -> Optional[str]:
    """
    Calls the DSPy compiled program to generate the full memo.
    Returns the memo string, or None if it fails (triggers fallback).
    """
    if _COMPILED_PROGRAM is None:
        return None
    try:
        result = _COMPILED_PROGRAM(
            financial_context = fin_context,
            risk_context      = risk_context,
            code_context      = code_context,
            personal_context  = personal_ctx,
            rebalance_context = rebal_context,
        )
        memo = result.memo or ""
        if len(memo) > 200:
            print(f"  [writer] ✅ DSPy path — {len(memo)} chars")
            return memo
        return None   # too short, fall back
    except Exception as e:
        print(f"  [writer] DSPy path failed ({e}), falling back to hand-written")
        return None


# ── Main Orchestrator ─────────────────────────────────────────────────────────

async def run_writer_agent(
    ticker:             str,
    financial_snapshot  = None,
    risk_report         = None,
    code_output         = None,
    rebalance_suggestion= None,
    personal_finance    = None,
    research_snapshot   = None,
) -> InvestmentMemo:
    """
    Main entry point. Called by LangGraph in Phase 4.

    ## Flow
    1. Try DSPy compiled prompt (one call, all sections)
    2. If that fails or compiled prompt doesn't exist, run the 7-section approach
    """
    print(f"\n{'='*50}")
    print(f"  Writer Agent — {ticker}")
    print(f"{'='*50}")

    # Format all context upfront (same for both paths)
    fin_context   = format_financial_snapshot(financial_snapshot)
    risk_context  = format_risk_report(risk_report)
    code_context  = format_code_output(code_output)
    rebal_context = format_rebalancing(rebalance_suggestion)
    personal_ctx  = format_personal_finance(personal_finance)

    # Top-level values for the memo header
    verdict      = "Hold"
    risk_score   = None
    company_name = ticker

    if risk_report:
        d          = risk_report.model_dump() if hasattr(risk_report, "model_dump") else risk_report
        verdict    = d.get("recommendation", "Hold")
        risk_score = d.get("risk_score")

    if financial_snapshot:
        d            = financial_snapshot.model_dump() if hasattr(financial_snapshot, "model_dump") else financial_snapshot
        company_name = d.get("company_name") or ticker

    # --- Try DSPy path first --------------------------------------------------
    dspy_memo = _write_memo_with_dspy(ticker, fin_context, risk_context, code_context, rebal_context, personal_ctx)

    if dspy_memo:
        # DSPy returned the full memo — wrap it and return
        memo = InvestmentMemo(
            ticker=ticker,
            company_name=company_name,
            verdict=verdict,
            risk_score=risk_score,
            full_memo=dspy_memo,
        )
        print(f"\n  ✅ Memo complete (DSPy) — {len(dspy_memo)} characters")
        print(f"  Verdict: {verdict}")
        print(f"{'='*50}\n")
        return memo

    # --- Fallback: hand-written 7-section approach ---------------------------
    print(f"  [writer] Using hand-written 7-section approach")
    sections = {}

    async with httpx.AsyncClient() as client:

        # 1. Executive Summary
        print(f"  Writing: Executive Summary...")
        sections["executive_summary"] = await write_section(
            section_name="Executive Summary",
            instructions="Summarize the investment case in 3-4 sentences. "
                         "Lead with the verdict (Buy/Hold/Avoid) and the single strongest reason.",
            context=f"{fin_context}\n\nRisk Assessment:\n{risk_context}\n\nValuation:\n{code_context}",
            client=client,
        )

        # 2. Financial Snapshot
        print(f"  Writing: Financial Snapshot...")
        sections["financial_snapshot"] = await write_section(
            section_name="Financial Snapshot",
            instructions="Summarize the key financial metrics. "
                         "Highlight revenue trend, profitability, and balance sheet strength.",
            context=fin_context,
            client=client,
        )

        # 3. Valuation Analysis
        print(f"  Writing: Valuation Analysis...")
        sections["valuation_analysis"] = await write_section(
            section_name="Valuation Analysis",
            instructions="Interpret the DCF and Monte Carlo results. "
                         "Is the stock overvalued or undervalued? What does the probability distribution tell us?",
            context=code_context,
            client=client,
        )

        # 4. Risk Assessment
        print(f"  Writing: Risk Assessment...")
        sections["risk_assessment"] = await write_section(
            section_name="Risk Assessment",
            instructions="Explain the risk score and top risk factors. "
                         "Note any personal finance adjustments made to the score.",
            context=risk_context,
            client=client,
        )

        # 5. Portfolio Impact
        print(f"  Writing: Portfolio Impact...")
        sections["portfolio_impact"] = await write_section(
            section_name="Portfolio Impact",
            instructions="Explain how adding this stock affects the user's portfolio. "
                         "Highlight any rebalancing actions needed.",
            context=rebal_context,
            client=client,
        )

        # 6. Personal Finance Fit
        print(f"  Writing: Personal Finance Fit...")
        sections["personal_fit"] = await write_section(
            section_name="Personal Finance Fit",
            instructions="Assess whether this investment fits the user's personal financial situation. "
                         "Reference their surplus, health score, and risk capacity directly.",
            context=f"{personal_ctx}\n\nRisk Assessment:\n{risk_context}",
            client=client,
        )

        # 7. Final Verdict
        print(f"  Writing: Final Verdict...")
        sections["final_verdict"] = await write_section(
            section_name="Final Verdict",
            instructions=f"Give a clear {verdict} recommendation with 2-3 specific reasons. "
                          "End with one actionable next step for the investor.",
            context=f"Verdict: {verdict}\n\n{risk_context}\n\nValuation:\n{code_context}\n\nPersonal:\n{personal_ctx}",
            client=client,
        )

    # Assemble full memo
    full_memo = f"""# WealthOS Investment Analysis: {company_name} ({ticker})
*Generated: {datetime.now(timezone.utc).strftime('%B %d, %Y')}*

---

## Executive Summary
{sections['executive_summary']}

---

## Financial Snapshot
{sections['financial_snapshot']}

---

## Valuation Analysis
{sections['valuation_analysis']}

---

## Risk Assessment
{sections['risk_assessment']}

---

## Portfolio Impact
{sections['portfolio_impact']}

---

## Personal Finance Fit
{sections['personal_fit']}

---

## Final Verdict: {verdict.upper()}
{sections['final_verdict']}

---
*Powered by WealthOS — AI-driven personal financial intelligence*
"""

    memo = InvestmentMemo(
        ticker=ticker,
        company_name=company_name,
        verdict=verdict,
        risk_score=risk_score,
        full_memo=full_memo,
        **sections,
    )

    print(f"\n  ✅ Memo complete — {len(full_memo)} characters")
    print(f"  Verdict: {verdict}")
    print(f"{'='*50}\n")

    return memo


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "TSLA"

    async def main():
        from agents.data_agent import run_data_agent
        from agents.risk_agent import run_risk_agent
        from agents.code_agent import run_code_agent

        print("Running full pipeline...\n")

        snapshot = await run_data_agent(ticker, use_rag=False)
        risk     = await run_risk_agent(ticker, snapshot)
        code     = await run_code_agent(ticker, snapshot)

        test_personal = {
            "monthly_income":     150000,
            "monthly_surplus":    30000,
            "debt_burden_ratio":  0.25,
            "health_score":       {"total": 72, "grade": "Good"},
            "risk_capacity":      "medium",
            "investable_monthly": 20000,
        }

        memo = await run_writer_agent(
            ticker=ticker,
            financial_snapshot=snapshot,
            risk_report=risk,
            code_output=code,
            personal_finance=test_personal,
        )

        print("\n" + "="*60)
        print(memo.full_memo)
        print("="*60)

    asyncio.run(main())