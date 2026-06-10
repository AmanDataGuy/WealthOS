# rag/indexer.py
# Ingestion engine — PDF/HTML → hierarchical chunks → Voyage dense + BM25 sparse → Qdrant
#
# Two chunk levels per filing:
#   level=1  section parent  (~1500 words, one per detected section)
#   level=2  child prose/table  (~150 words each, embedded + searched)
# Retrieval fetches level-2 by similarity, then returns level-1 parent for LLM context.

import os
import re
import uuid
import json
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

QDRANT_URL     = os.getenv("QDRANT_URL",     "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "")
VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY", "")

COLLECTION_NAME  = "wealthos_docs"
VOYAGE_MODEL     = "voyage-finance-2"
VOYAGE_DIMS      = 1024
CHILD_MAX_WORDS  = 150

SECTION_HEADERS = {
    "consolidated statements of operations":  "income_statement",
    "consolidated statements of income":      "income_statement",
    "results of operations":                  "income_statement",
    "consolidated balance sheet":             "balance_sheet",
    "consolidated statements of financial":   "balance_sheet",
    "consolidated statements of cash flow":   "cash_flow",
    "cash flows from operating":              "cash_flow",
    "management's discussion":                "md_and_a",
    "management discussion":                  "md_and_a",
    "risk factors":                           "risk_factors",
    "quantitative and qualitative":           "market_risk",
    "legal proceedings":                      "legal",
    "controls and procedures":                "controls",
    "business overview":                      "business",
    "item 1.":                                "business",
    "properties":                             "properties",
    "selected financial data":                "financial_summary",
    "notes to":                               "notes",
}


def detect_section(text: str) -> Optional[str]:
    t = text.lower()
    for keyword, section in SECTION_HEADERS.items():
        if keyword in t:
            return section
    return None


# ── Qdrant setup ──────────────────────────────────────────────────────────────

def get_qdrant_client():
    from qdrant_client import QdrantClient
    if QDRANT_API_KEY:
        return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    return QdrantClient(url=QDRANT_URL)


def ensure_collection():
    from qdrant_client.models import VectorParams, Distance, SparseVectorParams, SparseIndexParams
    client = get_qdrant_client()
    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME not in existing:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config={
                "dense": VectorParams(size=VOYAGE_DIMS, distance=Distance.COSINE),
            },
            sparse_vectors_config={
                "sparse": SparseVectorParams(index=SparseIndexParams(on_disk=False)),
            },
        )
        print(f"[qdrant] Created collection '{COLLECTION_NAME}'")
    return client


# ── Embeddings ────────────────────────────────────────────────────────────────

def embed_dense_batch(texts: list[str]) -> list[list[float]]:
    """Voyage AI finance-2. Falls back to zero vectors if key absent."""
    if not VOYAGE_API_KEY:
        print("[indexer] VOYAGE_API_KEY not set — using zero vectors (search quality degraded)")
        return [[0.0] * VOYAGE_DIMS for _ in texts]
    import voyageai
    vc = voyageai.Client(api_key=VOYAGE_API_KEY)
    result = vc.embed(texts, model=VOYAGE_MODEL, input_type="document")
    return result.embeddings


def embed_sparse_batch(texts: list[str]) -> list:
    """BM25 sparse vectors via fastembed."""
    from fastembed import SparseTextEmbedding
    model = SparseTextEmbedding(model_name="Qdrant/bm25")
    return list(model.embed(texts))


# ── Extraction ────────────────────────────────────────────────────────────────

def extract_from_pdf(file_path: str) -> list[dict]:
    """Extract prose pages + tables. pdfplumber for tables, pypdf for prose."""
    elements = []

    # Tables via pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(file_path) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                for tbl in page.extract_tables():
                    if not tbl or len(tbl) < 2:
                        continue
                    headers = [str(h or "").strip() for h in tbl[0]]
                    if not any(headers):
                        continue
                    data_rows = []
                    for row in tbl[1:21]:
                        data_rows.append({
                            headers[i] if i < len(headers) else f"col{i}": str(cell or "").strip()
                            for i, cell in enumerate(row)
                        })
                    md = ["| " + " | ".join(headers) + " |",
                          "| " + " | ".join(["---"] * len(headers)) + " |"]
                    for row in data_rows:
                        md.append("| " + " | ".join(row.values()) + " |")
                    md_table = "\n".join(md)
                    if len(md_table.strip()) > 50:
                        elements.append({"type": "table", "content": md_table,
                                         "page_number": page_num, "table_json": json.dumps(data_rows)})
    except ImportError:
        print("[indexer] pdfplumber not installed — table extraction skipped")
    except Exception as e:
        print(f"[indexer] Table extraction error: {e}")

    # Prose via pypdf
    try:
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        for page_num, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            if text.strip():
                elements.append({"type": "prose", "content": text,
                                  "page_number": page_num, "table_json": None})
    except Exception as e:
        print(f"[indexer] PDF prose extraction error: {e}")

    return elements


