"""
# api/main.py - the web API: every screen of the app talks to these routes

A thin layer: each route checks who is asking, then calls one plain-Python function that does the work.

- **Who is asking:** every route with a `{user_id}` in it needs that user's sign-in token; routes with a
  `{workspace_id}` also check that the workspace exists.
- **Errors are mapped once** (bottom of the setup section), so routes stay one or two lines: not found -> 404,
  bad input -> 400, wrong feed token -> 401, too slow -> 408, the day's AI budget used up -> 429,
  LibreOffice / a price site down -> 503 / 502.
- Plain `def` routes run in a background thread (FastAPI does that), so slow work never blocks other requests.

Run: `uvicorn api.main:app --port 8000`. Every route is listed at http://localhost:8000/docs.
"""
import asyncio
import json
import logging
import os
import secrets
import subprocess
import sys
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Optional

import asyncpg
import bcrypt
import httpx
import jwt
from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from wealthos import engine, settings
from wealthos.services.llm_client import CostCapReached

for _stream in (sys.stdout, sys.stderr):  # a ₹ in a log line must never crash a Windows console
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_URL = os.getenv("WEALTHOS_DB_URL", "").replace("postgresql+asyncpg://", "postgresql://")
JWT_SECRET = ""  # set by settings.check_api() at startup; empty = every user route answers 503 (fails closed)
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ── Setup: database tables, the app, error mapping ─────────────────────────────────────────────────────────

# Old builds seeded these with passwords published in the README; any copy still holding them is locked out.
OLD_DEFAULT_LOGINS = [("admin", "wealthos123"), ("demo", "demo123")]


# Lock old default accounts, and with WEALTHOS_DEMO_SEED=1 create one demo-<random> account (password printed once).
async def _secure_accounts(conn) -> None:
    for name, password in OLD_DEFAULT_LOGINS:
        row = await conn.fetchrow("SELECT password_hash FROM users WHERE username = $1", name)
        if row and row["password_hash"] != "!" and bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
            await conn.execute("UPDATE users SET password_hash = '!' WHERE username = $1", name)  # '!' never matches
            logger.warning("disabled the old default account %r (its password was public)", name)
    if os.getenv("WEALTHOS_DEMO_SEED") == "1":
        name, password = f"demo-{secrets.token_hex(3)}", secrets.token_urlsafe(12)
        await conn.execute("INSERT INTO users (username, password_hash) VALUES ($1, $2)", name, _hash(password))
        print(f"demo account created: {name} / {password}  (shown once)", flush=True)


