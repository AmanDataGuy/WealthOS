# rag/pipeline.py
# End-to-end pipeline:
#   ticker → sec_edgar_server (direct import) → filing URL → download → index into pgvector

import os
import sys
import asyncio
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

# ── Add project root to path so mcp_servers is importable ────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mcp_servers.sec_edgar_server import get_10k, get_10q
from rag.indexer import FilingIndexer

FILINGS_DIR = Path("data/filings")

SEC_HEADERS = {
    "User-Agent": os.getenv("SEC_USER_AGENT", "WealthOS research@wealthos.app"),
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_filing_meta(ticker: str, filing_type: str = "10-K") -> dict:
    """
    Call the EDGAR server functions directly (no HTTP needed — stdio transport).
    Returns the filing metadata dict including document_url.
    """
    if filing_type == "10-K":
        return get_10k(ticker)
    elif filing_type == "10-Q":
        return get_10q(ticker)
    else:
        return {"error": f"Unsupported filing type: {filing_type}"}


async def download_filing(url: str, save_path: Path) -> Path:
    """Download a filing (PDF or HTML) from SEC EDGAR and save locally."""
    save_path.parent.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(
        timeout=120.0,
        follow_redirects=True,
        headers=SEC_HEADERS,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        save_path.write_bytes(resp.content)

    size_kb = len(resp.content) // 1024
    print(f"[pipeline] Downloaded {size_kb} KB → {save_path}")
    return save_path


# ── Main pipeline ─────────────────────────────────────────────────────────────

async def fetch_and_index_filing(ticker: str, filing_type: str = "10-K") -> dict:
    """
    Full pipeline for one ticker:
      1. Get filing URL from sec_edgar_server (direct call)
      2. Download the filing
      3. Index into pgvector via FilingIndexer
    """
    print(f"\n[pipeline] Starting: {ticker} {filing_type}")

    # Step 1 — Get filing metadata
    meta = get_filing_meta(ticker, filing_type)

    if "error" in meta:
        return {"ticker": ticker, "error": meta["error"]}

    pdf_url = meta.get("document_url")
    if not pdf_url:
        return {"ticker": ticker, "error": "No document_url returned by SEC EDGAR server"}

    print(f"[pipeline] Filing URL: {pdf_url}")
    print(f"[pipeline] Filed date: {meta.get('filed_date')}")

    # Step 2 — Download
    # SEC filings can be .htm or .pdf — save with correct extension
    ext = ".pdf" if pdf_url.endswith(".pdf") else ".htm"
    save_path = FILINGS_DIR / f"{ticker}_{filing_type}{ext}"

    try:
        await download_filing(pdf_url, save_path)
    except Exception as e:
        return {"ticker": ticker, "error": f"Download failed: {e}", "url": pdf_url}

    # Step 3 — Index
    indexer = FilingIndexer()
    result = await indexer.index_filing(str(save_path), ticker, filing_type)
    return result


async def index_local_file(file_path: str, ticker: str, filing_type: str = "10-K") -> dict:
    """Index a file you already have locally — skips the download step."""
    indexer = FilingIndexer()
    return await indexer.index_filing(file_path, ticker, filing_type)


async def batch_index(tickers: list[str], filing_type: str = "10-K") -> list[dict]:
    """Index multiple tickers sequentially with a polite delay between requests."""
    results = []
    for ticker in tickers:
        result = await fetch_and_index_filing(ticker, filing_type)
        results.append(result)
        print(f"[pipeline] Done: {ticker} → {result.get('chunks_indexed', 'error')} chunks")
        await asyncio.sleep(2)  # be polite to SEC EDGAR
    return results


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python rag/pipeline.py AAPL                        # fetch from SEC + index 10-K")
        print("  python rag/pipeline.py AAPL 10-Q                   # fetch 10-Q instead")
        print("  python rag/pipeline.py local <path> <ticker> [type] # index local file")
        print("  python rag/pipeline.py batch AAPL MSFT GOOGL       # batch index")
        sys.exit(1)

    mode = sys.argv[1]

    if mode == "local":
        file_path   = sys.argv[2]
        ticker      = sys.argv[3]
        filing_type = sys.argv[4] if len(sys.argv) > 4 else "10-K"
        result = asyncio.run(index_local_file(file_path, ticker, filing_type))
        print(result)

    elif mode == "batch":
        tickers = sys.argv[2:]
        results = asyncio.run(batch_index(tickers))
        for r in results:
            print(r)

    else:
        ticker      = sys.argv[1]
        filing_type = sys.argv[2] if len(sys.argv) > 2 else "10-K"
        result = asyncio.run(fetch_and_index_filing(ticker, filing_type))
        print(result)