"""
rag/populate_facts.py
Populates the financial_facts table using yfinance.
Avoids EDGAR XBRL key inconsistency — yfinance handles aggregation internally.
Trade-off: yfinance uses calendar year labels, not strict fiscal year.

Usage:
    python -m rag.populate_facts TSLA
    python -m rag.populate_facts TSLA AAPL AMZN
"""

import sys
import asyncio
import asyncpg
import yfinance as yf
from datetime import datetime, timezone
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("WEALTHOS_DB_URL", "")
DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


METRICS = [
    "total_revenue",
    "net_income",
    "gross_profit",
    "operating_income",
    "ebitda",
    "free_cash_flow",
    "total_assets",
    "total_debt",
    "cash_and_equivalents",
    "eps_basic",
    "eps_diluted",
]

# yfinance income statement / balance sheet / cashflow key mapping
INCOME_KEYS = {
    "total_revenue":    ["Total Revenue"],
    "net_income":       ["Net Income"],
    "gross_profit":     ["Gross Profit"],
    "operating_income": ["Operating Income", "EBIT"],
    "ebitda":           ["EBITDA"],
    "eps_basic":        ["Basic EPS"],
    "eps_diluted":      ["Diluted EPS"],
}

CASHFLOW_KEYS = {
    "free_cash_flow":   ["Free Cash Flow"],
}

BALANCE_KEYS = {
    "total_assets":          ["Total Assets"],
    "total_debt":            ["Total Debt", "Long Term Debt"],
    "cash_and_equivalents":  ["Cash And Cash Equivalents",
                              "Cash Cash Equivalents And Short Term Investments"],
}


def get_value(df, keys: list[str]):
    """Try multiple key names, return first match or None."""
    for key in keys:
        if key in df.index:
            return df.loc[key]
    return None


from datetime import datetime, timezone

def extract_facts(ticker_symbol: str) -> list[dict]:
    """Pull financial facts from yfinance for all available years."""
    ticker = yf.Ticker(ticker_symbol)
    facts = []
    current_year = datetime.now(timezone.utc).year  # 2026 — skips incomplete FY2026

    # --- Income statement (annual) ---
    try:
        income = ticker.financials
        if income is not None and not income.empty:
            for col in income.columns:
                year = col.year
                if year >= current_year:   # ← skip incomplete current year
                    continue
                for metric, keys in INCOME_KEYS.items():
                    series = get_value(income, keys)
                    if series is not None and col in series.index:
                        val = series[col]
                        if val is not None and str(val) != "nan":
                            facts.append({
                                "ticker":      ticker_symbol.upper(),
                                "metric":      metric,
                                "value":       float(val) / 1_000_000,
                                "unit":        "millions_usd",
                                "fiscal_year": year,
                                "period":      f"FY{year}",
                                "source":      "yfinance_annual",
                            })
    except Exception as e:
        print(f"  [WARN] Income statement fetch failed: {e}")

    # --- Cash flow statement (annual) ---
    try:
        cashflow = ticker.cashflow
        if cashflow is not None and not cashflow.empty:
            for col in cashflow.columns:
                year = col.year
                if year >= current_year:   # ← skip incomplete current year
                    continue
                for metric, keys in CASHFLOW_KEYS.items():
                    series = get_value(cashflow, keys)
                    if series is not None and col in series.index:
                        val = series[col]
                        if val is not None and str(val) != "nan":
                            facts.append({
                                "ticker":      ticker_symbol.upper(),
                                "metric":      metric,
                                "value":       float(val) / 1_000_000,
                                "unit":        "millions_usd",
                                "fiscal_year": year,
                                "period":      f"FY{year}",
                                "source":      "yfinance_annual",
                            })
    except Exception as e:
        print(f"  [WARN] Cash flow fetch failed: {e}")

    # --- Balance sheet (annual) ---
    try:
        balance = ticker.balance_sheet
        if balance is not None and not balance.empty:
            for col in balance.columns:
                year = col.year
                if year >= current_year:   # ← skip incomplete current year
                    continue
                for metric, keys in BALANCE_KEYS.items():
                    series = get_value(balance, keys)
                    if series is not None and col in series.index:
                        val = series[col]
                        if val is not None and str(val) != "nan":
                            facts.append({
                                "ticker":      ticker_symbol.upper(),
                                "metric":      metric,
                                "value":       float(val) / 1_000_000,
                                "unit":        "millions_usd",
                                "fiscal_year": year,
                                "period":      f"FY{year}",
                                "source":      "yfinance_annual",
                            })
    except Exception as e:
        print(f"  [WARN] Balance sheet fetch failed: {e}")

    return facts


async def upsert_facts(facts: list[dict], ticker: str):
    """Upsert facts into financial_facts table."""
    conn = await asyncpg.connect(DATABASE_URL)

    # Delete existing rows for this ticker first (clean re-population)
    deleted = await conn.execute(
        "DELETE FROM financial_facts WHERE ticker = $1", ticker.upper()
    )
    print(f"  Cleared old rows: {deleted}")

    inserted = 0
    for f in facts:
        await conn.execute("""
            INSERT INTO financial_facts 
                (ticker, metric, value, unit, fiscal_year, period, source)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT DO NOTHING
        """,
        f["ticker"], f["metric"], f["value"], f["unit"],
        f["fiscal_year"], f["period"], f["source"]
        )
        inserted += 1

    await conn.close()
    print(f"  Inserted {inserted} rows for {ticker.upper()}")


async def populate(tickers: list[str]):
    for ticker in tickers:
        print(f"\n{'='*50}")
        print(f"  Processing: {ticker.upper()}")
        print(f"{'='*50}")

        print("  Fetching from yfinance...")
        facts = extract_facts(ticker)
        print(f"  Extracted {len(facts)} facts")

        if not facts:
            print(f"  [ERROR] No facts extracted for {ticker} — check ticker symbol")
            continue

        # Show preview
        revenue_facts = [f for f in facts if f["metric"] == "total_revenue"]
        if revenue_facts:
            print("\n  Revenue preview (should match known figures):")
            for f in sorted(revenue_facts, key=lambda x: x["fiscal_year"], reverse=True)[:5]:
                print(f"    FY{f['fiscal_year']}: ${f['value']:,.0f}M")

        await upsert_facts(facts, ticker)

    print("\n✅ Done. Run the query engine to verify:")
    for t in tickers:
        print(f'  python -m rag.query_engine "What is {t.upper()}\'s total revenue?" {t.upper()}')


if __name__ == "__main__":
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["TSLA"]
    asyncio.run(populate(tickers))