# sec_edgar_server.py
# Fetches SEC filings for US-listed companies (10-K, 10-Q).
# No API key needed — SEC EDGAR is a free public API.
#
# NOTE: Works for US tickers only (AAPL, TSLA, MSFT etc.)
# Indian tickers (RELIANCE.NS) will return a "not found" error — expected behaviour.
#
# Tools:
#   get_filings_list      — list recent filings for a company
#   get_10k               — fetch latest 10-K (annual report) metadata
#   get_10q               — fetch latest 10-Q (quarterly report) metadata
#   get_financial_facts   — fetch structured financial figures from XBRL API
#                           (revenue, net income, operating income, etc.)

import os
import logging
import httpx
import redis
import json

from mcp.server.fastmcp import FastMCP

# ── Setup ─────────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("sec-edgar-mcp")

r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)

# SEC requires a User-Agent header with your name/email — mandatory, else 403
SEC_HEADERS = {
    "User-Agent": os.getenv("SEC_USER_AGENT", "WealthOS research@wealthos.app"),
    "Accept-Encoding": "gzip, deflate",
}

TTL_FILINGS = 60 * 60 * 6   # 6 hours — filings don't change often
TTL_CIK     = 60 * 60 * 24  # 24 hours — CIK never changes for a company
TTL_FACTS   = 60 * 60 * 12  # 12 hours — financial facts are stable


# ── Helpers ───────────────────────────────────────────────────────────────────

def from_cache(key: str):
    """Return cached dict or None."""
    try:
        raw = r.get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def to_cache(key: str, data: dict, ttl: int):
    """Save dict to Redis. Silently fails if Redis is down."""
    try:
        r.set(key, json.dumps(data, default=str), ex=ttl)
    except Exception:
        pass


