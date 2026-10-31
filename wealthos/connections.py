"""
# connections.py - connect a workspace to a live database (Postgres, MySQL or SQLite)

The user gives a connection string (a **read-only** database user is recommended); we never query their database
live. Instead:

1. `connect` checks the string works and stores it **encrypted** (Fernet, key in the `WEALTHOS_FERNET_KEY` env var).
2. `list_tables` shows what tables the database has.
3. `snapshot` copies the chosen tables into Parquet datasets of the workspace. Calling it again = **refresh**.
   From then on they are ordinary tables for the engine, the assistant and dashboards (`SELECT * FROM orders`).

Safety:
- A separate, short-lived DuckDB connection ATTACHes the database with `READ_ONLY` and runs only our own fixed
  statements (list tables, copy a table). The locked query connection (`engine.open_readonly`) never attaches anything.
- The password is only ever decrypted in memory, is replaced by `***` in anything shown, and is scrubbed from errors.
- SQLite: only a file the user uploaded into their own folder (a server path could point at someone else's file).

Stored as `data/users/<user_id>/workspaces/<workspace_id>/connection.json`: `{kind, display, secret, tables, ...}`.

| Kind | Connection string |
|---|---|
| Postgres | `postgresql://user:password@host:5432/dbname` |
| MySQL | `mysql://user:password@host:3306/dbname` |
| SQLite | upload the `.sqlite` / `.db` file (`POST /connections/{user}/{ws}/sqlite`) |
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

import duckdb
from cryptography.fernet import Fernet, InvalidToken

from wealthos import engine

SNAPSHOT_ROW_CAP = 1_000_000  # ponytail: rows per table per snapshot; raise it (or go incremental) for bigger tables
BUILT_IN_SHAPES = {"transactions", "trades", "holdings", "prices", "statements"}
SQLITE_FILE = "database.sqlite"  # where an uploaded SQLite file is kept, next to connection.json


# ── Secrets ─────────────────────────────────────────────────────────────────────────────

# The encryption key from the environment (make one: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())").
def _fernet() -> Fernet:
    key = os.getenv("WEALTHOS_FERNET_KEY", "").strip()
    if not key:
        raise ValueError("database connections are switched off: set WEALTHOS_FERNET_KEY in .env (see .env.example)")
    return Fernet(key.encode())


# The connection string with its password replaced by ***, safe to show and log.
def redact(url: str) -> str:
    if url.startswith("sqlite:"):
        return "sqlite: uploaded file"
    password = urlsplit(url).password
    return url.replace(f":{password}@", ":***@", 1) if password else url


# ── Talking to the database ─────────────────────────────────────────────────────────────

# Which DuckDB extension reads this string, and the text to hand to ATTACH.
def _target(url: str) -> tuple[str, str]:
    scheme = url.split("://", 1)[0].split("+", 1)[0].lower()  # "postgresql+asyncpg" -> "postgresql"
    if scheme in ("postgres", "postgresql") and "://" in url:
        url = "postgresql://" + url.split("://", 1)[1]
        return "postgres", url if "connect_timeout" in url else url + ("&" if "?" in url else "?") + "connect_timeout=10"
    if scheme == "mysql" and "://" in url:  # DuckDB's MySQL reader takes "key=value" pairs, not a URL
        p = urlsplit(url)
        pairs = {"host": p.hostname, "port": p.port, "user": unquote(p.username or ""),
                 "passwd": unquote(p.password or ""), "database": p.path.lstrip("/")}
        return "mysql", " ".join(f"{k}={v}" for k, v in pairs.items() if v)
    if scheme == "sqlite":
        return "sqlite", url.split(":///", 1)[-1]
    raise ValueError("the connection string must start with postgresql:// or mysql:// (or upload a SQLite file)")


# A fresh in-memory DuckDB with the user's database attached READ_ONLY as "src". Errors never show the password.
def _attach(url: str) -> tuple[duckdb.DuckDBPyConnection, str]:
    kind, target = _target(url)
    con = duckdb.connect(":memory:")
    try:
        con.execute(f"INSTALL {kind}")  # downloads once, then a no-op
        con.execute(f"LOAD {kind}")
        con.execute(f"ATTACH '{target.replace(chr(39), chr(39) * 2)}' AS src (TYPE {kind}, READ_ONLY)")
    except duckdb.Error as e:
        con.close()
        message = str(e)
        password = urlsplit(url).password if kind != "sqlite" else None
        for secret in {password, unquote(password or "")} - {None, ""}:
            message = message.replace(secret, "***")
        raise ValueError(f"could not connect: {message}") from None
    return con, kind


# Every user table and view in the attached database, as "schema.table" (the database's own catalog schemas left out).
def _tables(con: duckdb.DuckDBPyConnection) -> list[str]:
    rows = con.execute("SELECT table_schema, table_name FROM information_schema.tables WHERE table_catalog = 'src' "
                       "AND table_schema NOT IN ('information_schema', 'pg_catalog', 'mysql', 'performance_schema', 'sys') "
                       "ORDER BY 1, 2").fetchall()
    return [f"{s}.{t}" for s, t in rows]


# The name a database table gets in the workspace: "public.Order Items" -> "order_items" (never a built-in shape name).
def table_name(full_name: str) -> str:
    name = re.sub(r"[^a-z0-9_]+", "_", full_name.split(".", 1)[-1].lower()).strip("_") or "table"
    if name[0].isdigit() or name in BUILT_IN_SHAPES:
        name = f"db_{name}"
    return name[:60]


# ── Saved connection ────────────────────────────────────────────────────────────────────

# Folder for this workspace's own files (connection, uploaded SQLite file, semantic model).
def folder(user_id: str, workspace_id: str) -> Path:
    return engine.user_dir(user_id) / "workspaces" / engine.safe_id(workspace_id)


# The saved connection file of a workspace (with its encrypted secret), or None.
def _load(user_id: str, workspace_id: str) -> dict | None:
    path = folder(user_id, workspace_id) / "connection.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# Write the connection file of a workspace.
def _save(user_id: str, workspace_id: str, conn: dict) -> None:
    folder(user_id, workspace_id).mkdir(parents=True, exist_ok=True)
    (folder(user_id, workspace_id) / "connection.json").write_text(json.dumps(conn, ensure_ascii=False), encoding="utf-8")


# The saved connection without its secret, or None: {kind, display, tables, connected_at, refreshed_at}.
def info(user_id: str, workspace_id: str) -> dict | None:
    conn = _load(user_id, workspace_id)
    return {k: v for k, v in conn.items() if k != "secret"} if conn else None


# The decrypted connection string of a workspace (raises ValueError if there is none).
def _url(user_id: str, workspace_id: str) -> str:
    conn = _load(user_id, workspace_id)
    if not conn:
        raise ValueError("this workspace has no database connection")
    try:
        return _fernet().decrypt(conn["secret"].encode()).decode()
    except InvalidToken:
        raise ValueError("the saved connection can't be decrypted (WEALTHOS_FERNET_KEY changed): connect again") from None


# ── What the API calls ──────────────────────────────────────────────────────────────────

# Check a connection string works, save it encrypted for this workspace, and return its info + tables.
def connect(user_id: str, workspace_id: str, url: str) -> dict:
    url = url.strip()
    if url.lower().startswith("sqlite:"):
        file = Path(url.split(":///", 1)[-1]).resolve()
        if file != (folder(user_id, workspace_id) / SQLITE_FILE).resolve():
            raise ValueError("a SQLite database must be uploaded, not given as a server path")
    secret = _fernet().encrypt(url.encode()).decode()  # fails early if the key is missing
    con, kind = _attach(url)
    try:
        tables = _tables(con)
    finally:
        con.close()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save(user_id, workspace_id, {"kind": kind, "display": redact(url), "secret": secret, "tables": [],
                                  "connected_at": now, "refreshed_at": None})
    return info(user_id, workspace_id) | {"available_tables": tables}


# Connect the SQLite file the user uploaded to folder(user, workspace) / SQLITE_FILE.
def connect_sqlite(user_id: str, workspace_id: str) -> dict:
    path = folder(user_id, workspace_id) / SQLITE_FILE
    with path.open("rb") as f:
        if f.read(16) != b"SQLite format 3\x00":
            raise ValueError("that is not a SQLite database file")
    return connect(user_id, workspace_id, f"sqlite:///{path.resolve().as_posix()}")


# The tables the connected database has right now.
def list_tables(user_id: str, workspace_id: str) -> list[str]:
    con, _ = _attach(_url(user_id, workspace_id))
    try:
        return _tables(con)
    finally:
        con.close()


# Copy the chosen tables (default: the ones chosen last time = refresh) into the workspace as Parquet datasets.
# Returns {"datasets": [...], "errors": {table: reason}}; one failing table doesn't stop the others.
def snapshot(user_id: str, workspace_id: str, tables: list[str] | None = None) -> dict:
    ws = engine.safe_id(workspace_id)
    url = _url(user_id, ws)
    conn = _load(user_id, ws)
    tables = list(dict.fromkeys(tables or conn["tables"]))
    if not tables:
        raise ValueError("choose at least one table to copy")
    names = [table_name(t) for t in tables]
    if len(set(names)) != len(names):
        raise ValueError(f"two chosen tables would get the same name: {names}")

    con, kind = _attach(url)
    saved, errors = [], {}
    try:
        unknown = set(tables) - set(_tables(con))
        if unknown:
            raise ValueError(f"not in the database: {sorted(unknown)}")
        for full, name in zip(tables, names):
            try:
                saved.append(_copy_table(con, user_id, ws, kind, full, name))
            except duckdb.Error as e:
                errors[full] = str(e).splitlines()[0]
    finally:
        con.close()
    _save(user_id, ws, conn | {"tables": tables, "refreshed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return {"datasets": saved, "errors": errors}


# Copy one table to Parquet, describe it like an uploaded dataset, and replace its previous snapshot.
def _copy_table(con, user_id: str, ws: str, kind: str, full: str, name: str) -> dict:
    schema_name, table = full.split(".", 1)
    out_dir = engine.user_dir(user_id) / "datasets" / name
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / f"_copying_{ws}.parquet"
    quote = lambda s: '"' + s.replace('"', '""') + '"'  # noqa: E731
    path_sql = tmp.resolve().as_posix().replace("'", "''")
    con.execute(f"COPY (SELECT * FROM src.{quote(schema_name)}.{quote(table)} LIMIT {SNAPSHOT_ROW_CAP}) "
                f"TO '{path_sql}' (FORMAT parquet)")
    cur = con.execute(f"SELECT * FROM read_parquet('{path_sql}') LIMIT 0")
    columns = [d[0] for d in cur.description]
    rows = con.execute(f"SELECT COUNT(*) FROM read_parquet('{path_sql}')").fetchone()[0]
    digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
    dataset_id = f"db_{ws}_{name}_{digest[:12]}"  # new data -> new id -> new data_version in provenance

    for old in engine.list_datasets(user_id, ws):
        if old.get("db_table") == full:
            engine.remove_dataset(user_id, old)
    tmp.replace(out_dir / f"{dataset_id}.parquet")
    meta = {"dataset_id": dataset_id, "workspace_id": ws, "shape": name, "db_table": full,
            "source": {"file": f"{kind}:{full}", "content_hash": digest, "page": None, "sheet": None},
            "rows": rows, "columns": columns,
            "warnings": [f"only the first {SNAPSHOT_ROW_CAP:,} rows were copied"] if rows >= SNAPSHOT_ROW_CAP else [],
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (out_dir / f"{dataset_id}.json").write_text(json.dumps(meta, ensure_ascii=False, default=str), encoding="utf-8")
    return meta


# Forget the connection and remove the tables copied from it. Returns how many datasets were removed.
def disconnect(user_id: str, workspace_id: str) -> int:
    ws = engine.safe_id(workspace_id)
    if not _load(user_id, ws):
        raise ValueError("this workspace has no database connection")
    copied = [m for m in engine.list_datasets(user_id, ws) if m.get("db_table")]
    for m in copied:
        engine.remove_dataset(user_id, m)
    (folder(user_id, ws) / "connection.json").unlink()
    (folder(user_id, ws) / SQLITE_FILE).unlink(missing_ok=True)
    return len(copied)
