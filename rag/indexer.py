# rag/indexer.py
# Ingestion engine — PDF/HTML → hierarchical chunks → MiniLM dense + BM25 sparse → Qdrant
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

COLLECTION_NAME  = "wealthos_docs"
SENTENCE_MODEL   = "sentence-transformers/all-MiniLM-L6-v2"
DENSE_DIMS       = 384
CHILD_MAX_WORDS  = 150

# Module-level model cache — loaded once, reused across all batches
_dense_model = None


def _get_dense_model():
    global _dense_model
    if _dense_model is None:
        from sentence_transformers import SentenceTransformer
        print(f"[indexer] Loading {SENTENCE_MODEL} ...")
        _dense_model = SentenceTransformer(SENTENCE_MODEL)
    return _dense_model

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


def _info_type_and_half_life(section: str) -> tuple:
    s = section.lower()
    if any(k in s for k in ["risk_factor", "legal", "market_risk"]):
        return "risk_factors", 365
    elif any(k in s for k in ["income_statement", "cash_flow", "balance_sheet", "financial_summary"]):
        return "financials", 90
    elif any(k in s for k in ["md_and_a", "notes"]):
        return "guidance", 90
    elif any(k in s for k in ["business", "properties"]):
        return "business_model", 365
    else:
        return "general", 180


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
                "dense": VectorParams(size=DENSE_DIMS, distance=Distance.COSINE),
            },
            sparse_vectors_config={
                "sparse": SparseVectorParams(index=SparseIndexParams(on_disk=False)),
            },
        )
        print(f"[qdrant] Created collection '{COLLECTION_NAME}'")
    return client


# ── Embeddings ────────────────────────────────────────────────────────────────

