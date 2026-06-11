# agents/risk_agent.py
"""
Risk Agent — LangGraph subgraph pattern
=======================================
Three-node internal debate before producing final risk score:
  Node 1 — MacroAnalyst   : macro environment + sector risks
  Node 2 — StockAnalyst   : company-specific financial risks
  Node 3 — RiskScorer     : synthesizes both + personal finance context → final score

All LLM calls via Groq (DeepSeek R1 for reasoning).
Receives FinancialSnapshot from Data Agent.
Receives PersonalFinanceSnapshot from Finance Agent.
Produces RiskReport.
"""

import os
import json
import asyncio
import httpx
from datetime import datetime, timezone
from typing import Optional, Literal
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
GROQ_API_KEY  = os.getenv("GROQ_API_KEY", "")

# DeepSeek R1 on Groq for reasoning tasks
REASONING_MODEL = "llama-3.3-70b-versatile"
FAST_MODEL      = "llama-3.3-70b-versatile"


# ── Pydantic Schemas ───────────────────────────────────────────────────────────

class RiskFactor(BaseModel):
    category:    str              # "macro" | "financial" | "personal" | "sector"
    description: str
    severity:    Literal["low", "medium", "high"]

class PersonalRiskAdjustment(BaseModel):
    base_score:       int
    adjusted_score:   int
    adjustments_made: list[str]   # reasons for each adjustment

class RiskReport(BaseModel):
    ticker:             str
    risk_score:         int       = Field(ge=1, le=10)   # 1=lowest, 10=highest risk
    risk_grade:         str       # "Low" | "Moderate" | "High" | "Very High"
    macro_analysis:     str
    stock_analysis:     str
    final_verdict:      str
    risk_factors:       list[RiskFactor] = Field(default_factory=list)
    personal_adjustment: Optional[PersonalRiskAdjustment] = None
    recommendation:     Literal["Buy", "Hold", "Avoid"]
    analysis_date:      str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    confidence:         str = "high"


# ── LLM Calls ─────────────────────────────────────────────────────────────────

from services.llm_client import call_llm

# ── Node 1 — MacroAnalyst ─────────────────────────────────────────────────────

async def macro_analyst_node(ticker: str, sector: str, snapshot_context: str) -> str:
    """
    Analyzes macro environment and sector-level risks.
    Uses fast model — macro context is well-known to the LLM.
    """
    print(f"  [Node 1] MacroAnalyst running...")

    system = """You are a macro economist analyzing investment risk.
Assess the macro and sector environment for the given stock.
Be specific and concise. Focus on:
- Current interest rate environment impact
- Sector-specific tailwinds and headwinds
- Regulatory risks
- Global macro risks (inflation, recession probability, currency)
Return a structured analysis in 200 words max."""

    user = f"""Stock: {ticker}
Sector: {sector}
Financial context: {snapshot_context}

Provide your macro and sector risk analysis."""

    return await call_llm(system, user, model=FAST_MODEL)


# ── Node 2 — StockAnalyst ─────────────────────────────────────────────────────

async def stock_analyst_node(ticker: str, snapshot_context: str) -> str:
    """
    Analyzes company-specific financial risks.
    Uses DeepSeek R1 for deeper reasoning on financial metrics.
    """
    print(f"  [Node 2] StockAnalyst running (DeepSeek R1)...")

    system = """You are a financial analyst specializing in stock risk assessment.
Analyze the company's financial health and identify specific risks.
Focus on:
- Valuation risk (P/E vs sector average, PEG ratio)
- Balance sheet strength (debt levels, cash position)
- Earnings quality and consistency
- Revenue growth sustainability
- Free cash flow generation
Be direct. Give a risk score 1-10 for the stock itself (ignoring personal context).
Return analysis in 200 words max, ending with: "Stock Risk Score: X/10" """

    user = f"""Stock: {ticker}
Financial Data:
{snapshot_context}

Provide your stock-specific risk analysis."""

    return await call_llm(system, user, model=REASONING_MODEL, max_tokens=1000)


# ── Node 3 — RiskScorer ───────────────────────────────────────────────────────

