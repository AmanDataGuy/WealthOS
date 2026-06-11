"""
WealthOS — Qdrant collection initialiser.

Creates the `wealthos_docs` collection with:
  - named dense vector  "dense"  (1024-dim cosine, Voyage AI voyage-finance-2)
  - named sparse vector "sparse" (BM25, fastembed Qdrant/bm25)
  - payload indexes on ticker, chunk_level, section  (used by query_engine filters)

Run once before ingesting any documents:
    python scripts/init_qdrant.py

Safe to re-run — exits without error if the collection already exists.
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()

QDRANT_URL      = os.getenv("QDRANT_URL",     "http://localhost:6333")
QDRANT_API_KEY  = os.getenv("QDRANT_API_KEY", "")
COLLECTION_NAME = "wealthos_docs"


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

    # Check if collection already exists
    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME in existing:
        print(f"Collection '{COLLECTION_NAME}' already exists — nothing to do.")
        info = client.get_collection(COLLECTION_NAME)
        print(f"  vectors_count : {info.vectors_count}")
        print(f"  points_count  : {info.points_count}")
        return

    print(f"Creating collection '{COLLECTION_NAME}' ...")

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config={
            "dense": VectorParams(
                size=1024,
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

    print(f"\nDone. Collection '{COLLECTION_NAME}' created.")
    print("Next step: ingest documents with rag/indexer.py")
    print("  python -m rag.indexer --ticker TCS.NS --file path/to/annual_report.pdf")


if __name__ == "__main__":
    main()
