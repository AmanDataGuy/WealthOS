-- 003_research_chunks.sql - one embedding per chunk of an uploaded filing, for research.py's hybrid search.
-- Needs the pgvector extension (the pgvector/pgvector:pg16 image). Rows go when the user's account is deleted.
-- The chunk text is not stored here: it lives in the user's JSON index next to these ids.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS research_chunks (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    doc_id TEXT NOT NULL,             -- research.py doc_id: the first 16 hex of the upload's SHA-256
    chunker TEXT NOT NULL,            -- 'structure' or 'fixed'
    chunk INTEGER NOT NULL,           -- 0-based, per doc and chunker
    company TEXT,
    form TEXT,
    period TEXT,                      -- fiscal period such as 'FY2025'
    section TEXT,
    page_start INTEGER NOT NULL,
    page_end INTEGER NOT NULL,
    embedding vector(384) NOT NULL,   -- BAAI/bge-small-en-v1.5
    PRIMARY KEY (user_id, workspace_id, doc_id, chunker, chunk)
);

CREATE INDEX IF NOT EXISTS research_chunks_cosine
    ON research_chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS research_chunks_filter
    ON research_chunks (user_id, workspace_id, company, period);

-- The old postgres:16-alpine image sorted text with musl, the pgvector image with glibc: rebuild the text indexes
-- written under the old image so lookups by username stay correct. Small tables, so this is quick.
REINDEX TABLE users;
REINDEX TABLE schema_migrations;