# Create the two Postgres tables the app uses (users, llm_usage) and secure the accounts in it.
async def _prepare_database() -> None:
    if not DB_URL:
        return
    try:
        conn = await asyncpg.connect(DB_URL)
        try:
            await conn.execute("""CREATE TABLE IF NOT EXISTS users (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(), username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL, created_at TIMESTAMP DEFAULT NOW())""")
            await conn.execute("""CREATE TABLE IF NOT EXISTS llm_usage (
                id SERIAL PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL, prompt_tokens INTEGER NOT NULL,
                completion_tokens INTEGER NOT NULL, total_tokens INTEGER NOT NULL, estimated_cost_usd NUMERIC NOT NULL,
                user_id TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
            await _secure_accounts(conn)
        finally:
            await conn.close()
    except Exception as e:
        logger.warning("database not ready, sign-in will fail until it is: %s", e)


# Refuse to start on a weak or missing secret (settings.py), then set up the database.
@asynccontextmanager
async def lifespan(app: FastAPI):
    global JWT_SECRET
    JWT_SECRET = settings.check_api()
    if not JWT_SECRET:
        logger.warning("WEALTHOS_DEV_INSECURE_AUTH=1: sign-in checks are OFF (local development only)")
    await _prepare_database()
    yield


app = FastAPI(title="WealthOS API", description="Your data in, trusted numbers and dashboards out", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(","),
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

ERRORS = [(LookupError, 404), (ValueError, 400), (PermissionError, 401), (TimeoutError, 408), (CostCapReached, 429),
          (RuntimeError, 503), (subprocess.TimeoutExpired, 504), (httpx.HTTPError, 502)]


# Turn an error raised by the modules into an HTTP answer with a readable message.
def _error_handler(status: int):
    # Answer with the error's status code and its message.
    async def handle(_request: Request, exc: Exception):
        detail = "the job took too long and was stopped" if isinstance(exc, subprocess.TimeoutExpired) else str(exc)
        return JSONResponse(status_code=status, content={"detail": detail})
    return handle


for _exc, _status in ERRORS:
    app.add_exception_handler(_exc, _error_handler(_status))


# ── Sign-in: passwords (bcrypt), 7-day tokens (JWT), and the checks every route uses ────────────────────────

# Hash a password (bcrypt reads at most 72 bytes, so cut explicitly).
def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt()).decode()


# A signed sign-in token for a user, valid WEALTHOS_TOKEN_DAYS (default 7).
def _issue_token(user_id: str, username: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": user_id, "username": username, "iat": now,
                       "exp": now + timedelta(days=settings.token_days())}, JWT_SECRET, algorithm="HS256")


# Check the Bearer token belongs to `user_id`. No secret = refuse, unless insecure dev auth was switched on.
async def verify_user_token(user_id: str, authorization: Optional[str] = Header(None)) -> str:
    if not JWT_SECRET:
        if settings.insecure_dev_auth():
            logger.warning("insecure dev auth: trusting user id %r from the path", user_id)
            return user_id
        raise HTTPException(503, "sign-in is not configured on this server")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing or malformed Authorization header")
    try:
        payload = jwt.decode(authorization[7:].strip(), JWT_SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError as e:  # includes an expired token
        raise HTTPException(401, f"invalid token: {e}")
    if payload.get("sub") != user_id:
        raise HTTPException(403, "this token does not belong to that user")
    return user_id


# 404 unless the workspace exists for this user.
def require_workspace(user_id: str, workspace_id: str) -> None:
    from wealthos.workspaces import get_workspace
    if not get_workspace(user_id, workspace_id):
        raise HTTPException(404, "workspace not found")


# Check the token, then that the workspace exists (for routes with both in the path).
async def verify_user_workspace(user_id: str, workspace_id: str, authorization: Optional[str] = Header(None)) -> None:
    await verify_user_token(user_id, authorization)
    require_workspace(user_id, workspace_id)


USER = [Depends(verify_user_token)]
USER_WS = [Depends(verify_user_workspace)]


_DUMMY_HASH = bcrypt.hashpw(b"no-such-user", bcrypt.gensalt()).decode()


# What sign-up and sign-in send.
class Credentials(BaseModel):
    username: str
    password: str


# Run one query against the users table.
async def _users(query: str, *args):
    if not DB_URL:
        raise HTTPException(503, "sign-in needs the database (WEALTHOS_DB_URL)")
    conn = await asyncpg.connect(DB_URL)
    try:
        return await conn.fetchrow(query, *args)
    finally:
        await conn.close()


@app.post("/auth/signup")
async def signup(body: Credentials):
    """Create an account (username 3+ characters, password 6+) and sign in."""
    name = body.username.strip()
    if len(name) < 3 or len(body.password) < 6:
        raise HTTPException(400, "username needs 3+ characters and password 6+")
    try:
        row = await _users("INSERT INTO users (username, password_hash) VALUES ($1, $2) RETURNING id::text, username",
                           name, _hash(body.password))
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, "username already taken")
    return {"user_id": row["id"], "username": row["username"], "token": _issue_token(row["id"], row["username"])}


@app.post("/auth/login")
async def login(body: Credentials):
    """Sign in with username and password."""
    row = await _users("SELECT id::text, username, password_hash FROM users WHERE username = $1", body.username.strip())
    stored = row["password_hash"] if row and row["password_hash"].startswith("$2") else None  # '!' = disabled
    # Unknown or disabled users still pay for one bcrypt check, so timing doesn't reveal which usernames exist.
    ok = bcrypt.checkpw(body.password.encode()[:72], (stored or _DUMMY_HASH).encode()) and stored is not None
    if not ok:
        raise HTTPException(401, "wrong username or password")
    return {"user_id": row["id"], "username": row["username"], "token": _issue_token(row["id"], row["username"])}


@app.get("/health")
def health():
    """Is the API up."""
    return {"status": "ok"}


# ── Workspaces: named groups of data that chats and dashboards are tied to ─────────────────────────────────

# A workspace name.
class NameBody(BaseModel):
    name: str


@app.get("/workspaces/{user_id}", dependencies=USER)
def workspaces_list(user_id: str):
    """The user's workspaces (My files first) with their table counts."""
    from wealthos.workspaces import list_workspaces
    return list_workspaces(user_id)


@app.post("/workspaces/{user_id}", dependencies=USER)
def workspaces_create(user_id: str, body: NameBody):
    """Create a workspace."""
    from wealthos.workspaces import create_workspace
    return create_workspace(user_id, body.name)


@app.patch("/workspaces/{user_id}/{workspace_id}", dependencies=USER_WS)
def workspaces_rename(user_id: str, workspace_id: str, body: NameBody):
    """Rename a workspace."""
    from wealthos.workspaces import rename_workspace
    return rename_workspace(user_id, workspace_id, body.name)


@app.delete("/workspaces/{user_id}/{workspace_id}", dependencies=USER_WS)
def workspaces_delete(user_id: str, workspace_id: str):
    """Delete a workspace with its tables and dashboards (My files can't be deleted)."""
    from wealthos.workspaces import delete_workspace
    return {"deleted_datasets": delete_workspace(user_id, workspace_id)}


# ── Data in: file uploads, Google Sheets, column mapping ─────────────────────────────────────────────────────

# Stream an upload to disk in 1 MB pieces and stop at 20 MB (an endless upload must not fill the disk).
async def _save_upload(file: UploadFile, dest: Path) -> None:
    written = 0
    with dest.open("wb") as f:
        while chunk := await file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "file too large: 20 MB at most")
            f.write(chunk)


# The user's folder of original uploads.
def _raw_dir(user_id: str) -> Path:
    path = engine.user_dir(user_id) / "raw"
    path.mkdir(parents=True, exist_ok=True)
    return path


