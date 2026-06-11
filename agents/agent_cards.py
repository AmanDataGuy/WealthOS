# agents/agent_cards.py
"""
A2A protocol foundation — structured metadata for each WealthOS agent.

Each card describes what the agent does, what it consumes from state,
what it produces, and which MCP servers it depends on. Honest about
what's fully implemented vs partially wired.
"""

from typing import Optional


# ── Agent card definitions ─────────────────────────────────────────────────────

AGENT_CARDS = {
    "finance_agent": {
        "name": "finance_agent",
        "description": "Reads user transaction history and computes a 5-dimension financial health score.",
        "capabilities": [
            "z-score anomaly detection on spending categories",
            "health score calculation (savings rate, debt burden, expense stability, goals, emergency fund)",
            "bank statement and receipt parsing via vision model",
            "monthly surplus and investable amount calculation",
        ],
        "input_schema": ["user_id"],
        "output_schema": ["personal_finance"],
        "mcp_servers": ["finance_server"],
        "status": "FULL",
    },

    "data_agent": {
        "name": "data_agent",
        "description": "Fetches validated financial numbers for a ticker from Postgres, yfinance, and Qdrant RAG.",
        "capabilities": [
            "SQL fetch from financial_facts table",
            "live market data via MCP protocol (market_server)",
            "Qdrant hybrid search for qualitative SEC filing context",
            "Redis 15-minute caching of full FinancialSnapshot",
        ],
        "input_schema": ["tickers"],
        "output_schema": ["financial_snapshot"],
        "mcp_servers": ["market_server"],
        "status": "PARTIAL",
        # market_server now called via MCP protocol; RAG has a _call_llm bug (see wealthos_context.md)
    },

    "research_agent": {
        "name": "research_agent",
        "description": "Gathers news sentiment, market signals, and SEC filings for given tickers.",
        "capabilities": [
            "news headline and sentiment analysis",
            "SEC EDGAR filing retrieval",
            "Reddit sentiment via Firecrawl",
            "market signal aggregation",
        ],
        "input_schema": ["user_id", "tickers"],
        "output_schema": ["research_output"],
        "mcp_servers": ["market_server", "news_server", "sec_edgar_server"],
        "status": "PARTIAL",
        # query_rag() still uses dead pgvector path; direct imports not yet migrated to MCP
    },

    "risk_agent": {
        "name": "risk_agent",
        "description": "3-node LangGraph debate — macro analyst + stock analyst run in parallel, risk scorer synthesizes.",
        "capabilities": [
            "macro environment risk assessment",
            "stock-specific risk factor analysis",
            "personal risk adjustment based on user financial profile",
            "JSON-structured risk report with Buy/Hold/Avoid recommendation",
        ],
        "input_schema": ["tickers", "financial_snapshot", "personal_finance"],
        "output_schema": ["risk_report"],
        "mcp_servers": [],
        "status": "FULL",
    },

    "code_agent": {
        "name": "code_agent",
        "description": "Runs DCF, Monte Carlo, and sensitivity analysis in an E2B sandboxed Python environment.",
        "capabilities": [
            "DCF intrinsic value calculation",
            "10,000-simulation Monte Carlo distribution",
            "sensitivity table across WACC and growth rate inputs",
            "graceful fallback when E2B_API_KEY is absent",
        ],
        "input_schema": ["tickers", "financial_snapshot"],
        "output_schema": ["code_output"],
        "mcp_servers": [],
        "status": "FULL",
    },

    "rebalancing_agent": {
        "name": "rebalancing_agent",
        "description": "Compares current portfolio allocation against targets and produces buy/sell actions.",
        "capabilities": [
            "sector drift detection against DEFAULT_TARGET allocation",
            "concentration warning at >40% sector exposure",
            "new investment impact preview",
            "demo portfolio fallback when no real holdings exist",
        ],
        "input_schema": ["user_id", "tickers", "financial_snapshot"],
        "output_schema": ["rebalance_suggestion"],
        "mcp_servers": ["market_server", "portfolio_server"],
        "status": "FULL",
    },

    "writer_agent": {
        "name": "writer_agent",
        "description": "Produces a 7-section markdown investment memo using DSPy BootstrapFewShot-compiled prompt.",
        "capabilities": [
            "DSPy compiled prompt path (loads eval/compiled_writer.json)",
            "7-section memo: Executive Summary → Valuation → Risk → Portfolio Impact → Personal Finance Fit → Final Verdict",
            "LLM fallback path when compiled prompt file is absent",
            "Guardrails schema validation before returning",
        ],
        "input_schema": [
            "tickers", "financial_snapshot", "risk_report",
            "code_output", "rebalance_suggestion", "personal_finance", "research_output"
        ],
        "output_schema": ["final_memo"],
        "mcp_servers": [],
        "status": "FULL",
    },
}


# ── Public API ─────────────────────────────────────────────────────────────────

def get_agent_card(agent_name: str) -> Optional[dict]:
    """Return the card for a specific agent, or None if not found."""
    return AGENT_CARDS.get(agent_name)


def list_all_agents() -> list[dict]:
    """Return all agent cards as a list."""
    return list(AGENT_CARDS.values())