async def risk_scorer_node(
    ticker: str,
    macro_analysis: str,
    stock_analysis: str,
    personal_context: Optional[str] = None,
) -> dict:
    """
    Synthesizes macro + stock analysis.
    Applies personal finance adjustments if PersonalFinanceSnapshot provided.
    Produces final risk score and recommendation.
    Uses DeepSeek R1 for final synthesis.
    """
    print(f"  [Node 3] RiskScorer synthesizing...")

    personal_section = ""
    if personal_context:
        personal_section = f"\nPersonal Finance Context:\n{personal_context}"

    system = """You are a senior risk officer making a final investment risk assessment.
You receive analysis from a MacroAnalyst and a StockAnalyst.
Synthesize their findings into a final risk score and recommendation.

Rules:
- Risk score 1-10 (1=very low risk, 10=very high risk)
- Recommendation: "Buy" (score 1-4), "Hold" (score 5-6), "Avoid" (score 7-10)
- If personal finance context shows high debt burden or low surplus → add 1-2 to score
- If personal finance context shows strong health score (>75) → subtract 1 from score

Respond in this EXACT JSON format:
{
  "risk_score": <int 1-10>,
  "risk_grade": "<Low|Moderate|High|Very High>",
  "recommendation": "<Buy|Hold|Avoid>",
  "final_verdict": "<2-3 sentence summary>",
  "risk_factors": [
    {"category": "<macro|financial|personal|sector>", "description": "<text>", "severity": "<low|medium|high>"},
    {"category": "...", "description": "...", "severity": "..."}
  ],
  "personal_adjustment": {
    "base_score": <int>,
    "adjusted_score": <int>,
    "adjustments_made": ["<reason1>", "<reason2>"]
  }
}"""

    user = f"""Ticker: {ticker}

MacroAnalyst Report:
{macro_analysis}

StockAnalyst Report:
{stock_analysis}
{personal_section}

Produce the final risk assessment JSON."""

    response = await call_llm(system, user, model=REASONING_MODEL, max_tokens=1000)

    # Parse JSON response
    try:
        # Strip any markdown code blocks
        clean = response.strip()
        if "```json" in clean:
            clean = clean.split("```json")[1].split("```")[0].strip()
        elif "```" in clean:
            clean = clean.split("```")[1].split("```")[0].strip()

        # Find JSON object
        start = clean.find("{")
        end   = clean.rfind("}") + 1
        if start != -1 and end > start:
            clean = clean[start:end]

        return json.loads(clean)
    except Exception as e:
        print(f"  [risk_agent] JSON parse failed: {e}")
        print(f"  Raw response: {response[:200]}")
        # Return safe defaults
        return {
            "risk_score":   5,
            "risk_grade":   "Moderate",
            "recommendation": "Hold",
            "final_verdict": f"Unable to parse full analysis for {ticker}. Manual review recommended.",
            "risk_factors": [],
            "personal_adjustment": None,
        }


# ── Personal Finance Context Builder ──────────────────────────────────────────

def build_personal_context(personal_finance: Optional[dict]) -> Optional[str]:
    """
    Converts PersonalFinanceSnapshot dict to a concise string for the RiskScorer.
    Accepts dict (for flexibility) or None.
    """
    if not personal_finance:
        return None

    lines = []
    if personal_finance.get("monthly_surplus"):
        lines.append(f"Monthly surplus: ₹{personal_finance['monthly_surplus']:,.0f}")
    if personal_finance.get("monthly_income"):
        lines.append(f"Monthly income: ₹{personal_finance['monthly_income']:,.0f}")
    if personal_finance.get("debt_burden_ratio"):
        ratio = personal_finance["debt_burden_ratio"]
        lines.append(f"Debt burden ratio: {ratio:.1%} ({'HIGH — caution' if ratio > 0.4 else 'acceptable'})")
    if personal_finance.get("health_score"):
        hs = personal_finance["health_score"]
        score = hs.get("total") if isinstance(hs, dict) else hs
        lines.append(f"Financial Health Score: {score}/100")
    if personal_finance.get("risk_capacity"):
        lines.append(f"Risk capacity: {personal_finance['risk_capacity']}")
    if personal_finance.get("investable_monthly"):
        lines.append(f"Investable monthly: ₹{personal_finance['investable_monthly']:,.0f}")

    goals = personal_finance.get("goals", [])
    short_term_goals = [g for g in goals if isinstance(g, dict) and g.get("deadline_months", 999) < 12]
    if short_term_goals:
        lines.append(f"Short-term goals (< 12 months): {len(short_term_goals)} — reduces risk capacity")

    return "\n".join(lines) if lines else None


# ── Snapshot Context Builder ───────────────────────────────────────────────────

def build_snapshot_context(snapshot) -> str:
    """Convert FinancialSnapshot to a concise string for the LLM nodes."""
    lines = []

    # Handle both FinancialSnapshot objects and dicts
    if hasattr(snapshot, "model_dump"):
        data = snapshot.model_dump()
    elif isinstance(snapshot, dict):
        data = snapshot
    else:
        return str(snapshot)

    inc = data.get("income_statement", {})
    val = data.get("valuation", {})
    bal = data.get("balance_sheet", {})
    cf  = data.get("cash_flow", {})
    gr  = data.get("growth", {})

    if inc.get("total_revenue"):
        lines.append(f"Revenue (FY{inc.get('fiscal_year')}): ${inc['total_revenue']:,.0f}M")
    if inc.get("net_income"):
        lines.append(f"Net Income: ${inc['net_income']:,.0f}M")
    if val.get("current_price"):
        lines.append(f"Current Price: ${val['current_price']:.2f}")
    if val.get("pe_ratio"):
        lines.append(f"P/E Ratio: {val['pe_ratio']:.1f}x")
    if val.get("market_cap"):
        lines.append(f"Market Cap: ${val['market_cap']/1e9:.1f}B")
    if bal.get("total_debt"):
        lines.append(f"Total Debt: ${bal['total_debt']:,.0f}M")
    if bal.get("cash_equivalents"):
        lines.append(f"Cash: ${bal['cash_equivalents']:,.0f}M")
    if cf.get("free_cash_flow"):
        lines.append(f"Free Cash Flow: ${cf['free_cash_flow']:,.0f}M")
    if gr.get("revenue_cagr_3y"):
        lines.append(f"Revenue CAGR 3Y: {gr['revenue_cagr_3y']:.1f}%")

    if data.get("key_risks"):
        lines.append(f"Key Risks (from filing): {data['key_risks'][:200]}")

    return "\n".join(lines)


