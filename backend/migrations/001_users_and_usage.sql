-- 001_users_and_usage.sql - the sign-in table and the LLM cost log.
-- Written with IF NOT EXISTS so databases created before migrations existed take it as a no-op.

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS failed_logins INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS llm_usage (
    id SERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    estimated_cost_usd NUMERIC NOT NULL,
    user_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