# A saved file becomes tables: kept under raw/<sha256>/, read, auto-mapped if its layout is known, saved to the
# workspace; a filing PDF is also indexed for questions in chat. Returns what was read.
def _ingest_file(user_id: str, tmp: Path, filename: str, password: Optional[str], workspace_id: str) -> dict:
    from wealthos.ingest import file_hash, parse_file
    from wealthos.mapping import auto_apply
    digest = file_hash(tmp)
    dest = _raw_dir(user_id) / digest / filename
    dest.parent.mkdir(exist_ok=True)
    tmp.replace(dest)  # the same file uploaded twice lands in the same folder
    try:
        tables = parse_file(dest, password, filename)
    except ValueError:
        raise
    except Exception as e:  # a broken or unreadable file
        raise ValueError(f"could not read the file: {type(e).__name__}: {e}") from e
    (dest.parent / "tables.json").write_text(json.dumps([t.model_dump(mode="json") for t in tables], ensure_ascii=False),
                                             encoding="utf-8")  # kept so unknown tables can be mapped later
    tables = [auto_apply(user_id, t) for t in tables]
    saved = engine.save_tables(user_id, tables, workspace_id)
    doc = None
    if dest.suffix.lower() == ".pdf" and all(t.shape in ("statements", "unknown") for t in tables):  # a filing, not a CAS
        from wealthos import research
        try:
            doc = research.add(user_id, workspace_id, dest, digest[:16])
        except Exception as e:  # indexing is a bonus: it never fails the upload
            logger.warning("research index failed for %s: %s", filename, e)
    return {"file": filename, "workspace_id": workspace_id, "content_hash": digest, "research": doc,
            "datasets": [m["dataset_id"] for m in saved],
            "tables": [{"index": i, "shape": t.shape, "status": t.status, "rows": len(t.rows), "columns": t.columns,
                        "sheet": t.source.sheet, "sample": t.rows[:5], "warnings": t.warnings} for i, t in enumerate(tables)]}


@app.post("/sources/upload")
async def upload_source(user_id: str = Form(...), file: UploadFile = File(...), password: Optional[str] = Form(None),
                        workspace_id: str = Form("default"), authorization: Optional[str] = Header(None)):
    """Upload a CSV / Excel / PDF (bank statement, tradebook, holdings, prices, CAS with its password, filing)."""
    await verify_user_workspace(user_id, workspace_id, authorization)
    name = Path(file.filename or "upload").name  # never a path from the browser
    if Path(name).suffix.lower() not in (".csv", ".xlsx", ".pdf"):
        raise HTTPException(400, "upload a .csv, .xlsx or .pdf file")
    tmp = _raw_dir(user_id) / f"upload_{uuid.uuid4().hex}{Path(name).suffix.lower()}"
    await _save_upload(file, tmp)
    return await asyncio.to_thread(_ingest_file, user_id, tmp, name, password, workspace_id)


# A Google Sheet link to read into a workspace.
class SheetBody(BaseModel):
    user_id: str
    url: str                       # a Google Sheets link shared as "Anyone with the link can view"
    workspace_id: str = "default"


@app.post("/sources/sheet")
async def upload_sheet(body: SheetBody, authorization: Optional[str] = Header(None)):
    """Read one tab of a Google Sheet shared by link, exactly like an uploaded CSV."""
    await verify_user_workspace(body.user_id, body.workspace_id, authorization)
    from wealthos.ingest.sheets import fetch_sheet_csv
    data, name = await fetch_sheet_csv(body.url, MAX_UPLOAD_BYTES)
    tmp = _raw_dir(body.user_id) / f"upload_{uuid.uuid4().hex}.csv"
    tmp.write_bytes(data)
    return await asyncio.to_thread(_ingest_file, body.user_id, tmp, name, None, body.workspace_id)


@app.get("/sources/{user_id}/datasets", dependencies=USER)
def list_user_datasets(user_id: str, workspace_id: Optional[str] = None):
    """The user's saved tables: kind, source file, rows, warnings."""
    return engine.list_datasets(user_id, workspace_id)


# One table as it was read from an upload (kept in tables.json), for mapping.
def _uploaded_table(user_id: str, content_hash: str, index: int):
    from wealthos.ingest import ExtractedTable
    path = _raw_dir(user_id) / Path(content_hash).name / "tables.json"
    tables = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    if not 0 <= index < len(tables):
        raise LookupError("that upload or table was not found")
    return ExtractedTable(**tables[index])


@app.get("/sources/{user_id}/{content_hash}/tables/{index}/mapping", dependencies=USER)
async def suggest_mapping(user_id: str, content_hash: str, index: int):
    """Suggest what each column of an unrecognised table means (saved layout, name rules, then one AI call)."""
    from wealthos.ingest.tabular import SHAPES
    from wealthos.mapping import suggest
    table = _uploaded_table(user_id, content_hash, index)
    return {"columns": [c for c in table.columns if c != "source_row"], "profile": table.profile,
            "suggestion": await suggest(user_id, table),
            "shapes": {s: {"required": spec["required"], "columns": list(spec["names"])} for s, spec in SHAPES.items()}}


# The user's choice of what each column of an unknown table means.
class MappingBody(BaseModel):
    shape: str
    column_map: dict[str, str]  # {standard column: file column}
    workspace_id: str = "default"


