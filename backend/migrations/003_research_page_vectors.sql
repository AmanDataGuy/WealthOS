-- 003_research_page_vectors.sql - one embedding per page of an uploaded filing, for research.py's hybrid search.
-- Needs the pgvector extension (the pgvector/pgvector:pg16 image). Rows go when the user's account is deleted.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS research_page_vectors (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    file_hash TEXT NOT NULL,          -- research.py doc_id: the first 16 hex of the upload's SHA-256
    page INTEGER NOT NULL,
    embedding vector(384) NOT NULL,   -- BAAI/bge-small-en-v1.5
    PRIMARY KEY (user_id, workspace_id, file_hash, page)
);

CREATE INDEX IF NOT EXISTS research_page_vectors_cosine
    ON research_page_vectors USING hnsw (embedding vector_cosine_ops);

-- The old postgres:16-alpine image sorted text with musl, the pgvector image with glibc: rebuild the text indexes
-- written under the old image so lookups by username stay correct. Small tables, so this is quick.
REINDEX TABLE users;
REINDEX TABLE schema_migrations;
