# agents/code_agent.py
"""
Code Agent — Smolagents + E2B
==============================

Builds and runs real financial models in a sandboxed Python environment.
No hallucinated numbers — every output is computed, not invented.

## What it builds
- 5-year DCF model
- Monte Carlo simulation (1000 paths)
- Sensitivity table (growth rate vs WACC)

## Dependencies
- smolagents — agent framework for code execution
- e2b-code-interpreter — secure sandbox
- FinancialSnapshot from Data Agent (for inputs)
"""

import os
import asyncio
from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

E2B_API_KEY  = os.getenv("E2B_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")


# ── Output Schema ─────────────────────────────────────────────────────────────

class DCFResult(BaseModel):
    intrinsic_value:    float           # per share
    current_price:      Optional[float] = None
    upside_downside:    Optional[float] = None   # % difference
    wacc_used:          float
    growth_rate_used:   float
    terminal_growth:    float = 0.03

class MonteCarloResult(BaseModel):
    median_price:       float
    percentile_10:      float           # bear case
    percentile_90:      float           # bull case
    probability_upside: float           # % of paths above current price
    paths_run:          int = 1000

class SensitivityResult(BaseModel):
    # Dict of "growth_rate:wacc" → intrinsic value
    table:              dict[str, float] = Field(default_factory=dict)

class CodeAgentOutput(BaseModel):
    ticker:             str
    dcf:                Optional[DCFResult]         = None
    monte_carlo:        Optional[MonteCarloResult]  = None
    sensitivity:        Optional[SensitivityResult] = None
    raw_output:         Optional[str]               = None
    error:              Optional[str]               = None
    analysis_date:      str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ── DCF Code Template ─────────────────────────────────────────────────────────

def build_dcf_code(fcf: float, growth_rate: float, wacc: float, current_price: float, shares: float) -> str:
    """
    Returns Python code string for DCF model.
    Runs inside E2B sandbox.
    """
    return f"""
import json

# --- Inputs ---
fcf            = {fcf}          # Free cash flow in millions
growth_rate_5y = {growth_rate}  # Expected annual growth for 5 years
terminal_growth = 0.03          # Long-term terminal growth rate
wacc           = {wacc}         # Weighted avg cost of capital
shares_out     = {shares}       # Shares outstanding in millions
current_price  = {current_price}

# --- 5-Year DCF ---
projected_fcf = [fcf * (1 + growth_rate_5y) ** i for i in range(1, 6)]
terminal_value = projected_fcf[-1] * (1 + terminal_growth) / (wacc - terminal_growth)

pv_fcfs = sum(f / (1 + wacc) ** i for i, f in enumerate(projected_fcf, 1))
pv_terminal = terminal_value / (1 + wacc) ** 5

total_value = pv_fcfs + pv_terminal
intrinsic_per_share = total_value / shares_out

upside = ((intrinsic_per_share - current_price) / current_price) * 100

result = {{
    "intrinsic_value":  round(intrinsic_per_share, 2),
    "current_price":    current_price,
    "upside_downside":  round(upside, 2),
    "wacc_used":        wacc,
    "growth_rate_used": growth_rate_5y,
    "terminal_growth":  terminal_growth,
    "projected_fcf":    [round(f, 2) for f in projected_fcf],
    "pv_fcfs":          round(pv_fcfs, 2),
    "pv_terminal":      round(pv_terminal, 2),
}}

print(json.dumps(result))
"""


def build_montecarlo_code(current_price: float, growth_mean: float, growth_std: float) -> str:
    """Monte Carlo simulation — 1000 price paths over 1 year."""
    return f"""
import json
import random
import math

current_price = {current_price}
growth_mean   = {growth_mean}    # Expected annual return
growth_std    = {growth_std}     # Standard deviation (volatility)
paths         = 1000
days          = 252              # Trading days in a year

final_prices = []

for _ in range(paths):
    price = current_price
    for _ in range(days):
        daily_return = random.gauss(growth_mean / days, growth_std / math.sqrt(days))
        price *= (1 + daily_return)
    final_prices.append(price)

final_prices.sort()

result = {{
    "median_price":       round(final_prices[paths // 2], 2),
    "percentile_10":      round(final_prices[int(paths * 0.10)], 2),
    "percentile_90":      round(final_prices[int(paths * 0.90)], 2),
    "probability_upside": round(sum(1 for p in final_prices if p > current_price) / paths * 100, 1),
    "paths_run":          paths,
}}

print(json.dumps(result))
"""