@app.post("/sources/{user_id}/{content_hash}/tables/{index}/mapping", dependencies=USER)
def confirm_mapping(user_id: str, content_hash: str, index: int, body: MappingBody):
    """Save a confirmed mapping (remembered for this layout) and the cleaned table."""
    from wealthos.mapping import confirm
    require_workspace(user_id, body.workspace_id)
    parsed, saved = confirm(user_id, _uploaded_table(user_id, content_hash, index), body.shape, body.column_map,
                            body.workspace_id)
    return {"datasets": [m["dataset_id"] for m in saved], "shape": parsed.shape, "rows": len(parsed.rows),
            "sample": parsed.rows[:5], "warnings": parsed.warnings}


# ── Database connections: copy tables in from Postgres / MySQL / SQLite (read-only, encrypted) ─────────────

# A database connection string.
class ConnectBody(BaseModel):
    url: str  # postgresql://user:password@host:5432/db or mysql://... (use a read-only database user)


# Which database tables to copy in.
class SnapshotBody(BaseModel):
    tables: list[str] = []  # "schema.table" names; empty = refresh the tables copied last time


@app.post("/connections/{user_id}/{workspace_id}", dependencies=USER_WS)
def connection_create(user_id: str, workspace_id: str, body: ConnectBody):
    """Check a connection string, store it encrypted, list the database's tables."""
    from wealthos.connections import connect
    return connect(user_id, workspace_id, body.url)


@app.post("/connections/{user_id}/{workspace_id}/sqlite", dependencies=USER_WS)
async def connection_sqlite(user_id: str, workspace_id: str, file: UploadFile = File(...)):
    """Upload a SQLite database file (20 MB at most) and connect it."""
    from wealthos.connections import SQLITE_FILE, connect_sqlite, folder
    folder(user_id, workspace_id).mkdir(parents=True, exist_ok=True)
    await _save_upload(file, folder(user_id, workspace_id) / SQLITE_FILE)
    return await asyncio.to_thread(connect_sqlite, user_id, workspace_id)


@app.get("/connections/{user_id}/{workspace_id}", dependencies=USER_WS)
def connection_get(user_id: str, workspace_id: str):
    """The workspace's connection (password hidden) and the tables copied, or null."""
    from wealthos.connections import info
    return info(user_id, workspace_id)


@app.get("/connections/{user_id}/{workspace_id}/tables", dependencies=USER_WS)
def connection_tables(user_id: str, workspace_id: str):
    """The tables the connected database has right now."""
    from wealthos.connections import list_tables
    return list_tables(user_id, workspace_id)


@app.post("/connections/{user_id}/{workspace_id}/snapshot", dependencies=USER_WS)
def connection_snapshot(user_id: str, workspace_id: str, body: SnapshotBody):
    """Copy the chosen tables in (again = refresh); per-table failures come back in `errors`."""
    from wealthos.connections import snapshot
    return snapshot(user_id, workspace_id, body.tables or None)


@app.delete("/connections/{user_id}/{workspace_id}", dependencies=USER_WS)
def connection_delete(user_id: str, workspace_id: str):
    """Forget the connection and remove the tables copied from it."""
    from wealthos.connections import disconnect
    return {"deleted_datasets": disconnect(user_id, workspace_id)}


# ── Live feeds: rows pushed in with a token, or pulled from a URL on a schedule ────────────────────────────

# A new live feed: push (rows sent to us) or pull (we fetch a URL).
class FeedBody(BaseModel):
    name: str                 # the table the rows go into
    kind: Literal["push", "pull"] = "push"
    url: Optional[str] = None  # pull only: an https URL returning JSON rows or CSV
    interval_s: int = 300     # pull only: seconds between fetches (30 .. 86400)


@app.post("/feeds/{user_id}/{workspace_id}", dependencies=USER_WS)
def feeds_create(user_id: str, workspace_id: str, body: FeedBody):
    """Create a feed (a push feed's token is shown once, in this answer)."""
    from wealthos.feeds import create_feed
    return create_feed(user_id, workspace_id, body.name, body.kind, body.url, body.interval_s)


@app.get("/feeds/{user_id}/{workspace_id}", dependencies=USER_WS)
def feeds_list(user_id: str, workspace_id: str):
    """The workspace's feeds: rows, last push / pull, last error."""
    from wealthos.feeds import list_feeds
    return list_feeds(user_id, workspace_id)


@app.delete("/feeds/{user_id}/{workspace_id}/{feed_id}", dependencies=USER_WS)
def feeds_delete(user_id: str, workspace_id: str, feed_id: str):
    """Delete a feed and the rows it brought in."""
    from wealthos.feeds import delete_feed
    delete_feed(user_id, workspace_id, feed_id)
    return {"deleted": feed_id}


@app.post("/feeds/{user_id}/{workspace_id}/{feed_id}/push")
def feeds_push(user_id: str, workspace_id: str, feed_id: str, body: Any = Body(...), x_feed_token: Optional[str] = Header(None)):
    """Add rows (a JSON list, or {"rows": [...]}, 5,000 at most) with the feed's token instead of a sign-in."""
    from wealthos.feeds import append_rows, check_token
    check_token(user_id, workspace_id, feed_id, x_feed_token)
    return append_rows(user_id, workspace_id, feed_id, body.get("rows") if isinstance(body, dict) else body)


