#!/usr/bin/env python
# scripts/index_earnings_calls.py
"""
Index the latest earnings call transcript for one or more tickers into Qdrant.

Run:
    python scripts/index_earnings_calls.py --tickers NVDA AAPL MSFT
    python scripts/index_earnings_calls.py --tickers NVDA --company "Nvidia"

Requires: Qdrant running at QDRANT_URL (default localhost:6333)
          FIRECRAWL_API_KEY set (free tier at firecrawl.dev)
"""

import asyncio
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag.earnings_indexer import index_earnings_call


async def main(tickers: list[str], companies: dict[str, str]) -> None:
    print(f"\nIndexing earnings call transcripts for {len(tickers)} ticker(s)\n")

    results = {}
    for i, ticker in enumerate(tickers, 1):
        print(f"  [{i}/{len(tickers)}] {ticker}...", end=" ", flush=True)
        count = await index_earnings_call(ticker, companies.get(ticker, ""))
        results[ticker] = count
        print(f"{count} chunks")
        await asyncio.sleep(2)  # be polite to Firecrawl/fool.com

    print("\n── Summary ──────────────────────────────────")
    success = {t: c for t, c in results.items() if c > 0}
    failed  = [t for t, c in results.items() if c == 0]
    print(f"  Indexed: {len(success)} tickers")
    for ticker, count in sorted(success.items()):
        print(f"    {ticker}: {count} chunks")
    if failed:
        print(f"  Failed ({len(failed)}): {', '.join(failed)}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="+", required=True,
                        help="Tickers to index earnings call transcripts for")
    parser.add_argument("--company", default="",
                        help="Company name hint for the search (applies to all --tickers)")
    args = parser.parse_args()
    company_map = {t: args.company for t in args.tickers}
    asyncio.run(main(args.tickers, company_map))
