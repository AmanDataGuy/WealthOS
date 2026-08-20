# rag/bse_indexer.py
"""
Indian Annual Report Downloader + Indexer.

Downloads annual reports for Indian companies and indexes them into Qdrant.
Fixes the ~8-chunk problem for Indian stocks (vs 180-287 chunks for US).

Was a hardcoded BSE scrip-ID map + guessed URL pattern with no discovery
step — broke silently the moment BSE changed its file-naming convention, and
an NSE "fallback" that found a PDF link but never downloaded it. Replaced
with search-then-download, the same pattern already proven in
rag/earnings_indexer.py: Firecrawl /v1/search finds the real, current PDF
URL (usually on the company's own investor-relations site, not BSE's —
verified live, e.g. TCS's real annual report lives on tcs.com, not a
guessable bseindia.com path). No scrip-ID map to maintain.

One thing NOT solved by Firecrawl: some IR sites 403 a bare "requests"
User-Agent even though the PDF itself is public (verified live on tcs.com).
A full browser-like header set gets past this — it's basic bot-filtering,
not real protection — so PDFs are downloaded directly via httpx rather than
through Firecrawl's own /v1/scrape (which times out on large PDFs anyway).

Usage:
    from rag.bse_indexer import index_indian_company
    count = await index_indian_company("TCS.NS", "Tata Consultancy Services")

    # Or index a batch:
    python scripts/index_indian_stocks.py
"""

import logging
import os
import tempfile
from datetime import date
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# A full browser header set — verified live: a bare "requests"-style
# User-Agent gets 403'd by some IR sites that serve the exact same PDF at
# 200 to a normal-looking browser request.
_DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/pdf,text/html,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


async def find_annual_report_url(ticker: str, company_name: str = "") -> str | None:
    """Search for a company's most recent annual report PDF via Firecrawl."""
    api_key = os.getenv("FIRECRAWL_API_KEY", "")
    if not api_key:
        logger.warning("[bse_indexer] FIRECRAWL_API_KEY not set — skipping %s", ticker)
        return None

    query = f"{company_name or ticker} annual report pdf".strip()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.firecrawl.dev/v1/search",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"query": query, "limit": 5},
            )
            resp.raise_for_status()
            results = resp.json().get("data", [])
    except Exception as e:
        logger.warning("[bse_indexer] Firecrawl search failed for %s: %s", ticker, e)
        return None

    for r in results:
        url = r.get("url", "")
        if url.lower().endswith(".pdf"):
            return url

    logger.info("[bse_indexer] No direct PDF link found for %s", ticker)
    return None


async def index_indian_company(ticker: str, company_name: str = "") -> int:
    """
    Find, download, and index the latest annual report for an Indian company.
    Returns the number of chunks indexed (0 on failure).
    """
    url = await find_annual_report_url(ticker, company_name)
    if not url:
        return 0

    logger.info("[bse_indexer] Downloading %s annual report: %s", ticker, url)

    try:
        async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
            resp = await client.get(url, headers=_DOWNLOAD_HEADERS)
            resp.raise_for_status()
    except Exception as e:
        logger.warning("[bse_indexer] Download failed for %s: %s", ticker, e)
        return 0

    content = resp.content
    if len(content) < 5000:
        logger.warning("[bse_indexer] PDF too small for %s (%d bytes) — likely an error page", ticker, len(content))
        return 0

    # tempfile, not a hardcoded /tmp/ path — the old version was POSIX-only
    # and broke on Windows.
    tmp_path = Path(tempfile.gettempdir()) / f"{ticker.replace('.', '_')}_annual_report.pdf"
    tmp_path.write_bytes(content)
    logger.info("[bse_indexer] Downloaded %d bytes — indexing...", len(content))

    try:
        from rag.indexer import FilingIndexer
        indexer = FilingIndexer()
        result = await indexer.index_filing(
            file_path=str(tmp_path),
            ticker=ticker,
            filing_type="annual_report",
            filing_date=date.today().isoformat(),
        )
        count = result.get("total_points", 0)
        logger.info("[bse_indexer] Indexed %d chunks for %s", count, ticker)
        await _update_indexed_tickers(ticker, count, str(date.today().year), "annual_report_pdf")
        return count
    except Exception as e:
        logger.error("[bse_indexer] Indexing failed for %s: %s", ticker, e)
        return 0
    finally:
        tmp_path.unlink(missing_ok=True)


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