def embed_dense_batch(texts: list[str]) -> list[list[float]]:
    """sentence-transformers/all-MiniLM-L6-v2 — 384-dim, CPU, no API key needed."""
    model = _get_dense_model()
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return embeddings.tolist()


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
            _itype, _hlife = _info_type_and_half_life(current_section)
            child = {
                "id": str(uuid.uuid4()), "chunk_level": 2, "chunk_type": "table",
                "section": current_section, "content": content,
                "ticker": ticker, "form_type": filing_type,
                "filing_date": filing_date, "fiscal_year": _year(filing_date),
                "page_number": page, "source_file": source_file,
                "parent_id": None, "table_json": elem.get("table_json"),
                "info_type": _itype, "half_life_days": _hlife,
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
            _itype, _hlife = _info_type_and_half_life(current_section)
            child = {
                "id": str(uuid.uuid4()), "chunk_level": 2, "chunk_type": "prose",
                "section": current_section, "content": prose_chunk,
                "ticker": ticker, "form_type": filing_type,
                "filing_date": filing_date, "fiscal_year": _year(filing_date),
                "page_number": page, "source_file": source_file,
                "parent_id": None, "table_json": None,
                "info_type": _itype, "half_life_days": _hlife,
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
        user_id: str = "",
    ) -> dict:
        print(f"[indexer] {ticker} {filing_type} — extracting {file_path}")

        try:
            elements = await asyncio.to_thread(extract_elements, file_path)
        except Exception as e:
            return {"error": f"Extraction failed: {e}", "ticker": ticker}

        tables = sum(1 for e in elements if e["type"] == "table")
        n_elements = len(elements)
        print(f"[indexer] {n_elements} elements ({tables} tables, {n_elements - tables} prose pages)")

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
        extra = {"user_id": user_id} if user_id else {}
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
                payload={k: v for k, v in c.items() if k != "id"} | extra,
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
            "elements_extracted": n_elements,
            "parent_chunks": len(parents), "child_chunks": len(children),
            "total_points": len(points), "status": "success",
        }


    async def index_personal_doc(
        self,
        file_path: str,
        user_id: str,
        filename: str = "",
    ) -> dict:
        """
        Flat-chunk indexer for short personal finance documents (receipts, loan
        statements, salary slips). Bypasses the hierarchical chunker's 50-word
        minimum so even a 1-page EMI receipt gets indexed.
        """
        ticker = f"PERSONAL_{user_id}"

        # Extract raw text — try pdfplumber first (handles Chrome-printed PDFs
        # and modern fonts better than pypdf), fall back to pypdf, then HTML path
        def _extract_text(path: str) -> str:
            ext = Path(path).suffix.lower()
            if ext in {".htm", ".html"}:
                elems = extract_from_html(path)
                return "\n\n".join(e["content"] for e in elems if e.get("content", "").strip())

            text_parts = []
            # pdfplumber — best for text-based PDFs
            try:
                import pdfplumber
                with pdfplumber.open(path) as pdf:
                    for page in pdf.pages:
                        t = page.extract_text(x_tolerance=2, y_tolerance=2) or ""
                        if t.strip():
                            text_parts.append(t)
            except Exception:
                pass

            # pypdf fallback
            if not text_parts:
                try:
                    from pypdf import PdfReader
                    for page in PdfReader(path).pages:
                        t = page.extract_text() or ""
                        if t.strip():
                            text_parts.append(t)
                except Exception:
                    pass

            # OCR fallback — for image-based or scanned PDFs
            if not text_parts:
                try:
                    import pytesseract
                    from pdf2image import convert_from_path
                    images = convert_from_path(path, dpi=200)
                    for img in images:
                        t = pytesseract.image_to_string(img, lang="eng") or ""
                        if t.strip():
                            text_parts.append(t)
                    if text_parts:
                        print(f"[indexer] OCR extracted text from {Path(path).name}")
                except Exception as e:
                    print(f"[indexer] OCR failed: {e}")

            return "\n\n".join(text_parts)

        try:
            full_text = await asyncio.to_thread(_extract_text, file_path)
        except Exception as e:
            return {"error": f"Extraction failed: {e}", "ticker": ticker}

        if not full_text.strip():
            return {"error": "No text could be extracted. The document may be image-based or scanned. Please use a text-based PDF.", "ticker": ticker}

        words = full_text.split()
        CHUNK_SIZE = 150
        raw_chunks = [
            " ".join(words[i:i + CHUNK_SIZE])
            for i in range(0, len(words), CHUNK_SIZE)
            if words[i:i + CHUNK_SIZE]
        ]

        chunks = [
            {
                "id":           str(uuid.uuid4()),
                "chunk_level":  2,
                "chunk_type":   "prose",
                "section":      "personal-document",
                "content":      text,
                "ticker":       ticker,
                "form_type":    "personal-doc",
                "filing_date":  datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "fiscal_year":  datetime.now(timezone.utc).year,
                "page_number":  0,
                "source_file":  filename or Path(file_path).name,
                "parent_id":    None,
                "table_json":   None,
                "user_id":      user_id,
            }
            for text in raw_chunks
        ]

        if not chunks:
            return {"error": "Document too short to index.", "ticker": ticker}

        # Delete old version of this file for this user
        try:
            from qdrant_client.models import Filter, FieldCondition, MatchValue
            self.qdrant.delete(
                collection_name=COLLECTION_NAME,
                points_selector=Filter(must=[
                    FieldCondition(key="ticker",      match=MatchValue(value=ticker)),
                    FieldCondition(key="source_file", match=MatchValue(value=filename or Path(file_path).name)),
                ]),
            )
        except Exception:
            pass

        # Embed and upsert
        texts = [c["content"] for c in chunks]
        dense  = await asyncio.to_thread(embed_dense_batch,  texts)
        sparse = await asyncio.to_thread(embed_sparse_batch, texts)

        from qdrant_client.models import PointStruct, SparseVector
        points = [
            PointStruct(
                id=c["id"],
                vector={
                    "dense":  dv,
                    "sparse": SparseVector(indices=sv.indices.tolist(), values=sv.values.tolist()),
                },
                payload={k: v for k, v in c.items() if k != "id"},
            )
            for c, dv, sv in zip(chunks, dense, sparse)
        ]

        UPSERT = 100
        for i in range(0, len(points), UPSERT):
            await asyncio.to_thread(
                self.qdrant.upsert,
                collection_name=COLLECTION_NAME,
                points=points[i:i + UPSERT],
            )

        return {
            "ticker":         ticker,
            "filename":       filename or Path(file_path).name,
            "chunks_indexed": len(points),
            "status":         "success",
        }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m rag.indexer",
        description="Index a PDF or HTML document into Qdrant for WealthOS RAG.",
    )
    parser.add_argument("--file",     required=True,
                        help="Path to the PDF (or HTML) file to index")
    parser.add_argument("--user_id",  required=True,
                        help="User UUID (e.g. 00000000-0000-0000-0000-000000000001)")
    parser.add_argument("--doc_type", default="personal_finance",
                        help="Document type / filing type (default: personal_finance)")
    parser.add_argument("--ticker",   default="PERSONAL",
                        help="Ticker symbol associated with this document (default: PERSONAL)")
    args = parser.parse_args()

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"Error: file not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Indexing {file_path.name}...")

    try:
        indexer = FilingIndexer()
        result = asyncio.run(
            indexer.index_filing(
                file_path=str(file_path),
                ticker=args.ticker,
                filing_type=args.doc_type,
                user_id=args.user_id,
            )
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if result.get("error"):
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)

    print(f"Extracted {result['elements_extracted']} chunks")
    print(f"Indexed {result['total_points']} chunks into Qdrant")
    print("Done.")