@app.post("/feeds/{user_id}/{workspace_id}/{feed_id}/pull", dependencies=USER_WS)
def feeds_pull_now(user_id: str, workspace_id: str, feed_id: str):
    """Fetch a pull feed now instead of waiting for the worker."""
    from wealthos.feeds import pull
    return pull(user_id, workspace_id, feed_id)


# ── Free daily prices: NSE stocks / ETFs and mutual fund NAVs ─────────────────────────────────────────────

# Which prices to fetch and from when.
class PriceBody(BaseModel):
    source: Literal["nse", "mf"]   # NSE trading symbols, or mutual fund scheme codes (from /search)
    symbols: list[str]
    start: Optional[date] = None   # default: one year ago


@app.get("/prices/{user_id}/{workspace_id}", dependencies=USER_WS)
def price_sources(user_id: str, workspace_id: str):
    """The workspace's price sources and how far they go."""
    from wealthos.prices import list_sources
    return list_sources(user_id, workspace_id)


@app.post("/prices/{user_id}/{workspace_id}", dependencies=USER_WS)
def price_add(user_id: str, workspace_id: str, body: PriceBody):
    """Fetch daily prices into the `prices` table; they are updated every evening."""
    from wealthos.prices import add
    return add(user_id, workspace_id, body.source, body.symbols, body.start)


@app.post("/prices/{user_id}/{workspace_id}/refresh", dependencies=USER_WS)
def price_refresh(user_id: str, workspace_id: str):
    """Bring every price source up to today now."""
    from wealthos.prices import refresh
    return refresh(user_id, workspace_id)


@app.get("/prices/{user_id}/{workspace_id}/search", dependencies=USER_WS)
def price_search_mf(user_id: str, workspace_id: str, q: str):
    """Mutual fund schemes whose name matches."""
    from wealthos.prices import search_mf
    return search_mf(q)


@app.delete("/prices/{user_id}/{workspace_id}/{source_id}", dependencies=USER_WS)
def price_remove(user_id: str, workspace_id: str, source_id: str):
    """Stop a price source and remove its prices."""
    from wealthos.prices import remove
    remove(user_id, workspace_id, source_id)
    return {"deleted": source_id}


# ── Understanding the data: column meanings, how tables join, the workspace's own metrics ───────────────────

# What each column means.
class ColumnsBody(BaseModel):
    columns: dict[str, dict[str, dict]]  # {table: {column: {"role": "measure", "description": "..."}}}


# How the workspace's tables join.
class RelationshipsBody(BaseModel):
    relationships: list[dict]  # [{"left": "orders.customer_id", "right": "customers.id", "kind": "many_to_one"}]


# A workspace metric: which table, which date column, and the formula.
class MetricBody(BaseModel):
    table: str
    date: str               # the date column periods filter on
    sql: str                # one aggregate expression, e.g. SUM(amount)
    name: str = ""
    unit: str = ""
    time_semantics: Literal["flow", "point_in_time"] = "flow"


@app.get("/semantic/{user_id}/{workspace_id}", dependencies=USER_WS)
def semantic_get(user_id: str, workspace_id: str):
    """Columns (saved meaning or a suggestion), relationships, metrics and remembered meanings."""
    from wealthos import semantic
    from wealthos.agent import definitions
    model = semantic.load(user_id, workspace_id)
    return {"columns": semantic.suggest_columns(user_id, workspace_id), "relationships": model["relationships"],
            "workspace_metrics": model["metrics"], "metrics": engine.load_metrics(user_id, workspace_id),
            "definitions": definitions(user_id)}


@app.put("/semantic/{user_id}/{workspace_id}/columns", dependencies=USER_WS)
def semantic_columns(user_id: str, workspace_id: str, body: ColumnsBody):
    """Save what columns mean."""
    from wealthos.semantic import save_columns
    return save_columns(user_id, workspace_id, body.columns)


@app.put("/semantic/{user_id}/{workspace_id}/relationships", dependencies=USER_WS)
def semantic_relationships(user_id: str, workspace_id: str, body: RelationshipsBody):
    """Replace how the workspace's tables join."""
    from wealthos.semantic import save_relationships
    return save_relationships(user_id, workspace_id, body.relationships)


@app.put("/semantic/{user_id}/{workspace_id}/metrics/{key}", dependencies=USER_WS)
def semantic_metric_save(user_id: str, workspace_id: str, key: str, body: MetricBody):
    """Add or change a workspace metric (checked and test-run before it is saved)."""
    from wealthos.semantic import save_metric
    return save_metric(user_id, workspace_id, key, body.model_dump())


@app.delete("/semantic/{user_id}/{workspace_id}/metrics/{key}", dependencies=USER_WS)
def semantic_metric_delete(user_id: str, workspace_id: str, key: str):
    """Remove a workspace metric."""
    from wealthos.semantic import delete_metric
    return delete_metric(user_id, workspace_id, key)


# ── Numbers: trusted metrics and checked read-only SQL (every answer says where it came from) ──────────────

