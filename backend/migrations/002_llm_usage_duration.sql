-- 002_llm_usage_duration.sql - how long each LLM call took, next to its tokens and cost.
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS duration_ms INTEGER;