def extract_from_html(file_path: str) -> list[dict]:
    """Extract prose + tables from SEC EDGAR HTML filings."""
    from bs4 import BeautifulSoup
    raw = Path(file_path).read_bytes()
    try:
        html = raw.decode("utf-8")
    except UnicodeDecodeError:
        html = raw.decode("latin-1")

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(True):
        if tag.name and (tag.name.startswith("ix:") or tag.name.startswith("xbrl")):
            tag.decompose()

    elements = []

    for tbl in soup.find_all("table"):
        rows = [[td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                for tr in tbl.find_all("tr")]
        rows = [r for r in rows if any(r)]
        if len(rows) < 2:
            tbl.decompose()
            continue
        headers = rows[0]
        data_rows = [{headers[i] if i < len(headers) else f"col{i}": v
                      for i, v in enumerate(row)} for row in rows[1:21]]
        md = ["| " + " | ".join(headers) + " |",
              "| " + " | ".join(["---"] * len(headers)) + " |"]
        for row in data_rows:
            md.append("| " + " | ".join(row.values()) + " |")
        md_table = "\n".join(md)
        if len(md_table.strip()) > 50:
            elements.append({"type": "table", "content": md_table,
                              "page_number": 0, "table_json": json.dumps(data_rows)})
        tbl.decompose()

    text = soup.get_text(separator="\n")
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    elements.append({"type": "prose", "content": "\n".join(lines),
                     "page_number": 0, "table_json": None})
    return elements


def extract_elements(file_path: str) -> list[dict]:
    ext = Path(file_path).suffix.lower()
    return extract_from_pdf(file_path) if ext == ".pdf" else extract_from_html(file_path)


# ── Sentence-based prose chunking ─────────────────────────────────────────────

def chunk_prose(text: str, max_words: int = CHILD_MAX_WORDS) -> list[str]:
    """Split text into sentence-boundary-respecting chunks of ~max_words words."""
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)
    chunks, buf = [], []
    for sent in sentences:
        words = sent.split()
        if len(buf) + len(words) > max_words and buf:
            chunks.append(" ".join(buf))
            buf = words
        else:
            buf.extend(words)
    if buf:
        chunks.append(" ".join(buf))
    return [c for c in chunks
            if len(c.split()) >= 20
            and sum(1 for w in c.split() if len(w) > 30) <= 5]


# ── Hierarchical chunk builder ────────────────────────────────────────────────

def _year(date_str: str) -> int:
    try:
        return int(date_str[:4])
    except (ValueError, TypeError):
        return datetime.now().year


def build_hierarchical_chunks(
    elements: list[dict],
    ticker: str,
    filing_type: str,
    filing_date: str = "",
    source_file: str = "",
) -> list[dict]:
    all_chunks: list[dict] = []
    current_section = "unknown"
    section_buffer: list[dict] = []

    def flush_parent():
        if not section_buffer:
            return
        prose_words = " ".join(c["content"] for c in section_buffer if c["chunk_type"] == "prose")
        if len(prose_words.split()) < 50:
            return
        parent_id = str(uuid.uuid4())
        parent = {
            "id": parent_id, "chunk_level": 1, "chunk_type": "prose",
            "section": section_buffer[0]["section"],
            "content": prose_words[:3000],
            "ticker": ticker, "form_type": filing_type,
            "filing_date": filing_date, "fiscal_year": _year(filing_date),
            "page_number": section_buffer[0].get("page_number", 0),
            "source_file": source_file, "parent_id": None, "table_json": None,
        }
        for child in section_buffer:
            child["parent_id"] = parent_id
        all_chunks.append(parent)

    for elem in elements:
        content = elem["content"]
        page    = elem.get("page_number", 0)

        if elem["type"] == "table":
            child = {
                "id": str(uuid.uuid4()), "chunk_level": 2, "chunk_type": "table",
                "section": current_section, "content": content,
                "ticker": ticker, "form_type": filing_type,
                "filing_date": filing_date, "fiscal_year": _year(filing_date),
                "page_number": page, "source_file": source_file,
                "parent_id": None, "table_json": elem.get("table_json"),
            }
            section_buffer.append(child)
            all_chunks.append(child)
            continue

        detected = detect_section(content)
        if detected and detected != current_section:
            flush_parent()
            section_buffer = []
            current_section = detected

        for prose_chunk in chunk_prose(content):
            child = {
                "id": str(uuid.uuid4()), "chunk_level": 2, "chunk_type": "prose",
                "section": current_section, "content": prose_chunk,
                "ticker": ticker, "form_type": filing_type,
                "filing_date": filing_date, "fiscal_year": _year(filing_date),
                "page_number": page, "source_file": source_file,
                "parent_id": None, "table_json": None,
            }
            section_buffer.append(child)
            all_chunks.append(child)

    flush_parent()
    return all_chunks