# A metrics question: which metrics, split by what, for which period.
class MetricQueryBody(BaseModel):
    user_id: str
    workspace_id: Optional[str] = None  # None = all of the user's data
    metrics: list[str]
    dimensions: list[str] = []
    time: str = "fy"
    filters: list[dict] = []


# One read-only SQL query.
class SqlBody(BaseModel):
    user_id: str
    sql: str
    workspace_id: Optional[str] = None


@app.get("/metrics")
def metric_catalog():
    """Every built-in metric: what it reads and its unit."""
    return {k: {f: v[f] for f in ("name", "shape", "unit", "time_semantics")} for k, v in engine.load_metrics().items()}


@app.post("/query/metric")
async def query_metric(body: MetricQueryBody, authorization: Optional[str] = Header(None)):
    """Compute metrics for a period (time = "fy", "mtd", "2026-01-01..2026-03-31" ...)."""
    await verify_user_token(body.user_id, authorization)
    q = engine.MetricQuery(metrics=body.metrics, dimensions=body.dimensions, time=body.time, filters=body.filters)
    return (await asyncio.to_thread(engine.run_metric, body.user_id, q, None, body.workspace_id)).model_dump()


@app.post("/query/sql")
async def query_sql(body: SqlBody, authorization: Optional[str] = Header(None)):
    """One checked read-only SELECT over the user's own tables."""
    await verify_user_token(body.user_id, authorization)
    return (await asyncio.to_thread(engine.run_sql, body.user_id, body.sql, body.workspace_id)).model_dump()


# ── Chat: plain-English questions, saved chats, remembered meanings ─────────────────────────────────────────

# A chat question.
class AskBody(BaseModel):
    user_id: str
    question: str
    workspace_id: Optional[str] = None
    previous_plan: Optional[dict] = None  # the last answer's plan, for follow-up questions
    define: Optional[dict] = None         # {"term", "value"} when answering a clarify question
    chat_id: Optional[str] = None         # continue this saved chat (its workspace wins); none = a new chat


@app.post("/assistant/ask")
async def assistant_ask(body: AskBody, authorization: Optional[str] = Header(None)):
    """Answer a question with its numbers, where they came from, and whether the text passed the number check."""
    await verify_user_token(body.user_id, authorization)
    from wealthos import agent
    workspace = agent.get_chat(body.user_id, body.chat_id)["workspace_id"] if body.chat_id else body.workspace_id or "default"
    answer = (await agent.ask(body.user_id, body.question, body.previous_plan, body.define, workspace)).model_dump()
    chat_id = agent.save_turn(body.user_id, body.chat_id, workspace, body.question, answer)
    return {**answer, "chat_id": chat_id}


@app.get("/assistant/{user_id}/chats", dependencies=USER)
def assistant_chats(user_id: str, workspace_id: Optional[str] = None):
    """The user's saved chats, newest first."""
    from wealthos.agent import list_chats
    return list_chats(user_id, workspace_id)


@app.get("/assistant/{user_id}/chats/{chat_id}", dependencies=USER)
def assistant_chat(user_id: str, chat_id: str):
    """One saved chat with every question and answer."""
    from wealthos.agent import get_chat
    return get_chat(user_id, chat_id)


@app.delete("/assistant/{user_id}/chats/{chat_id}", dependencies=USER)
def assistant_delete_chat(user_id: str, chat_id: str):
    """Delete one saved chat."""
    from wealthos.agent import delete_chat
    delete_chat(user_id, chat_id)
    return {"deleted": chat_id}


@app.get("/assistant/{user_id}/definitions", dependencies=USER)
def assistant_definitions(user_id: str):
    """What the user told the assistant a word means ("rent" = the Rent description ...)."""
    from wealthos.agent import definitions
    return definitions(user_id)


@app.delete("/assistant/{user_id}/definitions/{term}", dependencies=USER)
def assistant_forget(user_id: str, term: str):
    """Forget one remembered meaning."""
    from wealthos.agent import delete_definition
    return delete_definition(user_id, term)


@app.get("/research/{user_id}/{workspace_id}", dependencies=USER_WS)
def research_docs(user_id: str, workspace_id: str):
    """The workspace's filings / annual reports whose text chat can answer from."""
    from wealthos.research import list_docs
    return list_docs(user_id, workspace_id)


@app.delete("/research/{user_id}/{workspace_id}/{doc_id}", dependencies=USER_WS)
def research_delete(user_id: str, workspace_id: str, doc_id: str):
    """Stop using one document in chat (its statements table stays)."""
    from wealthos.research import delete_doc
    delete_doc(user_id, workspace_id, doc_id)
    return {"deleted": doc_id}


# ── Dashboards: built only from checked edits; every save is kept, so undo always works ─────────────────────

# A new dashboard's title and workspace.
class DashboardBody(BaseModel):
    title: str
    workspace_id: str = "default"


# Dashboard edits, plus the edit number the screen was showing.
class OpsBody(BaseModel):
    ops: list[dict]                     # [{"op": "add_tile", "value": {...}}, ...]
    base_version: Optional[int] = None  # the edit the screen showed; refused if the dashboard has moved on


# Which edit to go back to.
class RevertBody(BaseModel):
    version: Optional[int] = None  # an edit number; none = undo the last change


# A plain-English change to a dashboard.
class InstructionBody(BaseModel):
    instruction: str  # e.g. "make revenue a bar chart and add last year"


