# tests/test_migrate.py
"""The migration runner applies each numbered SQL file once, in name order, and records it. No database: a fake
connection records what would run (the real-Postgres check is a manual run against a throwaway container)."""
import asyncio
from contextlib import asynccontextmanager

from app.core import migrate


# A fake asyncpg connection: remembers executed SQL and the schema_migrations rows.
class FakeConn:
    def __init__(self):
        self.sql, self.recorded = [], []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, query, *args):
        if query.startswith("INSERT INTO schema_migrations"):
            self.recorded.append(args[0])
        self.sql.append(query)

    async def fetch(self, _query):
        return [{"name": n} for n in self.recorded]


def test_each_file_runs_once_in_order(tmp_path, monkeypatch):
    (tmp_path / "002_second.sql").write_text("SELECT 2;")
    (tmp_path / "001_first.sql").write_text("SELECT 1;")
    monkeypatch.setattr(migrate, "MIGRATIONS", tmp_path)
    conn = FakeConn()
    assert asyncio.run(migrate.migrate(conn)) == ["001_first.sql", "002_second.sql"]
    assert conn.sql.index("SELECT 1;") < conn.sql.index("SELECT 2;")
    assert asyncio.run(migrate.migrate(conn)) == []  # already recorded: nothing runs again


def test_the_real_migrations_folder_is_numbered():
    names = [p.name for p in sorted(migrate.MIGRATIONS.glob("*.sql"))]
    assert names and names[0] == "001_users_and_usage.sql"
    assert all(n[:3].isdigit() and n[3] == "_" for n in names)
