# rag/earnings_indexer.py
"""
Earnings Call Transcript Indexer.

Finds and indexes the latest earnings call transcript for a ticker using
Firecrawl's search + scrape endpoints against The Motley Fool's free
transcript archive (fool.com/earnings/call-transcripts/) — no paid
transcript API, reuses the same FIRECRAWL_API_KEY already used by
mcp_servers/news_server.py for Reddit sentiment.

Verified live (2026-08-19) against real fool.com pages:
  - /v1/search with `site:fool.com/earnings/call-transcripts {ticker}`
    returns real transcript URLs.
  - /v1/scrape on a transcript URL returns markdown with a
    "## Full Conference Call Transcript" heading marking the real
    transcript body, ending at a "## Read Next" boilerplate heading.

Usage:
    from rag.earnings_indexer import index_earnings_call
    count = await index_earnings_call("NVDA", "Nvidia")
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "")
FIRECRAWL_BASE    = "https://api.firecrawl.dev/v1"
FILING_TYPE       = "EARNINGS_CALL"

# Markers bounding the real transcript body within the scraped page markdown —
# everything before TRANSCRIPT_START is site chrome (nav, ticker widgets);
# everything from TRANSCRIPT_END onward is "Read Next" / related-content boilerplate.
TRANSCRIPT_START = "## Full Conference Call Transcript"
TRANSCRIPT_END   = "## Read Next"


async def find_transcript_url(ticker: str, company_name: str = "") -> str | None:
    """
    Search fool.com for the most recent earnings call transcript for a ticker.
    Returns the transcript URL, or None if nothing found / Firecrawl unavailable.
    """
    if not FIRECRAWL_API_KEY:
        logger.warning("[earnings_indexer] FIRECRAWL_API_KEY not set — skipping")
        return None

    query = f"site:fool.com/earnings/call-transcripts {company_name or ticker} {ticker}".strip()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{FIRECRAWL_BASE}/search",
                headers={"Authorization": f"Bearer {FIRECRAWL_API_KEY}"},
                json={"query": query, "limit": 5},
            )
            resp.raise_for_status()
            results = resp.json().get("data", [])
    except Exception as e:
        logger.warning("[earnings_indexer] Firecrawl search failed for %s: %s", ticker, e)
        return None

    for r in results:
        url = r.get("url", "")
        if "/earnings/call-transcripts/" in url:
            return url

    logger.info("[earnings_indexer] No transcript URL found for %s", ticker)
    return None


def _extract_transcript_body(markdown: str) -> str:
    """
    Pull just the real transcript text out of a scraped fool.com page's markdown,
    trimming the site-chrome header and the "Read Next" footer boilerplate.
    Falls back to the full markdown if the expected markers aren't found —
    better a noisier chunk set than silently indexing nothing.
    """
    start = markdown.find(TRANSCRIPT_START)
    if start == -1:
        return markdown.strip()
    body = markdown[start + len(TRANSCRIPT_START):]

    end = body.find(TRANSCRIPT_END)
    if end != -1:
        body = body[:end]

    return body.strip()


async def index_earnings_call(ticker: str, company_name: str = "") -> int:
    """
    Find, scrape, and index the latest earnings call transcript for a ticker.
    Returns the number of chunks indexed (0 on failure).
    """
    if not FIRECRAWL_API_KEY:
        logger.warning("[earnings_indexer] FIRECRAWL_API_KEY not set — skipping %s", ticker)
        return 0

    url = await find_transcript_url(ticker, company_name)
    if not url:
        return 0

    logger.info("[earnings_indexer] Scraping %s transcript: %s", ticker, url)
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{FIRECRAWL_BASE}/scrape",
                headers={"Authorization": f"Bearer {FIRECRAWL_API_KEY}"},
                json={"url": url, "formats": ["markdown"]},
            )
            resp.raise_for_status()
            markdown = resp.json().get("data", {}).get("markdown", "")
    except Exception as e:
        logger.warning("[earnings_indexer] Scrape failed for %s: %s", ticker, e)
        return 0

    body = _extract_transcript_body(markdown)
    if len(body.split()) < 200:
        logger.warning("[earnings_indexer] Transcript body too short for %s (%d words) — skipping", ticker, len(body.split()))
        return 0

    try:
        from rag.indexer import FilingIndexer
        indexer = FilingIndexer()
        result = await indexer.index_text(
            text=body,
            ticker=ticker,
            filing_type=FILING_TYPE,
            source_file=url,
        )
        count = result.get("total_points", 0)
        logger.info("[earnings_indexer] Indexed %d chunks for %s", count, ticker)
        return count
    except Exception as e:
        logger.error("[earnings_indexer] Indexing failed for %s: %s", ticker, e)
        return 0