# A chat answer to add to a dashboard.
class PinBody(BaseModel):
    plan: dict       # the plan of a chat answer
    title: str = ""


# The period to recompute a dashboard for.
class PeriodBody(BaseModel):
    time: Optional[str] = None  # e.g. "mtd"; none = as saved


# Report options: period, AI summary on/off, subtitle.
class ReportBody(BaseModel):
    time: Optional[str] = None
    narrative: bool = True  # the AI-written summary (every number checked)
    subtitle: str = ""


# A dashboards function's answer as plain JSON.
def _plain(result):
    return result.model_dump() if hasattr(result, "model_dump") else result


@app.get("/dashboards/{user_id}", dependencies=USER)
def dashboards_list(user_id: str, workspace_id: Optional[str] = None):
    """The user's dashboards, newest first."""
    from wealthos.dashboards import list_dashboards
    return list_dashboards(user_id, workspace_id)


@app.post("/dashboards/{user_id}", dependencies=USER)
def dashboards_create(user_id: str, body: DashboardBody):
    """Create an empty dashboard in a workspace."""
    from wealthos.dashboards import create
    require_workspace(user_id, body.workspace_id)
    return _plain(create(user_id, body.title, body.workspace_id))


@app.get("/dashboards/{user_id}/{dashboard_id}", dependencies=USER)
def dashboards_get(user_id: str, dashboard_id: str, version: Optional[int] = None):
    """A dashboard at an edit (default: the latest)."""
    from wealthos.dashboards import get
    return _plain(get(user_id, dashboard_id, version))


@app.delete("/dashboards/{user_id}/{dashboard_id}", dependencies=USER)
def dashboards_delete(user_id: str, dashboard_id: str):
    """Delete a dashboard and its whole edit history."""
    from wealthos.dashboards import delete
    delete(user_id, dashboard_id)
    return {"deleted": dashboard_id}


@app.post("/dashboards/{user_id}/{dashboard_id}/ops", dependencies=USER)
def dashboards_ops(user_id: str, dashboard_id: str, body: OpsBody):
    """Apply edits (all checked first; one bad edit refuses the lot) and save them."""
    from wealthos.dashboards import Op, save_ops
    return _plain(save_ops(user_id, dashboard_id, [Op(**o) for o in body.ops], body.base_version))


@app.post("/dashboards/{user_id}/{dashboard_id}/revert", dependencies=USER)
def dashboards_revert(user_id: str, dashboard_id: str, body: RevertBody):
    """Undo the last change, or go back to a chosen edit (saved as a new edit)."""
    from wealthos.dashboards import revert
    return _plain(revert(user_id, dashboard_id, body.version))


@app.post("/dashboards/{user_id}/{dashboard_id}/ai-edit", dependencies=USER)
async def dashboards_ai_edit(user_id: str, dashboard_id: str, body: InstructionBody):
    """Turn an instruction into checked edits and a preview; nothing is saved until they are sent to /ops."""
    from wealthos.dashboards import ai_edit
    return await ai_edit(user_id, dashboard_id, body.instruction)


@app.post("/dashboards/{user_id}/{dashboard_id}/pin", dependencies=USER)
def dashboards_pin(user_id: str, dashboard_id: str, body: PinBody):
    """Add a chat answer to the dashboard (its query, never its numbers)."""
    from wealthos.dashboards import pin_answer
    return _plain(pin_answer(user_id, dashboard_id, body.plan, body.title))


@app.post("/dashboards/{user_id}/{dashboard_id}/refresh", dependencies=USER)
def dashboards_refresh(user_id: str, dashboard_id: str, body: PeriodBody):
    """Recompute every tile (no AI) and say per tile what changed since last time."""
    from wealthos.dashboards import refresh
    return refresh(user_id, dashboard_id, body.time)


@app.get("/dashboards/{user_id}/{dashboard_id}/refresh", dependencies=USER)
def dashboards_last_refresh(user_id: str, dashboard_id: str):
    """The last refresh's results (null if never), so a dashboard opens instantly."""
    from wealthos.dashboards import last_refresh
    return last_refresh(user_id, dashboard_id)