def get_cik(ticker: str) -> str | None:
    """
    Convert a ticker symbol to a SEC CIK number.
    SEC maintains a public JSON map of all tickers → CIK.
    Returns CIK as a zero-padded 10-digit string, or None if not found.
    """
    cache_key = f"sec:cik:{ticker.upper()}"
    cached = from_cache(cache_key)
    if cached:
        return cached.get("cik")

    try:
        # SEC publishes a full ticker → CIK mapping as a single JSON file
        url = "https://www.sec.gov/files/company_tickers.json"
        resp = httpx.get(url, headers=SEC_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        # data is a dict like {"0": {"cik_str": 320193, "ticker": "AAPL", ...}, ...}
        ticker_upper = ticker.upper()
        for entry in data.values():
            if entry.get("ticker", "").upper() == ticker_upper:
                cik = str(entry["cik_str"]).zfill(10)  # pad to 10 digits
                to_cache(cache_key, {"cik": cik}, TTL_CIK)
                return cik

        return None  # ticker not found in SEC database

    except Exception as e:
        logger.error("get_cik failed for %s: %s", ticker, e)
        return None


def fetch_submissions(cik: str) -> dict | None:
    """
    Fetch the full submission history for a company from SEC.
    Returns raw JSON from https://data.sec.gov/submissions/CIK{cik}.json
    """
    try:
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        resp = httpx.get(url, headers=SEC_HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error("fetch_submissions failed for CIK %s: %s", cik, e)
        return None


def extract_filings(submissions: dict, form_type: str, count: int = 5) -> list[dict]:
    """
    Pull the N most recent filings of a given form type (10-K or 10-Q)
    from the submissions JSON returned by SEC.
    """
    filings = []
    recent = submissions.get("filings", {}).get("recent", {})

    forms       = recent.get("form", [])
    dates       = recent.get("filingDate", [])
    accessions  = recent.get("accessionNumber", [])
    documents   = recent.get("primaryDocument", [])

    for i, form in enumerate(forms):
        if form == form_type:
            acc_clean = accessions[i].replace("-", "")  # strip dashes for URL
            filings.append({
                "form":       form,
                "filed_date": dates[i],
                "accession":  accessions[i],
                # Direct link to the filing index page on SEC EDGAR
                "url": (
                    f"https://www.sec.gov/Archives/edgar/full-index/"
                    f"{dates[i][:4]}/QTR{((int(dates[i][5:7])-1)//3)+1}/"
                ),
                # Direct link to the primary document (actual report)
                "document_url": (
                    f"https://www.sec.gov/Archives/edgar/data/"
                    f"{int(submissions.get('cik', 0))}/{acc_clean}/{documents[i]}"
                    if i < len(documents) else None
                ),
            })
        if len(filings) >= count:
            break

    return filings


def extract_xbrl_metric(us_gaap: dict, key: str) -> list[dict]:
    """
    Pull annual 10-K entries for a given XBRL metric key.
    Converts values from USD to millions and returns only 10-K filings.
    Falls back to empty list if the metric doesn't exist for this company.
    """
    entries = us_gaap.get(key, {}).get("units", {}).get("USD", [])
    results = []
    seen = set()  # deduplicate by fiscal_year

    for e in entries:
        # Only annual 10-K figures, skip amendments (10-K/A) and quarterly
        if e.get("form") != "10-K":
            continue
        fy = e.get("fy")
        if not fy or fy in seen:
            continue
        seen.add(fy)
        results.append({
            "value":       round(e["val"] / 1_000_000, 2),  # USD → millions
            "fiscal_year": fy,
            "period":      e.get("fp", "FY"),               # FY, Q1, Q2 etc.
            "filed":       e.get("filed", ""),
        })

    # Sort by fiscal year descending — most recent first
    results.sort(key=lambda x: x["fiscal_year"], reverse=True)
    return results


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_filings_list(ticker: str, count: int = 5) -> dict:
    """
    List the most recent SEC filings (10-K and 10-Q combined) for a company.

    Returns filing dates, accession numbers, and direct EDGAR URLs.
    Works for US-listed stocks only.

    Example: get_filings_list("AAPL", count=5)
    """
    cache_key = f"sec:list:{ticker.upper()}:{count}"
    cached = from_cache(cache_key)
    if cached:
        cached["from_cache"] = True
        return cached

    cik = get_cik(ticker)
    if not cik:
        return {"ticker": ticker, "error": f"Ticker '{ticker}' not found in SEC database. US tickers only."}

    submissions = fetch_submissions(cik)
    if not submissions:
        return {"ticker": ticker, "error": "Could not fetch submissions from SEC EDGAR."}

    # Grab recent 10-K and 10-Q filings together
    tenk  = extract_filings(submissions, "10-K", count=count)
    tenq  = extract_filings(submissions, "10-Q", count=count)

    # Merge and sort by date descending
    all_filings = sorted(tenk + tenq, key=lambda x: x["filed_date"], reverse=True)

    company_name = submissions.get("name", ticker)

    data = {
        "ticker":       ticker,
        "cik":          cik,
        "company_name": company_name,
        "filings":      all_filings[:count],
        "from_cache":   False,
    }
    to_cache(cache_key, data, TTL_FILINGS)
    return data


@mcp.tool()
def get_10k(ticker: str) -> dict:
    """
    Get the most recent 10-K (annual report) filing for a company.

    Returns filing date, accession number, and a direct link to the document.
    The document URL can be passed to LlamaIndex for PDF ingestion.

    Example: get_10k("MSFT")
    """
    cache_key = f"sec:10k:{ticker.upper()}"
    cached = from_cache(cache_key)
    if cached:
        cached["from_cache"] = True
        return cached

    cik = get_cik(ticker)
    if not cik:
        return {"ticker": ticker, "error": f"Ticker '{ticker}' not found in SEC database."}

    submissions = fetch_submissions(cik)
    if not submissions:
        return {"ticker": ticker, "error": "Could not fetch submissions from SEC EDGAR."}

    filings = extract_filings(submissions, "10-K", count=1)
    if not filings:
        return {"ticker": ticker, "error": "No 10-K filings found."}

    latest = filings[0]
    company_name = submissions.get("name", ticker)

    data = {
        "ticker":        ticker,
        "cik":           cik,
        "company_name":  company_name,
        "form":          "10-K",
        "filed_date":    latest["filed_date"],
        "accession":     latest["accession"],
        "document_url":  latest["document_url"],
        # Human-readable EDGAR page for this filing
        "edgar_url":     f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=10-K&dateb=&owner=include&count=5",
        "from_cache":    False,
    }
    to_cache(cache_key, data, TTL_FILINGS)
    return data


@mcp.tool()
def get_10q(ticker: str) -> dict:
    """
    Get the most recent 10-Q (quarterly report) filing for a company.

    Returns filing date, accession number, and a direct link to the document.

    Example: get_10q("GOOGL")
    """
    cache_key = f"sec:10q:{ticker.upper()}"
    cached = from_cache(cache_key)
    if cached:
        cached["from_cache"] = True
        return cached

    cik = get_cik(ticker)
    if not cik:
        return {"ticker": ticker, "error": f"Ticker '{ticker}' not found in SEC database."}

    submissions = fetch_submissions(cik)
    if not submissions:
        return {"ticker": ticker, "error": "Could not fetch submissions from SEC EDGAR."}

    filings = extract_filings(submissions, "10-Q", count=1)
    if not filings:
        return {"ticker": ticker, "error": "No 10-Q filings found."}

    latest = filings[0]
    company_name = submissions.get("name", ticker)

    data = {
        "ticker":        ticker,
        "cik":           cik,
        "company_name":  company_name,
        "form":          "10-Q",
        "filed_date":    latest["filed_date"],
        "accession":     latest["accession"],
        "document_url":  latest["document_url"],
        "edgar_url":     f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=10-Q&dateb=&owner=include&count=5",
        "from_cache":    False,
    }
    to_cache(cache_key, data, TTL_FILINGS)
    return data


@mcp.tool()
def get_financial_facts(ticker: str) -> dict:
    """
    Fetch structured financial facts from the SEC EDGAR XBRL API.

    Returns key financial metrics (revenue, net income, operating income,
    gross profit, assets, debt, cash flow) for all available fiscal years.
    Values are in millions USD. Only annual 10-K figures are returned.

    This data is used to populate the financial_facts table in Postgres
    for Route 2 (SQL) queries — no hallucination possible, always exact.

    Example: get_financial_facts("TSLA")
    """
    cache_key = f"sec:facts:{ticker.upper()}"
    cached = from_cache(cache_key)
    if cached:
        cached["from_cache"] = True
        return cached

    cik = get_cik(ticker)
    if not cik:
        return {"ticker": ticker, "error": f"Ticker '{ticker}' not found in SEC database."}

    try:
        # XBRL company facts endpoint — completely separate from submissions
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        resp = httpx.get(url, headers=SEC_HEADERS, timeout=30)
        resp.raise_for_status()
        raw = resp.json()

        us_gaap = raw.get("facts", {}).get("us-gaap", {})
        company_name = raw.get("entityName", ticker)

        # ── Core metrics — XBRL standard keys ─────────────────────────────────
        # Some companies use different XBRL keys for the same concept.
        # We try multiple keys and take the first one that returns data.

        def try_keys(*keys) -> list[dict]:
            for key in keys:
                result = extract_xbrl_metric(us_gaap, key)
                if result:
                    return result
            return []

        facts = {
            # Income statement
            "total_revenue": try_keys(
                "Revenues",                          # AMZN, GOOGL
                "RevenueFromContractWithCustomerExcludingAssessedTax",  # TSLA, AAPL
                "SalesRevenueNet",                   # older filings
            ),
            "net_income": try_keys(
                "NetIncomeLoss",
                "ProfitLoss",
            ),
            "operating_income": try_keys(
                "OperatingIncomeLoss",
            ),
            "gross_profit": try_keys(
                "GrossProfit",
            ),
            "research_and_development": try_keys(
                "ResearchAndDevelopmentExpense",
            ),

            # Balance sheet
            "total_assets": try_keys(
                "Assets",
            ),
            "total_liabilities": try_keys(
                "Liabilities",
            ),
            "long_term_debt": try_keys(
                "LongTermDebt",
                "LongTermDebtNoncurrent",
            ),
            "cash_and_equivalents": try_keys(
                "CashAndCashEquivalentsAtCarryingValue",
                "CashCashEquivalentsAndShortTermInvestments",
            ),

            # Cash flow
            "operating_cash_flow": try_keys(
                "NetCashProvidedByUsedInOperatingActivities",
            ),
            "capital_expenditure": try_keys(
                "PaymentsToAcquirePropertyPlantAndEquipment",
            ),
        }

        # Remove metrics with no data for this company
        facts = {k: v for k, v in facts.items() if v}

        data = {
            "ticker":       ticker,
            "cik":          cik,
            "company_name": company_name,
            "unit":         "millions_usd",
            "facts":        facts,
            "from_cache":   False,
        }

        to_cache(cache_key, data, TTL_FACTS)
        return data

    except Exception as e:
        logger.error("get_financial_facts failed for %s: %s", ticker, e)
        return {"ticker": ticker, "error": str(e)}


# ── Tool 5: get_insider_trades ────────────────────────────────────────────────

@mcp.tool()
async def get_insider_trades(ticker: str, days_back: int = 90) -> dict:
    """
    Fetch recent SEC Form 4 insider trading filings for a US-listed company.
    Form 4 reports purchases and sales by directors, officers, and 10%+ shareholders.
    No API key required — SEC EDGAR is a free public API.

    Args:
        ticker:    Stock ticker (US only — e.g. 'NVDA', 'AAPL')
        days_back: How many days of filings to retrieve (default 90)

    Returns:
        List of insider transactions with: insider_name, title, transaction_type,
        shares, price_per_share, date
    """
    cache_key = f"insider:{ticker}:{days_back}"
    cached = from_cache(cache_key)
    if cached:
        return cached

    try:
        from datetime import date, timedelta
        import xml.etree.ElementTree as ET

        start_dt = (date.today() - timedelta(days=days_back)).isoformat()

        # Search SEC EDGAR full-text search for Form 4 filings
        search_url = "https://efts.sec.gov/LATEST/search-index"
        params = {
            "q": f'"{ticker}"',
            "forms": "4",
            "dateRange": "custom",
            "startdt": start_dt,
        }
        async with httpx.AsyncClient(headers=SEC_HEADERS, timeout=15) as client:
            resp = await client.get(search_url, params=params)
            resp.raise_for_status()
            hits = resp.json().get("hits", {}).get("hits", [])

        transactions = []
        for hit in hits[:10]:  # cap at 10 most recent
            source = hit.get("_source", {})
            transactions.append({
                "filing_date":    source.get("file_date", ""),
                "insider_name":   source.get("display_names", ["Unknown"])[0] if source.get("display_names") else "Unknown",
                "form_type":      "4",
                "description":    source.get("period_of_report", ""),
            })

        data = {
            "ticker":       ticker,
            "days_back":    days_back,
            "count":        len(transactions),
            "transactions": transactions,
            "from_cache":   False,
        }
        to_cache(cache_key, data, TTL_FILINGS)
        return data

    except Exception as e:
        logger.error("get_insider_trades failed for %s: %s", ticker, e)
        return {"error": str(e), "ticker": ticker, "transactions": []}


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("sec-edgar-mcp server starting...")
    mcp.run(transport="stdio")