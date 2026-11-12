"""
# migrate.py - applies the numbered SQL files in `backend/migrations/` to Postgres, each once

- Files run in name order (`001_...sql`, `002_...sql`); each applied name is recorded in `schema_migrations`.
- All pending files run in one transaction under an advisory lock, so two API processes starting together
  cannot apply the same file twice, and a failing file leaves the database as it was.
- Called by `main._prepare_database` at API startup. To change the schema, add the next numbered file; never
  edit one that has already run.
"""
from __future__ import annotations

from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
_LOCK_ID = 7_406_001  # any fixed number; only this runner takes it


# Apply every migration file not yet recorded and return the names applied now.
async def migrate(conn) -> list[str]:
    applied = []
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", _LOCK_ID)
        await conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
                           "name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
        done = {r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name not in done:
                await conn.execute(path.read_text(encoding="utf-8"))
                await conn.execute("INSERT INTO schema_migrations (name) VALUES ($1)", path.name)
                applied.append(path.name)
    return applied
