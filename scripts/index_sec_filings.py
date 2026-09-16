"""
Batch-download and index the latest 10-K for a list of large US tickers.

Reuses the existing, already-tested pieces rather than reimplementing
anything: mcp_servers.sec_edgar_server.get_10k() for CIK resolution + the
document URL, and rag.indexer.FilingIndexer.index_filing() for the same
hierarchical-chunk embed/upsert pipeline that already indexed AAPL.

Usage:
    python scripts/index_sec_filings.py
    python scripts/index_sec_filings.py --tickers MSFT GOOGL AMZN
"""

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_servers.sec_edgar_server import get_10k, SEC_HEADERS
from rag.indexer import FilingIndexer

# Top 20 by market cap, mixing sectors so RAG test queries about
# "cloud", "advertising", "risk factors" etc. have real filings to hit.
DEFAULT_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK-B",
    "AVGO", "JPM", "LLY", "V", "WMT", "XOM", "UNH", "MA", "PG", "COST",
    "JNJ", "HD",
]

FILINGS_DIR = Path(__file__).resolve().parent.parent / "data" / "filings"


async def index_one(ticker: str, indexer: FilingIndexer) -> tuple[str, int]:
    info = get_10k(ticker)
    if "error" in info:
        print(f"  {ticker}: SKIP — {info['error']}")
        return ticker, 0

    url = info["document_url"]
    ext = ".htm" if url.lower().endswith((".htm", ".html")) else Path(url).suffix or ".htm"
    dest = FILINGS_DIR / f"{ticker}_10-K{ext}"

    if not dest.exists():
        try:
            async with httpx.AsyncClient(headers=SEC_HEADERS, timeout=30, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                dest.write_bytes(resp.content)
        except Exception as e:
            print(f"  {ticker}: SKIP — download failed: {e}")
            return ticker, 0

    result = await indexer.index_filing(
        str(dest), ticker, "10-K", filing_date=info.get("filed_date", ""),
    )
    count = result.get("chunks_indexed", 0)
    if "error" in result:
        print(f"  {ticker}: SKIP — index failed: {result['error']}")
        return ticker, 0
    print(f"  {ticker}: {count} chunks indexed ({info.get('company_name', ticker)}, filed {info.get('filed_date', '?')})")
    return ticker, count


async def main(tickers: list[str]):
    FILINGS_DIR.mkdir(parents=True, exist_ok=True)
    indexer = FilingIndexer()
    print(f"Indexing 10-K filings for {len(tickers)} companies...")
    results = {}
    for t in tickers:
        # SEC rate limit is per-second, not per-batch — small delay between
        # requests is politer than hammering data.sec.gov in a tight loop.
        ticker, count = await index_one(t, indexer)
        results[ticker] = count
        await asyncio.sleep(0.3)

    success = {t: c for t, c in results.items() if c > 0}
    failed = [t for t in results if results[t] == 0]
    print(f"\nIndexed: {len(success)}/{len(tickers)} companies")
    if failed:
        print(f"Failed/skipped ({len(failed)}): {', '.join(failed)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="+", default=None,
                        help="Specific tickers (default: built-in top-20 list)")
    args = parser.parse_args()
    asyncio.run(main(args.tickers or DEFAULT_TICKERS))
