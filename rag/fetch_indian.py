# rag/fetch_indian.py
# Generates structured annual report documents for Indian stocks using yfinance
# and indexes them into Qdrant via FilingIndexer.
#
# Usage:
#   python -m rag.fetch_indian SBIN RELIANCE TCS INFY
#   python -m rag.fetch_indian batch SBIN RELIANCE TCS INFY WIPRO HCLTECH
#
# Indian tickers on NSE use the .NS suffix in yfinance (e.g. SBIN.NS).
# This script adds .NS automatically if no suffix is provided.

import sys
import asyncio
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

FILINGS_DIR = PROJECT_ROOT / "data" / "filings"
FILINGS_DIR.mkdir(parents=True, exist_ok=True)


def _nse_ticker(ticker: str) -> str:
    t = ticker.upper()
    if t.endswith(".NS") or t.endswith(".BO"):
        return t
    return f"{t}.NS"


def _safe(val, default="N/A"):
    if val is None or val != val:   # catches NaN
        return default
    return val


def _cr(val) -> str:
    """Convert raw INR value to Crores string."""
    if val is None or val != val:
        return "N/A"
    crore = val / 1e7
    if crore >= 1_00_000:
        return f"₹{crore/1_00_000:.2f} Lakh Cr"
    return f"₹{crore:,.0f} Cr"


def generate_html_report(ticker: str) -> tuple[str, str]:
    """
    Fetch yfinance data for the ticker and return (html_content, company_name).
    ticker should already have .NS suffix.
    """
    import yfinance as yf

    base = ticker.replace(".NS", "").replace(".BO", "")
    print(f"[fetch_indian] Fetching {ticker} from yfinance...")

    t    = yf.Ticker(ticker)
    info = t.info or {}

    company_name   = info.get("longName") or info.get("shortName") or base
    sector         = _safe(info.get("sector"))
    industry       = _safe(info.get("industry"))
    country        = _safe(info.get("country"))
    exchange       = _safe(info.get("exchange"))
    summary        = info.get("longBusinessSummary") or "No description available."
    employees      = _safe(info.get("fullTimeEmployees"))
    website        = _safe(info.get("website"))
    currency       = info.get("currency", "INR")

    # Price & valuation
    price          = _safe(info.get("currentPrice") or info.get("regularMarketPrice"))
    mktcap         = _cr(info.get("marketCap"))
    pe             = _safe(info.get("trailingPE"))
    pb             = _safe(info.get("priceToBook"))
    eps            = _safe(info.get("trailingEps"))
    div_yield      = info.get("dividendYield")
    div_str        = f"{div_yield*100:.2f}%" if div_yield else "N/A"
    wk52h          = _safe(info.get("fiftyTwoWeekHigh"))
    wk52l          = _safe(info.get("fiftyTwoWeekLow"))
    beta           = _safe(info.get("beta"))
    roe            = info.get("returnOnEquity")
    roe_str        = f"{roe*100:.2f}%" if roe else "N/A"
    roa            = info.get("returnOnAssets")
    roa_str        = f"{roa*100:.2f}%" if roa else "N/A"
    profit_margin  = info.get("profitMargins")
    pm_str         = f"{profit_margin*100:.2f}%" if profit_margin else "N/A"
    op_margin      = info.get("operatingMargins")
    om_str         = f"{op_margin*100:.2f}%" if op_margin else "N/A"
    debt_equity    = _safe(info.get("debtToEquity"))
    current_ratio  = _safe(info.get("currentRatio"))

    # Financials
    try:
        financials = t.financials
        fin_html = "<p>Financial statements not available.</p>"
        if financials is not None and not financials.empty:
            rows = []
            for metric in financials.index[:10]:
                row = f"<tr><td>{metric}</td>"
                for col in financials.columns[:4]:
                    val = financials.loc[metric, col]
                    row += f"<td>{_cr(val) if abs(val or 0) > 1e6 else _safe(val)}</td>"
                row += "</tr>"
                rows.append(row)
            col_headers = "".join(f"<th>{str(c)[:10]}</th>" for c in financials.columns[:4])
            fin_html = f"""
            <table border='1'>
              <tr><th>Metric</th>{col_headers}</tr>
              {"".join(rows)}
            </table>"""
    except Exception as e:
        fin_html = f"<p>Could not fetch financials: {e}</p>"

    # Balance sheet
    try:
        bs = t.balance_sheet
        bs_html = "<p>Balance sheet not available.</p>"
        if bs is not None and not bs.empty:
            rows = []
            for metric in bs.index[:10]:
                row = f"<tr><td>{metric}</td>"
                for col in bs.columns[:4]:
                    val = bs.loc[metric, col]
                    row += f"<td>{_cr(val) if abs(val or 0) > 1e6 else _safe(val)}</td>"
                row += "</tr>"
                rows.append(row)
            col_headers = "".join(f"<th>{str(c)[:10]}</th>" for c in bs.columns[:4])
            bs_html = f"""
            <table border='1'>
              <tr><th>Metric</th>{col_headers}</tr>
              {"".join(rows)}
            </table>"""
    except Exception as e:
        bs_html = f"<p>Could not fetch balance sheet: {e}</p>"

    # Cash flow
    try:
        cf = t.cashflow
        cf_html = "<p>Cash flow not available.</p>"
        if cf is not None and not cf.empty:
            rows = []
            for metric in cf.index[:8]:
                row = f"<tr><td>{metric}</td>"
                for col in cf.columns[:4]:
                    val = cf.loc[metric, col]
                    row += f"<td>{_cr(val) if abs(val or 0) > 1e6 else _safe(val)}</td>"
                row += "</tr>"
                rows.append(row)
            col_headers = "".join(f"<th>{str(c)[:10]}</th>" for c in cf.columns[:4])
            cf_html = f"""
            <table border='1'>
              <tr><th>Metric</th>{col_headers}</tr>
              {"".join(rows)}
            </table>"""
    except Exception as e:
        cf_html = f"<p>Could not fetch cash flow: {e}</p>"

    # Recent news headlines
    try:
        news_items = t.news[:8] if t.news else []
        news_html = "<ul>" + "".join(
            f"<li>{n.get('title', '')} ({n.get('publisher', '')})</li>"
            for n in news_items
        ) + "</ul>" if news_items else "<p>No recent news.</p>"
    except Exception:
        news_html = "<p>News unavailable.</p>"

    date_str = datetime.now().strftime("%B %Y")

    html = f"""<!DOCTYPE html>
<html>
<head><title>{company_name} — Annual Report Summary {date_str}</title></head>
<body>

<h1>{company_name} ({base})</h1>
<h2>Company Overview</h2>
<p><strong>Exchange:</strong> {exchange} | <strong>Sector:</strong> {sector} | <strong>Industry:</strong> {industry}</p>
<p><strong>Country:</strong> {country} | <strong>Currency:</strong> {currency} | <strong>Employees:</strong> {employees}</p>
<p><strong>Website:</strong> {website}</p>

<h2>Business Description</h2>
<p>{summary}</p>

<h2>Key Financial Metrics</h2>
<table border='1'>
  <tr><th>Metric</th><th>Value</th></tr>
  <tr><td>Current Price</td><td>₹{price}</td></tr>
  <tr><td>Market Capitalisation</td><td>{mktcap}</td></tr>
  <tr><td>P/E Ratio (Trailing)</td><td>{pe}</td></tr>
  <tr><td>Price to Book</td><td>{pb}</td></tr>
  <tr><td>EPS (Trailing)</td><td>{eps}</td></tr>
  <tr><td>Dividend Yield</td><td>{div_str}</td></tr>
  <tr><td>52-Week High</td><td>₹{wk52h}</td></tr>
  <tr><td>52-Week Low</td><td>₹{wk52l}</td></tr>
  <tr><td>Beta</td><td>{beta}</td></tr>
  <tr><td>Return on Equity (ROE)</td><td>{roe_str}</td></tr>
  <tr><td>Return on Assets (ROA)</td><td>{roa_str}</td></tr>
  <tr><td>Profit Margin</td><td>{pm_str}</td></tr>
  <tr><td>Operating Margin</td><td>{om_str}</td></tr>
  <tr><td>Debt to Equity</td><td>{debt_equity}</td></tr>
  <tr><td>Current Ratio</td><td>{current_ratio}</td></tr>
</table>

<h2>Income Statement (Last 4 Years)</h2>
{fin_html}

<h2>Balance Sheet (Last 4 Years)</h2>
{bs_html}

<h2>Cash Flow Statement (Last 4 Years)</h2>
{cf_html}

<h2>Risk Factors</h2>
<p>Key risks for {company_name} include sector-specific regulatory risks, macroeconomic headwinds
in the Indian economy, currency fluctuations, competitive pressure within the {industry} industry,
and governance/management execution risk. Investors should review the latest annual report and
SEBI filings for detailed risk disclosures.</p>

<h2>Recent News</h2>
{news_html}

<h2>Investment Considerations</h2>
<p>{company_name} operates in the {sector} sector within India's growing economy.
The stock trades on {exchange} at ₹{price} with a market cap of {mktcap}.
With a P/E of {pe} and ROE of {roe_str}, the company's valuation and profitability
metrics should be compared against sector peers before making investment decisions.</p>

</body>
</html>"""

    return html, company_name


