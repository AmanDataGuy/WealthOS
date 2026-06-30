#!/usr/bin/env python
# scripts/index_indian_stocks.py
"""
Index annual reports for 30 major Indian companies into Qdrant.

Run once on EC2 after Qdrant is live:
    python scripts/index_indian_stocks.py
    python scripts/index_indian_stocks.py --tickers TCS.NS INFY.NS   # specific tickers
    python scripts/index_indian_stocks.py --year 2023                 # different year

Requires: Qdrant running at QDRANT_URL (default localhost:6333)
          PostgreSQL running at WEALTHOS_DB_URL
"""

import asyncio
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag.bse_indexer import BSE_SCRIP_MAP, index_indian_company


async def main(tickers: list[str], year: int) -> None:
    print(f"\nIndexing {len(tickers)} Indian companies (year={year})\n")

    results = {}
    for i, ticker in enumerate(tickers, 1):
        print(f"  [{i}/{len(tickers)}] {ticker}...", end=" ", flush=True)
        count = await index_indian_company(ticker, year)
        results[ticker] = count
        print(f"{count} chunks")
        await asyncio.sleep(2)  # be polite to BSE servers

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
    parser.add_argument("--tickers", nargs="+", default=list(BSE_SCRIP_MAP.keys()),
                        help="Specific tickers to index (default: all 30)")
    parser.add_argument("--year", type=int, default=2024,
                        help="Annual report year (default: 2024)")
    args = parser.parse_args()
    asyncio.run(main(args.tickers, args.year))
