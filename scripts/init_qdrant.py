"""
WealthOS — Qdrant collection initialiser.

Creates the `wealthos_docs` collection with:
  - named dense vector  "dense"  (384-dim cosine, sentence-transformers/all-MiniLM-L6-v2)
  - named sparse vector "sparse" (BM25, fastembed Qdrant/bm25)
  - payload indexes on ticker, chunk_level, section  (used by query_engine filters)

Run once before ingesting any documents:
    python scripts/init_qdrant.py

If the collection already exists with different dimensions it is deleted and recreated.
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()

QDRANT_URL      = os.getenv("QDRANT_URL",     "http://localhost:6333")
QDRANT_API_KEY  = os.getenv("QDRANT_API_KEY", "")
COLLECTION_NAME = "wealthos_docs"
DENSE_DIMS      = 384


def main():
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import (
            Distance,
            VectorParams,
            SparseVectorParams,
            SparseIndexParams,
            PayloadSchemaType,
        )
    except ImportError:
        print("ERROR: qdrant-client not installed.  pip install qdrant-client")
        sys.exit(1)

    print(f"Connecting to Qdrant at {QDRANT_URL} ...")

    if QDRANT_API_KEY:
        client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    else:
        client = QdrantClient(url=QDRANT_URL)

    # Check connectivity
    try:
        client.get_collections()
    except Exception as exc:
        print(f"ERROR: Cannot reach Qdrant — {exc}")
        print("Start it first:  docker start wealthos-qdrant")
        sys.exit(1)

    # Check if collection already exists — delete if dims don't match
    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME in existing:
        info = client.get_collection(COLLECTION_NAME)
        current_dims = info.config.params.vectors.get("dense", {})
        # qdrant_client returns VectorParams object; .size holds dims
        try:
            current_size = current_dims.size
        except AttributeError:
            current_size = None

        if current_size == DENSE_DIMS:
            print(f"Collection '{COLLECTION_NAME}' already exists with correct dims ({DENSE_DIMS}) — nothing to do.")
            print(f"  points_count  : {info.points_count}")
            return

        print(f"Collection '{COLLECTION_NAME}' exists with dims={current_size}, expected {DENSE_DIMS} — deleting ...")
        client.delete_collection(COLLECTION_NAME)
        print(f"  Deleted.")

    print(f"Creating collection '{COLLECTION_NAME}' (dense={DENSE_DIMS}-dim) ...")

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config={
            "dense": VectorParams(
                size=DENSE_DIMS,
                distance=Distance.COSINE,
                on_disk=False,
            ),
        },
        sparse_vectors_config={
            "sparse": SparseVectorParams(
                index=SparseIndexParams(on_disk=False),
            ),
        },
    )

    print("  Creating payload indexes ...")

    # ticker — keyword filter (e.g. WHERE ticker = 'TCS.NS')
    client.create_payload_index(
        collection_name=COLLECTION_NAME,
        field_name="ticker",
        field_schema=PayloadSchemaType.KEYWORD,
    )

    # chunk_level — integer filter (query_engine fetches only level=2 child chunks)
    client.create_payload_index(
        collection_name=COLLECTION_NAME,
        field_name="chunk_level",
        field_schema=PayloadSchemaType.INTEGER,
    )

    # section — keyword filter (e.g. WHERE section = 'risk_factors')
    client.create_payload_index(
        collection_name=COLLECTION_NAME,
        field_name="section",
        field_schema=PayloadSchemaType.KEYWORD,
    )

    print(f"\nDone. Collection '{COLLECTION_NAME}' created with {DENSE_DIMS}-dim dense vectors.")

    # ── user_analyses collection ────────────────────────────────────────────────
    # Stores one vector per user analysis (the Final Verdict section embedding).
    # Used to retrieve semantically similar past decisions at pipeline start.
    USER_ANALYSES = "user_analyses"
    if USER_ANALYSES not in existing:
        print(f"\nCreating collection '{USER_ANALYSES}' (per-user analysis memory) ...")
        client.create_collection(
            collection_name=USER_ANALYSES,
            vectors_config={
                "dense": VectorParams(
                    size=DENSE_DIMS,
                    distance=Distance.COSINE,
                    on_disk=False,
                ),
            },
        )
        client.create_payload_index(USER_ANALYSES, "user_id", PayloadSchemaType.KEYWORD)
        client.create_payload_index(USER_ANALYSES, "ticker",  PayloadSchemaType.KEYWORD)
        client.create_payload_index(USER_ANALYSES, "verdict", PayloadSchemaType.KEYWORD)
        print(f"  Collection '{USER_ANALYSES}' created with user_id / ticker / verdict indexes.")
    else:
        print(f"\nCollection '{USER_ANALYSES}' already exists — skipping.")

    print("\nNext step: ingest documents with rag/indexer.py")
    print("  python -m rag.indexer --file path/to/doc.pdf --user_id <uuid> --doc_type loan_statement")


if __name__ == "__main__":
    main()