async def fetch_and_index(ticker: str) -> dict:
    from rag.indexer import FilingIndexer

    nse = _nse_ticker(ticker)
    base = ticker.upper().split(".")[0]

    try:
        html, company_name = generate_html_report(nse)
    except Exception as e:
        return {"ticker": base, "error": f"yfinance fetch failed: {e}"}

    save_path = FILINGS_DIR / f"{base}_annual.htm"
    save_path.write_text(html, encoding="utf-8")
    print(f"[fetch_indian] Saved {save_path.name} ({len(html)//1024} KB)")

    indexer = FilingIndexer()
    result = await indexer.index_filing(str(save_path), base, "Annual-Report")
    print(f"[fetch_indian] Indexed {base} → {result.get('chunks_indexed', 'error')} chunks")
    return result


async def batch_fetch(tickers: list[str]) -> list[dict]:
    results = []
    for t in tickers:
        result = await fetch_and_index(t)
        results.append(result)
        await asyncio.sleep(1)
    return results


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m rag.fetch_indian SBIN")
        print("  python -m rag.fetch_indian batch SBIN RELIANCE TCS INFY WIPRO HCLTECH")
        sys.exit(1)

    if sys.argv[1].lower() == "batch":
        tickers = sys.argv[2:]
        results = asyncio.run(batch_fetch(tickers))
        for r in results:
            print(r)
    else:
        result = asyncio.run(fetch_and_index(sys.argv[1]))
        print(result)
