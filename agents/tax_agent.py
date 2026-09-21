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
) -> Optional[dict]:
    """
    Args mirror what graph/nodes.py already has from personal_finance
    (Finance Agent's monthly_income). Returns None if there's no income
    figure to compute against (e.g. cold-start user with no transactions).
    """
    if not monthly_income or monthly_income <= 0:
        return None

    gross_income = round(monthly_income * 12, 2)

    regime_comparison = calculate_tax(gross_income=gross_income, section_80c=current_80c)
    suggestions = tax_saving_suggestions(gross_income=gross_income, current_80c=current_80c)

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

    print("agents/tax_agent.py self-check: all cases passed")


if __name__ == "__main__":
    demo()
