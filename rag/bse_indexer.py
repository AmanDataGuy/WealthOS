# rag/bse_indexer.py
"""
BSE India Annual Report Downloader + Indexer.

Downloads annual reports from BSE India and indexes them into Qdrant.
Fixes the ~8-chunk problem for Indian stocks (vs 180-287 chunks for US).

Target: 30 major Indian companies across 6 sectors.

Usage:
    from rag.bse_indexer import index_indian_company
    count = await index_indian_company("TCS.NS", year=2024)

    # Or index all 30 at once:
    python scripts/index_indian_stocks.py
"""

import asyncio
import logging
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# BSE scrip IDs — map NSE ticker to BSE scrip_id
# Verify annually: https://www.bseindia.com/corporates/List_Scrips.html
BSE_SCRIP_MAP: dict[str, str] = {
    # Large Cap
    "TCS.NS":        "532540",
    "INFY.NS":       "500209",
    "HDFCB.NS":      "500180",
    "RELIANCE.NS":   "500325",
    "WIPRO.NS":      "507685",
    # IT / Tech
    "TECHM.NS":      "532755",
    "HCLTECH.NS":    "532281",
    "LTIM.NS":       "540005",
    "MPHASIS.NS":    "526299",
    # Finance
    "AXISBANK.NS":   "532215",
    "ICICIBANK.NS":  "532174",
    "KOTAKBANK.NS":  "500247",
    "SBIN.NS":       "500112",
    "BAJFINANCE.NS": "500034",
    # Consumer
    "ITC.NS":        "500875",
    "HINDUNILVR.NS": "500696",
    "NESTLEIND.NS":  "500790",
    "MARICO.NS":     "531642",
    "BRITANNIA.NS":  "500825",
    # Infra / Energy
    "NTPC.NS":       "532555",
    "POWERGRID.NS":  "532898",
    "ONGC.NS":       "500312",
    "COALINDIA.NS":  "533278",
    "BPCL.NS":       "500547",
    # Auto / Pharma
    "MARUTI.NS":     "532500",
    "TATAMOTORS.NS": "500570",
    "SUNPHARMA.NS":  "524715",
    "DRREDDY.NS":    "500124",
    "CIPLA.NS":      "500087",
}

HEADERS = {"User-Agent": "WealthOS research@wealthos.app"}


def get_bse_pdf_url(ticker: str, year: int = 2024) -> str | None:
    """Return BSE annual report PDF URL or None if ticker not in map."""
    scrip_id = BSE_SCRIP_MAP.get(ticker)
    if not scrip_id:
        return None
    return (
        f"https://www.bseindia.com/bseplus/AnnualReport/"
        f"{scrip_id}/{year}/{scrip_id}{year}.pdf"
    )


async def index_indian_company(ticker: str, year: int = 2024) -> int:
    """
    Download the annual report PDF for a BSE-listed company and index it into Qdrant.
    Returns the number of chunks indexed (0 on failure).
    """
    url = get_bse_pdf_url(ticker, year)
    if not url:
        logger.warning("[bse_indexer] %s not in BSE_SCRIP_MAP — skipping", ticker)
        return 0

    logger.info("[bse_indexer] Downloading %s annual report (%d)...", ticker, year)

    try:
        resp = requests.get(url, timeout=45, headers=HEADERS)
        resp.raise_for_status()
    except Exception as e:
        logger.warning("[bse_indexer] Download failed for %s: %s", ticker, e)
        # Try NSE fallback
        return await _try_nse_fallback(ticker, year)

    content = resp.content
    if len(content) < 5000:
        logger.warning("[bse_indexer] PDF too small for %s (%d bytes) — likely error page", ticker, len(content))
        return await _try_nse_fallback(ticker, year)

    # Save to temp path and index
    tmp_path = Path(f"/tmp/{ticker.replace('.', '_')}_{year}.pdf")
    tmp_path.write_bytes(content)
    logger.info("[bse_indexer] Downloaded %d bytes — indexing...", len(content))

    try:
        from rag.indexer import FilingIndexer
        indexer = FilingIndexer()
        result = await indexer.index_filing(
            file_path=str(tmp_path),
            ticker=ticker,
            filing_type="annual_report",
            filing_date=f"{year}-04-01",
        )
        count = result.get("total_points", 0)
        logger.info("[bse_indexer] Indexed %d chunks for %s", count, ticker)

        # Update indexed_tickers table
        await _update_indexed_tickers(ticker, count, str(year), "bse_pdf")
        return count
    except Exception as e:
        logger.error("[bse_indexer] Indexing failed for %s: %s", ticker, e)
        return 0
    finally:
        tmp_path.unlink(missing_ok=True)


async def _try_nse_fallback(ticker: str, year: int) -> int:
    """Try NSE annual reports API as fallback when BSE URL fails."""
    symbol = ticker.replace(".NS", "")
    nse_url = f"https://www.nseindia.com/api/annual-reports?index=equities&symbol={symbol}"
    try:
        resp = requests.get(nse_url, timeout=15, headers={**HEADERS, "Referer": "https://www.nseindia.com"})
        if resp.status_code == 200:
            data = resp.json()
            reports = data.get("data", [])
            if reports:
                pdf_link = reports[0].get("fileName", "")
                if pdf_link:
                    logger.info("[bse_indexer] NSE fallback found PDF for %s", ticker)
                    return 0  # TODO: download and index NSE PDF
    except Exception:
        pass
    logger.warning("[bse_indexer] No annual report found for %s (BSE + NSE both failed)", ticker)
    return 0


async def _update_indexed_tickers(ticker: str, chunk_count: int, filing_year: str, source: str) -> None:
    """Upsert into indexed_tickers table to track indexing status."""
    try:
        import os
        import asyncpg
        db_url = os.getenv("WEALTHOS_DB_URL", "").replace("postgresql+asyncpg://", "postgresql://")
        if not db_url:
            return
        conn = await asyncpg.connect(db_url)
        await conn.execute(
            """
            INSERT INTO indexed_tickers (ticker, chunk_count, last_indexed_at, filing_year, data_source, status)
            VALUES ($1, $2, NOW(), $3, $4, 'active')
            ON CONFLICT (ticker) DO UPDATE
              SET chunk_count = $2, last_indexed_at = NOW(), filing_year = $3,
                  data_source = $4, status = 'active'
            """,
            ticker, chunk_count, filing_year, source
        )
        await conn.close()
    except Exception as e:
        logger.warning("[bse_indexer] Could not update indexed_tickers: %s", e)
