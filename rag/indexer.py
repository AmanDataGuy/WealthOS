# rag/indexer.py
# Ingestion engine — reads a PDF or HTML filing, chunks it, embeds via Ollama mxbai-embed-large,
# stores chunks + vectors into pgvector (document_embeddings table).

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
        end = start + chunk_size
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
          3. Embed each chunk via Ollama mxbai-embed-large
          4. Store in document_embeddings (pgvector)
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

                inserted = 0
                for i, chunk in enumerate(chunks):
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

                    await conn.execute(
                        """
                        INSERT INTO document_embeddings
                            (id, ticker, doc_type, chunk_text, embedding, metadata, created_at)
                        VALUES ($1, $2, $3, $4, $5::text::vector, $6::jsonb, $7)
                        """,
                        uuid.uuid4(),
                        ticker,
                        filing_type,
                        chunk,
                        vector_str,
                        f'{{"ticker": "{ticker}", "filing_type": "{filing_type}", "chunk_index": {i}}}',
                        datetime.now(timezone.utc),
                    )
                    inserted += 1

                    if (i + 1) % 20 == 0:
                        print(f"[indexer] {i+1}/{len(chunks)} chunks indexed...")

        finally:
            await conn.close()

        result = {
            "ticker":        ticker,
            "filing_type":   filing_type,
            "total_chunks":  len(chunks),
            "chunks_indexed": inserted,
            "status":        "success",
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