# ── Main Orchestrator ──────────────────────────────────────────────────────────

async def run_risk_agent(
    ticker: str,
    financial_snapshot=None,
    personal_finance: Optional[dict] = None,
) -> RiskReport:
    """
    Main entry point. Called by LangGraph in Phase 4.

    Args:
        ticker:             Stock ticker
        financial_snapshot: FinancialSnapshot from Data Agent (object or dict)
        personal_finance:   PersonalFinanceSnapshot dict from Finance Agent (optional)
    """
    print(f"\n{'='*50}")
    print(f"  Risk Agent — {ticker}")
    print(f"{'='*50}")

    # Build context strings
    snapshot_context = build_snapshot_context(financial_snapshot) if financial_snapshot else \
                       f"Ticker: {ticker} (no financial data provided)"
    personal_context = build_personal_context(personal_finance)

    sector = "Unknown"
    if financial_snapshot:
        if hasattr(financial_snapshot, "sector"):
            sector = financial_snapshot.sector or "Unknown"
        elif isinstance(financial_snapshot, dict):
            sector = financial_snapshot.get("sector", "Unknown")

    # ── Run the 3-node debate (macro + stock in parallel, scorer after) ────────
    macro_task = macro_analyst_node(ticker, sector, snapshot_context)
    stock_task = stock_analyst_node(ticker, snapshot_context)

    macro_analysis, stock_analysis = await asyncio.gather(macro_task, stock_task)

    print(f"  [Debate] MacroAnalyst ✅ | StockAnalyst ✅")
    print(f"  [Debate] RiskScorer synthesizing...")

    scored = await risk_scorer_node(
        ticker=ticker,
        macro_analysis=macro_analysis,
        stock_analysis=stock_analysis,
        personal_context=personal_context,
    )

    # Build RiskFactor objects
    risk_factors = []
    for rf in scored.get("risk_factors", []):
        try:
            risk_factors.append(RiskFactor(**rf))
        except Exception:
            pass

    # Build PersonalRiskAdjustment if present
    personal_adj = None
    if scored.get("personal_adjustment"):
        try:
            personal_adj = PersonalRiskAdjustment(**scored["personal_adjustment"])
        except Exception:
            pass

    risk_score = scored.get("risk_score", 5)
    report = RiskReport(
        ticker=ticker,
        risk_score=risk_score,
        risk_grade=scored.get("risk_grade", "Moderate"),
        macro_analysis=macro_analysis,
        stock_analysis=stock_analysis,
        final_verdict=scored.get("final_verdict", ""),
        risk_factors=risk_factors,
        personal_adjustment=personal_adj,
        recommendation=scored.get("recommendation", "Hold"),
        confidence="high" if financial_snapshot else "low",
    )

    print(f"\n  Risk Score   : {report.risk_score}/10 ({report.risk_grade})")
    print(f"  Recommend    : {report.recommendation}")
    print(f"  Verdict      : {report.final_verdict[:100]}...")
    print(f"{'='*50}\n")

    return report


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "TSLA"

    async def main():
        # Run Data Agent first to get snapshot
        from agents.data_agent import run_data_agent
        print("Fetching financial snapshot...")
        snapshot = await run_data_agent(ticker, use_rag=False)

        # Optional personal finance context for testing
        test_personal = {
            "monthly_income":    150000,
            "monthly_surplus":   30000,
            "debt_burden_ratio": 0.25,
            "health_score":      {"total": 72},
            "risk_capacity":     "medium",
            "investable_monthly": 20000,
            "goals": [],
        }

        report = await run_risk_agent(
            ticker=ticker,
            financial_snapshot=snapshot,
            personal_finance=test_personal,
        )

        print("\n── RiskReport ─────────────────────────────────────")
        print(f"  Ticker       : {report.ticker}")
        print(f"  Risk Score   : {report.risk_score}/10")
        print(f"  Risk Grade   : {report.risk_grade}")
        print(f"  Recommend    : {report.recommendation}")
        print(f"\n  Risk Factors :")
        for rf in report.risk_factors:
            print(f"    [{rf.severity.upper()}] {rf.category}: {rf.description[:80]}")
        print(f"\n  Final Verdict: {report.final_verdict}")
        if report.personal_adjustment:
            print(f"\n  Personal Adj : {report.personal_adjustment.base_score} → {report.personal_adjustment.adjusted_score}")
            for reason in report.personal_adjustment.adjustments_made:
                print(f"    - {reason}")
        print("──────────────────────────────────────────────────\n")

    asyncio.run(main())