@app.get("/dashboards/{user_id}/{dashboard_id}/export.xlsx", dependencies=USER)
def dashboards_export(user_id: str, dashboard_id: str):
    """The dashboard as Excel: a sheet per tile and a sheet saying where every number came from."""
    from wealthos.dashboards import export_xlsx
    return Response(export_xlsx(user_id, dashboard_id), media_type=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="dashboard-{Path(dashboard_id).name}.xlsx"'})


@app.post("/dashboards/{user_id}/{dashboard_id}/report", dependencies=USER)
async def dashboards_report(user_id: str, dashboard_id: str, body: ReportBody):
    """The dashboard as a printable report with a number-checked summary."""
    from wealthos.reports import build_report
    return await build_report(user_id, dashboard_id, body.time, body.narrative, body.subtitle)


# The table and columns to build a template dashboard from.
class TemplateBody(BaseModel):
    table: Optional[str] = None   # business templates: the table to use (from /suggest)
    mapping: dict[str, str] = {}  # {slot: column}
    title: Optional[str] = None


@app.get("/templates")
def templates_catalog():
    """Every ready-made dashboard and the columns it needs."""
    from wealthos.templates import catalog
    return catalog()


@app.get("/templates/{user_id}/{workspace_id}/{template_id}/suggest", dependencies=USER_WS)
def templates_suggest(user_id: str, workspace_id: str, template_id: str):
    """The best table and column for each of the template's slots, and what is missing."""
    from wealthos.templates import suggest
    return suggest(user_id, workspace_id, template_id)


@app.post("/templates/{user_id}/{workspace_id}/{template_id}/apply", dependencies=USER_WS)
def templates_apply(user_id: str, workspace_id: str, template_id: str, body: TemplateBody):
    """Create a dashboard from the template with the confirmed columns."""
    from wealthos.templates import apply
    return _plain(apply(user_id, workspace_id, template_id, body.table, body.mapping, body.title))


# ── Excel templates: your workbook filled with a table and recalculated by LibreOffice ───────────────────────

@app.post("/excel/{user_id}/{workspace_id}", dependencies=USER_WS)
async def excel_add(user_id: str, workspace_id: str, file: UploadFile = File(...), data_table: str = Form(...),
                    name: str = Form("")):
    """Upload a template (.xlsx with a 'Data' sheet and named result cells); it is checked before it is kept."""
    from wealthos import excel
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / Path(file.filename or "template.xlsx").name
        await _save_upload(file, dest)
        return await asyncio.to_thread(excel.add, user_id, workspace_id, dest, name, data_table)


@app.get("/excel/{user_id}/{workspace_id}", dependencies=USER_WS)
def excel_list(user_id: str, workspace_id: str):
    """The workspace's templates with their last run."""
    from wealthos.excel import list_templates
    return list_templates(user_id, workspace_id)


@app.post("/excel/{user_id}/{workspace_id}/{template_id}/run", dependencies=USER_WS)
def excel_run(user_id: str, workspace_id: str, template_id: str):
    """Fill the template with today's data, recalculate it, return the named results."""
    from wealthos.excel import run
    return run(user_id, workspace_id, template_id)


@app.get("/excel/{user_id}/{workspace_id}/{template_id}/filled.xlsx", dependencies=USER_WS)
def excel_download(user_id: str, workspace_id: str, template_id: str):
    """The filled, recalculated workbook from the last run."""
    from wealthos.excel import filled_path
    return FileResponse(filled_path(user_id, workspace_id, template_id), media_type=XLSX,
                        filename=f"filled-{Path(template_id).name}.xlsx")


@app.delete("/excel/{user_id}/{workspace_id}/{template_id}", dependencies=USER_WS)
def excel_delete(user_id: str, workspace_id: str, template_id: str):
    """Delete a template."""
    from wealthos.excel import delete
    delete(user_id, workspace_id, template_id)
    return {"deleted": template_id}


# ── Strategy lab and tax (both educational, never advice) ───────────────────────────────────────────────────

# A trading idea in plain English.
class StrategyTextBody(BaseModel):
    text: str  # e.g. "buy NIFTYBEES when the 50-day average crosses above the 200-day"


# A checked trading rule to test.
class StrategyRunBody(BaseModel):
    spec: dict                          # the checked rule from /parse (or written by hand)
    generated_by: Optional[str] = None  # the AI model that wrote it (adds a "it may know the future" warning)


# Income, deductions and age for the tax comparison.
class TaxBody(BaseModel):
    fy: str = "2025-26"
    salary: float = 0
    other_income: float = 0
    deductions_80c: float = 0
    deductions_80d: float = 0
    age: Literal["below_60", "60_to_80", "above_80"] = "below_60"


@app.post("/strategy/{user_id}/{workspace_id}/parse", dependencies=USER_WS)
async def strategy_parse(user_id: str, workspace_id: str, body: StrategyTextBody):
    """Turn a plain-English idea into a checked rule and read it back; nothing runs yet."""
    from wealthos.strategy import parse
    return await parse(user_id, workspace_id, body.text)


@app.post("/strategy/{user_id}/{workspace_id}/run", dependencies=USER_WS)
def strategy_run(user_id: str, workspace_id: str, body: StrategyRunBody):
    """Test a rule on the workspace's past prices against buy-and-hold."""
    from wealthos.strategy import run
    return run(user_id, workspace_id, body.spec, body.generated_by)


@app.get("/strategy/{user_id}/runs", dependencies=USER)
def strategy_runs(user_id: str):
    """Earlier runs, newest first."""
    from wealthos.strategy import list_runs
    return list_runs(user_id)


@app.get("/strategy/{user_id}/runs/{run_id}", dependencies=USER)
def strategy_get_run(user_id: str, run_id: str):
    """One earlier run in full."""
    from wealthos.strategy import get_run
    return get_run(user_id, run_id)


@app.post("/tax/{user_id}/{workspace_id}", dependencies=USER_WS)
def tax_compare(user_id: str, workspace_id: str, body: TaxBody):
    """Tax under the old and new regime for a financial year, gains taken from the tradebook."""
    from wealthos.tax import compare
    return compare(user_id, workspace_id, body.fy, body.salary, body.other_income, body.deductions_80c,
                   body.deductions_80d, body.age)
