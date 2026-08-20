#!/usr/bin/env python
# scripts/index_indian_stocks.py
"""
Index annual reports for Indian companies into Qdrant via Firecrawl
search-then-download (see rag/bse_indexer.py).

Run once on EC2 after Qdrant is live:
    python scripts/index_indian_stocks.py
    python scripts/index_indian_stocks.py --tickers TCS.NS:"Tata Consultancy Services" INFY.NS:Infosys

Requires: Qdrant running at QDRANT_URL (default localhost:6333)
          PostgreSQL running at WEALTHOS_DB_URL
          FIRECRAWL_API_KEY set
"""

import asyncio
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag.bse_indexer import index_indian_company

DEFAULT_COMPANIES = {
    "TCS.NS": "Tata Consultancy Services",
    "INFY.NS": "Infosys",
    "HDFCB.NS": "HDFC Bank",
    "RELIANCE.NS": "Reliance Industries",
    "WIPRO.NS": "Wipro",
}


async def main(companies: dict[str, str]) -> None:
    print(f"\nIndexing {len(companies)} Indian companies\n")

    results = {}
    for i, (ticker, name) in enumerate(companies.items(), 1):
        print(f"  [{i}/{len(companies)}] {ticker}...", end=" ", flush=True)
        count = await index_indian_company(ticker, name)
        results[ticker] = count
        print(f"{count} chunks")
        await asyncio.sleep(2)  # be polite to IR servers

    print("\n── Summary ──────────────────────────────────")
    success = {t: c for t, c in results.items() if c > 0}
    failed  = [t for t, c in results.items() if c == 0]
    print(f"  Indexed: {len(success)} companies")
    for ticker, count in sorted(success.items()):
        print(f"    {ticker}: {count} chunks")
    if failed:
        print(f"  Failed ({len(failed)}): {', '.join(failed)}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="+", default=None,
                        help='Specific tickers as TICKER:"Company Name" (default: 5 built-in)')
    args = parser.parse_args()
    if args.tickers:
        companies = dict(t.split(":", 1) for t in args.tickers)
    else:
        companies = DEFAULT_COMPANIES
    asyncio.run(main(companies))