def build_sensitivity_code(fcf: float, shares: float) -> str:
    """Sensitivity table — intrinsic value at different growth + WACC combos."""
    return f"""
import json

fcf            = {fcf}
shares_out     = {shares}
terminal_growth = 0.03

growth_rates = [0.05, 0.10, 0.15, 0.20, 0.25]
waccs        = [0.08, 0.10, 0.12, 0.14]

table = {{}}

for g in growth_rates:
    for w in waccs:
        if w <= terminal_growth:
            continue
        projected = [fcf * (1 + g) ** i for i in range(1, 6)]
        tv  = projected[-1] * (1 + terminal_growth) / (w - terminal_growth)
        pv  = sum(f / (1 + w) ** i for i, f in enumerate(projected, 1))
        pv += tv / (1 + w) ** 5
        per_share = (pv / shares_out)
        key = f"g{{int(g*100)}}_w{{int(w*100)}}"
        table[key] = round(per_share, 2)

print(json.dumps({{"table": table}}))
"""


# ── E2B Runner ────────────────────────────────────────────────────────────────

async def run_in_sandbox(code: str) -> tuple[Optional[str], Optional[str]]:
    """
    Executes Python code securely in the E2B Code Interpreter sandbox.
    """
    import os
    import asyncio
    
    e2b_api_key = os.getenv("E2B_API_KEY")
    if not e2b_api_key or e2b_api_key == "your_e2b_key_here":
        return None, "E2B_API_KEY is missing or invalid. Cannot run sandbox securely."
        
    def _run():
        from e2b_code_interpreter import Sandbox
        try:
            with Sandbox.create(api_key=e2b_api_key) as sandbox:
                execution = sandbox.run_code(code, timeout=30)
                if execution.error:
                    return None, execution.error.value
                # print() in sandbox code goes to logs.stdout (List[str]), not .text
                # .text only returns the last REPL expression value
                stdout = "\n".join(execution.logs.stdout)
                return stdout if stdout else None, None
        except Exception as e:
            return None, str(e)
            
    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        return None, f"Sandbox failed: {e}"


# ── Input Extractor ───────────────────────────────────────────────────────────

def extract_inputs(snapshot) -> dict:
    """
    Pull the numbers the Code Agent needs from FinancialSnapshot.
    Falls back to safe defaults if data is missing.
    """
    # Handle both object and dict
    if hasattr(snapshot, "model_dump"):
        data = snapshot.model_dump()
    elif isinstance(snapshot, dict):
        data = snapshot
    else:
        data = {}

    # Use `or {}` so a None sub-dict doesn't cause AttributeError on .get()
    cf  = data.get("cash_flow")        or {}
    inc = data.get("income_statement") or {}
    val = data.get("valuation")        or {}
    gr  = data.get("growth")           or {}

    # dict.get(key, default) returns the default only when the KEY IS ABSENT.
    # model_dump() always includes keys with value None, so use `(x or fallback)`
    # instead of get(key, default) for fields that may be None in the dict.
    raw_fcf = cf.get("free_cash_flow")
    raw_ni  = inc.get("net_income")
    fcf     = raw_fcf or ((raw_ni or 1000) * 0.7)

    current_price = val.get("current_price") or 100.0
    market_cap    = val.get("market_cap")    or 100e9
    shares        = market_cap / (current_price * 1e6)
    growth_cagr   = (gr.get("revenue_cagr_3y") or 10.0) / 100
    pe            = val.get("pe_ratio") or 25.0

    # WACC estimate — simple proxy based on P/E
    # High P/E → market expects high growth → use higher discount rate
    wacc = 0.08 if pe < 20 else 0.10 if pe < 40 else 0.12

    return {
        "fcf":           abs(fcf),
        "current_price": float(current_price),
        "shares":        max(shares, 0.1),
        "growth_rate":   min(max(growth_cagr, 0.03), 0.35),   # cap 3-35%
        "wacc":          wacc,
        "growth_std":    0.25,   # 25% annual volatility assumption
    }


# ── Main Orchestrator ─────────────────────────────────────────────────────────

