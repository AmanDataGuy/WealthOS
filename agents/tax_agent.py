# agents/tax_agent.py
"""
Tax Agent — India-specific tax context (old vs. new regime, unused 80C
headroom), surfaced as a "Tax Impact" section on top of the Writer Agent's
memo. No mainstream investment-advice tool localizes to Indian tax law;
this is what actually backs the README's opening claim that WealthOS knows
"their 80C deduction is unutilized" — mcp_servers/tax_server.py existed
before this agent but had zero callers until now.

Calls tax_server's tool functions as a direct Python import, not via
MCPClient — same call-once-per-run reasoning as sec_edgar_server/news_server
in docs/adr/001-mcp-transport-boundary.md, not a hot-path call.
"""

import re
from typing import Optional

from mcp_servers.tax_server import calculate_tax, tax_saving_suggestions


# Cheap, deterministic gate — no LLM call needed to decide whether a query
# is tax-shaped. False negatives just mean no Tax Impact section (same as
# today); false positives just mean one extra pure-Python calculation, not
# a wasted LLM call, so this errs toward matching broadly.
_TAX_KEYWORDS = re.compile(
    r"\b(tax|80c|80d|elss|hra|nps|ppf|regime|deduction|capital gains?|ltcg|stcg)\b",
    re.IGNORECASE,
)


def involves_taxable_decision(query: str) -> bool:
    return bool(_TAX_KEYWORDS.search(query or ""))


async def run_tax_agent(
    monthly_income: float,
    current_80c: float = 0,
    emi_by_type: Optional[dict] = None,
) -> Optional[dict]:
    """
    Args mirror what graph/nodes.py already has from personal_finance
    (Finance Agent's monthly_income + emi_by_type). Returns None if there's
    no income figure to compute against (e.g. cold-start user with no
    transactions).

    emi_by_type (e.g. {"emi_home": 39650, "emi_auto": 14850}) holds one
    month's EMI payment per loan type — principal and interest are not
    split out anywhere upstream (no amortization schedule exists), so the
    full monthly EMI is annualized (x12) and used as a conservative proxy
    for annual interest paid. This overstates true interest (which is only
    a portion of the EMI and shrinks over the loan's life), so the resulting
    deduction estimate skews generous, not fabricated-low.
    """
    if not monthly_income or monthly_income <= 0:
        return None

    gross_income = round(monthly_income * 12, 2)

    emi_by_type = emi_by_type or {}
    home_loan_interest = round(emi_by_type.get("emi_home", 0) * 12, 2)
    education_loan_interest = round(emi_by_type.get("emi_education", 0) * 12, 2)
    auto_personal_interest = round(
        (emi_by_type.get("emi_auto", 0) + emi_by_type.get("emi_personal", 0)) * 12, 2
    )

    regime_comparison = calculate_tax(
        gross_income=gross_income, section_80c=current_80c,
        home_loan_interest=home_loan_interest,
        education_loan_interest=education_loan_interest,
    )
    suggestions = tax_saving_suggestions(
        gross_income=gross_income, current_80c=current_80c,
        home_loan_interest=home_loan_interest,
        education_loan_interest=education_loan_interest,
        auto_or_personal_loan_interest=auto_personal_interest,
    )

    return {
        "gross_annual_income": gross_income,
        "regime_comparison": regime_comparison,
        "tax_saving_suggestions": suggestions,
    }


def demo() -> None:
    """ponytail: smallest runnable check — trigger heuristic + a real calculation."""
    assert involves_taxable_decision("Should I invest in NIFTYBEES ELSS for 80C?")
    assert involves_taxable_decision("What's my capital gains tax on selling TCS?")
    assert not involves_taxable_decision("Should I buy NVDA right now?")

    import asyncio
    result = asyncio.run(run_tax_agent(monthly_income=150_000, current_80c=50_000))
    assert result is not None
    assert result["gross_annual_income"] == 1_800_000
    assert "recommendation" in result["regime_comparison"]
    assert result["regime_comparison"]["recommendation"] in ("old_regime", "new_regime")
    assert result["tax_saving_suggestions"]["suggestions"]

    assert asyncio.run(run_tax_agent(monthly_income=0)) is None

    # emi_by_type flows through to a real Section 24(b)/80E deduction
    with_loans = asyncio.run(run_tax_agent(
        monthly_income=150_000,
        emi_by_type={"emi_home": 30_000, "emi_education": 5_000, "emi_auto": 10_000},
    ))
    assert with_loans["regime_comparison"]["old_regime"]["total_tax"] < result["regime_comparison"]["old_regime"]["total_tax"]
    sections = [s["section"] for s in with_loans["tax_saving_suggestions"]["suggestions"]]
    assert "24(b)" in sections and "80E" in sections

    print("agents/tax_agent.py self-check: all cases passed")


if __name__ == "__main__":
    demo()