# ── Main indexer class ────────────────────────────────────────────────────────

class FilingIndexer:

    def __init__(self):
        self.qdrant = ensure_collection()

    async def index_filing(
        self,
        file_path: str,
        ticker: str,
        filing_type: str,
        filing_date: str = "",
    ) -> dict:
        print(f"[indexer] {ticker} {filing_type} — extracting {file_path}")

        try:
            elements = await asyncio.to_thread(extract_elements, file_path)
        except Exception as e:
            return {"error": f"Extraction failed: {e}", "ticker": ticker}

        tables = sum(1 for e in elements if e["type"] == "table")
        print(f"[indexer] {len(elements)} elements ({tables} tables, {len(elements)-tables} prose pages)")

        chunks = await asyncio.to_thread(
            build_hierarchical_chunks, elements, ticker, filing_type,
            filing_date, Path(file_path).name,
        )
        parents  = [c for c in chunks if c["chunk_level"] == 1]
        children = [c for c in chunks if c["chunk_level"] == 2]
        print(f"[indexer] {len(parents)} section chunks + {len(children)} child chunks")

        # Clear previous version of this filing
        try:
            from qdrant_client.models import Filter, FieldCondition, MatchValue
            self.qdrant.delete(
                collection_name=COLLECTION_NAME,
                points_selector=Filter(must=[
                    FieldCondition(key="ticker",    match=MatchValue(value=ticker)),
                    FieldCondition(key="form_type", match=MatchValue(value=filing_type)),
                ]),
            )
        except Exception as e:
            print(f"[indexer] Could not clear existing chunks: {e}")

        # Embed in batches
        texts    = [c["content"] for c in chunks]
        BATCH    = 64
        all_dense, all_sparse = [], []

        for i in range(0, len(texts), BATCH):
            batch = texts[i:i + BATCH]
            n = i // BATCH + 1
            total = (len(texts) - 1) // BATCH + 1
            print(f"[indexer] Embedding batch {n}/{total}...")
            d = await asyncio.to_thread(embed_dense_batch,  batch)
            s = await asyncio.to_thread(embed_sparse_batch, batch)
            all_dense.extend(d)
            all_sparse.extend(s)

        # Build Qdrant points
        from qdrant_client.models import PointStruct, SparseVector
        points = [
            PointStruct(
                id=c["id"],
                vector={
                    "dense":  dv,
                    "sparse": SparseVector(
                        indices=sv.indices.tolist(),
                        values=sv.values.tolist(),
                    ),
                },
                payload={k: v for k, v in c.items() if k != "id"},
            )
            for c, dv, sv in zip(chunks, all_dense, all_sparse)
        ]

        # Upsert in batches
        UPSERT = 100
        for i in range(0, len(points), UPSERT):
            await asyncio.to_thread(
                self.qdrant.upsert,
                collection_name=COLLECTION_NAME,
                points=points[i:i + UPSERT],
            )
            print(f"[indexer] Upserted {min(i+UPSERT, len(points))}/{len(points)}")

        return {
            "ticker": ticker, "filing_type": filing_type,
            "parent_chunks": len(parents), "child_chunks": len(children),
            "total_points": len(points), "status": "success",
        }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 4:
        print("Usage: python rag/indexer.py <file_path> <ticker> <filing_type> [filing_date]")
        sys.exit(1)
    indexer = FilingIndexer()
    result  = asyncio.run(indexer.index_filing(sys.argv[1], sys.argv[2], sys.argv[3],
                                                sys.argv[4] if len(sys.argv) > 4 else ""))
    print(result)
