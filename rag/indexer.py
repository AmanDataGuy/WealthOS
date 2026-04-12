# rag/indexer.py
# Ingestion engine — reads a PDF or HTML filing, chunks it, embeds via Ollama mxbai-embed-large,
# stores chunks + vectors into pgvector (document_embeddings table).
#
# What changed from v1:
#   - Added SECTION_HEADERS dict — maps keywords to section names
#   - Added detect_section() — figures out which section a chunk belongs to
#   - INSERT now stores section in both the metadata JSON and the section column

import os
import uuid
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import httpx
from pypdf import PdfReader
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("WEALTHOS_DB_URL", "postgresql://postgres:postgres@localhost:5432/wealthos")
OLLAMA_URL   = os.getenv("OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL  = "mxbai-embed-large"

CHUNK_SIZE    = 512   # words
CHUNK_OVERLAP = 50


# ── Section Detection ─────────────────────────────────────────────────────────
# Maps keywords found in 10-K headers to clean section names.
# Order matters — more specific phrases should come before generic ones.

SECTION_HEADERS = {
    "consolidated statements of operations":  "income_statement",
    "consolidated statements of income":      "income_statement",
    "results of operations":                  "income_statement",
    "consolidated balance sheet":             "balance_sheet",
    "consolidated statements of financial":   "balance_sheet",
    "consolidated statements of cash flow":   "cash_flow",
    "cash flows from operating":              "cash_flow",
    "management":                             "md_and_a",         # catches MD&A header
    "risk factors":                           "risk_factors",
    "quantitative and qualitative":           "market_risk",
    "legal proceedings":                      "legal",
    "controls and procedures":                "controls",
    "business overview":                      "business",
    "item 1.":                                "business",         # Item 1 = Business section
    "properties":                             "properties",
    "selected financial data":                "financial_summary",
}


def detect_section(chunk_text: str) -> str:
    """
    Scan the chunk for known 10-K section headers.
    Returns a clean section name or 'unknown' if no match found.

    Examples:
        "CONSOLIDATED STATEMENTS OF OPERATIONS..."  → 'income_statement'
        "RISK FACTORS We face risks related to..."  → 'risk_factors'
        "some random paragraph..."                  → 'unknown'
    """
    text_lower = chunk_text.lower()
    for keyword, section_name in SECTION_HEADERS.items():
        if keyword in text_lower:
            return section_name
    return "unknown"


# ── Text extraction ───────────────────────────────────────────────────────────

def extract_text_from_pdf(file_path: str) -> str:
    """Extract all text from a PDF file."""
    reader = PdfReader(file_path)
    pages  = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n".join(pages)


def extract_text_from_html(file_path: str) -> str:
    """
    Extract readable text from an SEC EDGAR HTML filing.
    Strips all tags, scripts, and styles — leaves only the text content.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise ImportError("Run: pip install beautifulsoup4")

    raw = Path(file_path).read_bytes()

    # Try UTF-8 first, fall back to latin-1 (SEC filings are sometimes latin-1)
    try:
        html = raw.decode("utf-8")
    except UnicodeDecodeError:
        html = raw.decode("latin-1")

    soup = BeautifulSoup(html, "html.parser")

    # Remove noise elements
    for tag in soup.find_all(True):
        if tag.name and (tag.name.startswith("ix:") or tag.name.startswith("xbrl")):
            tag.decompose()

    text = soup.get_text(separator="\n")

    # Collapse excessive blank lines
    lines   = [line.strip() for line in text.splitlines()]
    cleaned = "\n".join(line for line in lines if line)

    return cleaned


def extract_text(file_path: str) -> str:
    """Route to the correct extractor based on file extension."""
    ext = Path(file_path).suffix.lower()
    if ext == ".pdf":
        return extract_text_from_pdf(file_path)
    elif ext in (".htm", ".html"):
        return extract_text_from_html(file_path)
    else:
        # Try HTML as fallback for unknown extensions
        print(f"[indexer] Unknown extension '{ext}', trying HTML parser...")
        return extract_text_from_html(file_path)


# ── Chunking ──────────────────────────────────────────────────────────────────

def chunk_text(text: str, chunk_size: int = 100, overlap: int = 20) -> list[str]:
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end   = start + chunk_size
        chunk = " ".join(words[start:end])

        # Skip chunks that are mostly XBRL/metadata (no spaces in long tokens)
        long_tokens = [w for w in chunk.split() if len(w) > 30]
        if len(long_tokens) > 10:
            start = end - overlap
            continue

        if chunk.strip():
            chunks.append(chunk)

        start = end - overlap
    return chunks


# ── Embedding ─────────────────────────────────────────────────────────────────

async def get_embedding(text: str, client: httpx.AsyncClient) -> list[float]:
    """Call Ollama /api/embeddings and return the vector."""
    resp = await client.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60.0,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


# ── DB helper ─────────────────────────────────────────────────────────────────

def _strip_asyncpg_prefix(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


# ── Main Class ────────────────────────────────────────────────────────────────

class FilingIndexer:
    def __init__(self):
        self.db_url = _strip_asyncpg_prefix(DATABASE_URL)

    async def index_filing(self, file_path: str, ticker: str, filing_type: str) -> dict:
        """
        Full pipeline:
          1. Extract text from PDF or HTML
          2. Chunk into ~512-word segments with overlap
          3. Detect which section each chunk belongs to
          4. Embed each chunk via Ollama mxbai-embed-large
          5. Store in document_embeddings (pgvector) with section tag
        """
        file_path = str(file_path)

        # 1. Extract text
        print(f"[indexer] Extracting text from {file_path}...")
        try:
            full_text = extract_text(file_path)
        except Exception as e:
            return {"error": f"Text extraction failed: {e}", "ticker": ticker}

        if not full_text.strip():
            return {"error": "No text extracted from file", "ticker": ticker}

        print(f"[indexer] Extracted {len(full_text):,} characters")

        # 2. Chunk
        chunks = chunk_text(full_text)
        print(f"[indexer] {len(chunks)} chunks created for {ticker} {filing_type}")

        # 3. Embed + store
        conn = await asyncpg.connect(self.db_url)
        try:
            async with httpx.AsyncClient() as client:

                # Warm up — wait for model to load before processing chunks
                print("[indexer] Warming up embedding model...")
                for attempt in range(10):
                    try:
                        await get_embedding("warmup", client)
                        print("[indexer] Model ready.")
                        break
                    except Exception:
                        print(f"[indexer] Model not ready, waiting... ({attempt+1}/10)")
                        await asyncio.sleep(3)

                inserted        = 0
                section_counts  = {}   # track how many chunks per section (useful for debugging)

                for i, chunk in enumerate(chunks):

                    # ── Section detection (new) ────────────────────────────
                    section = detect_section(chunk)
                    section_counts[section] = section_counts.get(section, 0) + 1

                    # ── Embedding ──────────────────────────────────────────
                    vector = None
                    for attempt in range(5):
                        try:
                            vector = await get_embedding(chunk, client)
                            if vector is not None:
                                await asyncio.sleep(0.5)
                                break
                        except Exception as e:
                            print(f"[indexer] Chunk {i} retry {attempt+1}/5: {e}")
                            await asyncio.sleep(2)

                    if vector is None:
                        print(f"[indexer] Chunk {i} failed after retries, skipping")
                        continue

                    vector_str = "[" + ",".join(str(v) for v in vector) + "]"

                    # ── Store (section column + metadata) ──────────────────
                    import json
                    metadata = json.dumps({
                        "ticker":       ticker,
                        "filing_type":  filing_type,
                        "chunk_index":  i,
                        "section":      section,   # also inside JSON for easy inspection
                    })

                    await conn.execute(
                        """
                        INSERT INTO document_embeddings
                            (id, ticker, doc_type, chunk_text, embedding, metadata, section, created_at)
                        VALUES ($1, $2, $3, $4, $5::text::vector, $6::jsonb, $7, $8)
                        """,
                        uuid.uuid4(),
                        ticker,
                        filing_type,
                        chunk,
                        vector_str,
                        metadata,
                        section,                   # dedicated column for fast WHERE filtering
                        datetime.now(timezone.utc),
                    )
                    inserted += 1

                    if (i + 1) % 20 == 0:
                        print(f"[indexer] {i+1}/{len(chunks)} chunks indexed...")

        finally:
            await conn.close()

        result = {
            "ticker":          ticker,
            "filing_type":     filing_type,
            "total_chunks":    len(chunks),
            "chunks_indexed":  inserted,
            "sections_found":  section_counts,   # shows breakdown per section
            "status":          "success",
        }
        print(f"[indexer] Done: {result}")
        return result


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 4:
        print("Usage: python indexer.py <file_path> <ticker> <filing_type>")
        print("Example: python indexer.py data/filings/AAPL_10-K.htm AAPL 10-K")
        sys.exit(1)

    indexer = FilingIndexer()
    result  = asyncio.run(indexer.index_filing(sys.argv[1], sys.argv[2], sys.argv[3]))
    print(result)