async def run_code_agent(
    ticker: str,
    financial_snapshot=None,
) -> CodeAgentOutput:
    """
    Main entry point. Called by LangGraph in Phase 4.

    Runs DCF → Monte Carlo → Sensitivity in sequence.
    Each model runs in its own E2B sandbox for isolation.
    """
    print(f"\n{'='*50}")
    print(f"  Code Agent — {ticker}")
    print(f"{'='*50}")

    _defaults = {
        "fcf": 1000.0, "current_price": 100.0, "shares": 10.0,
        "growth_rate": 0.10, "wacc": 0.10, "growth_std": 0.25,
    }
    if financial_snapshot:
        try:
            inputs = extract_inputs(financial_snapshot)
        except Exception as e:
            print(f"  ⚠️  extract_inputs failed ({e}) — using default inputs")
            inputs = _defaults
    else:
        inputs = _defaults

    print(f"  Inputs: FCF=${inputs['fcf']:,.0f}M | Price=${inputs['current_price']:.2f} | "
          f"Growth={inputs['growth_rate']*100:.1f}% | WACC={inputs['wacc']*100:.1f}%")

    dcf_result  = None
    mc_result   = None
    sens_result = None

    # ── DCF Model ─────────────────────────────────────────────────────────────
    print(f"\n  [1/3] Running DCF model...")
    dcf_code = build_dcf_code(
        fcf=inputs["fcf"],
        growth_rate=inputs["growth_rate"],
        wacc=inputs["wacc"],
        current_price=inputs["current_price"],
        shares=inputs["shares"],
    )

    output, error = await run_in_sandbox(dcf_code)
    if output and not error:
        try:
            import json
            # grab last JSON line
            for line in reversed(output.strip().split("\n")):
                if line.strip().startswith("{"):
                    dcf_data = json.loads(line.strip())
                    dcf_result = DCFResult(**{
                        k: v for k, v in dcf_data.items()
                        if k in DCFResult.model_fields
                    })
                    print(f"  ✅ DCF: Intrinsic value = ${dcf_result.intrinsic_value:.2f} "
                          f"({dcf_result.upside_downside:+.1f}% vs current)")
                    break
        except Exception as e:
            print(f"  ⚠️  DCF parse error: {e}")
    else:
        print(f"  ⚠️  DCF failed: {error}")

    # ── Monte Carlo ───────────────────────────────────────────────────────────
    print(f"\n  [2/3] Running Monte Carlo (1000 paths)...")
    mc_code = build_montecarlo_code(
        current_price=inputs["current_price"],
        growth_mean=inputs["growth_rate"],
        growth_std=inputs["growth_std"],
    )

    output, error = await run_in_sandbox(mc_code)
    if output and not error:
        try:
            import json
            for line in reversed(output.strip().split("\n")):
                if line.strip().startswith("{"):
                    mc_data = json.loads(line.strip())
                    mc_result = MonteCarloResult(**mc_data)
                    print(f"  ✅ Monte Carlo: Median=${mc_result.median_price:.2f} | "
                          f"Bear=${mc_result.percentile_10:.2f} | "
                          f"Bull=${mc_result.percentile_90:.2f} | "
                          f"P(upside)={mc_result.probability_upside:.1f}%")
                    break
        except Exception as e:
            print(f"  ⚠️  Monte Carlo parse error: {e}")
    else:
        print(f"  ⚠️  Monte Carlo failed: {error}")

    # ── Sensitivity Table ─────────────────────────────────────────────────────
    print(f"\n  [3/3] Building sensitivity table...")
    sens_code = build_sensitivity_code(
        fcf=inputs["fcf"],
        shares=inputs["shares"],
    )

    output, error = await run_in_sandbox(sens_code)
    if output and not error:
        try:
            import json
            for line in reversed(output.strip().split("\n")):
                if line.strip().startswith("{"):
                    sens_data = json.loads(line.strip())
                    sens_result = SensitivityResult(table=sens_data.get("table", {}))
                    print(f"  ✅ Sensitivity: {len(sens_result.table)} scenarios computed")
                    break
        except Exception as e:
            print(f"  ⚠️  Sensitivity parse error: {e}")
    else:
        print(f"  ⚠️  Sensitivity failed: {error}")

    output = CodeAgentOutput(
        ticker=ticker,
        dcf=dcf_result,
        monte_carlo=mc_result,
        sensitivity=sens_result,
    )

    print(f"\n{'='*50}\n")
    return output


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "TSLA"

    async def main():
        from agents.data_agent import run_data_agent
        print("Fetching financial snapshot...")
        snapshot = await run_data_agent(ticker, use_rag=False)

        result = await run_code_agent(ticker, snapshot)

        print("\n── CodeAgentOutput ────────────────────────────────")
        if result.dcf:
            print(f"\n  DCF Model:")
            print(f"    Intrinsic Value : ${result.dcf.intrinsic_value:.2f}")
            print(f"    Current Price   : ${result.dcf.current_price:.2f}")
            print(f"    Upside/Downside : {result.dcf.upside_downside:+.1f}%")
            print(f"    WACC Used       : {result.dcf.wacc_used*100:.1f}%")
            print(f"    Growth Used     : {result.dcf.growth_rate_used*100:.1f}%")

        if result.monte_carlo:
            print(f"\n  Monte Carlo (1000 paths):")
            print(f"    Bear  (P10) : ${result.monte_carlo.percentile_10:.2f}")
            print(f"    Median      : ${result.monte_carlo.median_price:.2f}")
            print(f"    Bull  (P90) : ${result.monte_carlo.percentile_90:.2f}")
            print(f"    P(upside)   : {result.monte_carlo.probability_upside:.1f}%")

        if result.sensitivity:
            print(f"\n  Sensitivity Table (sample):")
            for i, (k, v) in enumerate(list(result.sensitivity.table.items())[:6]):
                g, w = k.split("_")
                print(f"    Growth {g[1:]}% / WACC {w[1:]}% → ${v:.2f}")
        print("──────────────────────────────────────────────────\n")

    asyncio.run(main())