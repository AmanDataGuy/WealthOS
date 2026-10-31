"""
# feeds.py - live data: rows pushed to WealthOS, or pulled from a URL on a schedule

A feed fills one table of a workspace (e.g. `orders`, or `prices` so the strategy lab sees live bars).

| Kind | How rows arrive |
|---|---|
| `push` | Your system POSTs JSON rows to `/feeds/{user}/{workspace}/{feed}/push` with the feed's token (header `X-Feed-Token`). The token is shown once and stored only as a hash. |
| `pull` | The worker (`python -m wealthos.worker`) fetches an `https` URL every `interval_s` seconds; the URL returns JSON rows (a list, or `{"rows": [...]}`) or CSV. |

How rows are stored (nothing is lost if the server stops):
- Every batch is written at once as a small Parquet "part" file next to the table's other files, and listed in the
  dataset's description (`files`), so the engine reads all parts as one table.
- After `COMPACT_AFTER` parts, they are merged into one file automatically. The data fingerprint changes with every batch.

Safety:
- At most 5,000 rows per batch, plain values only (text, numbers, true/false, empty); column names are cleaned.
- A feed into a built-in table (`transactions`, `trades`, `holdings`, `prices`) must carry that table's must-have columns.
- Pull URLs must be `https` and must not point at this machine or a private network (checked when the feed is created
  and before every fetch); redirects are not followed.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import ipaddress
import json
import re
import secrets
import socket
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import duckdb
import httpx
import pandas as pd

from wealthos import engine
from wealthos.ingest.tabular import SHAPES

MAX_ROWS_PER_BATCH = 5000
MAX_PULL_BYTES = 5 * 1024 * 1024
COMPACT_AFTER = 20  # part files before they are merged into one
_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)  # ponytail: per-process; one API process + one worker is fine


# The current UTC time as text.
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Feed records ────────────────────────────────────────────────────────────────────────

# Folder holding a workspace's feed records.
def _dir(user_id: str, workspace_id: str) -> Path:
    return engine.user_dir(user_id) / "workspaces" / engine.safe_id(workspace_id) / "feeds"


# Read one feed record, or fail if it does not exist.
def _load(user_id: str, workspace_id: str, feed_id: str) -> dict:
    path = _dir(user_id, workspace_id) / f"{engine.safe_id(feed_id)}.json"
    if not path.exists():
        raise LookupError("feed not found")
    return json.loads(path.read_text(encoding="utf-8"))


# Write one feed record to disk.
def _save(user_id: str, workspace_id: str, feed: dict) -> None:
    _dir(user_id, workspace_id).mkdir(parents=True, exist_ok=True)
    (_dir(user_id, workspace_id) / f"{feed['id']}.json").write_text(json.dumps(feed), encoding="utf-8")


# A feed as shown to the user (never the token hash).
def _public(feed: dict) -> dict:
    return {k: v for k, v in feed.items() if k != "token_hash"}


# The workspace's feeds.
def list_feeds(user_id: str, workspace_id: str) -> list[dict]:
    return [_public(json.loads(p.read_text(encoding="utf-8"))) for p in sorted(_dir(user_id, workspace_id).glob("*.json"))]


# Create a feed into table `name`. A push feed's token comes back once, in "token".
def create_feed(user_id: str, workspace_id: str, name: str, kind: str = "push", url: str | None = None,
                interval_s: int = 300) -> dict:
    table = re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_")[:60]
    if not table or table[0].isdigit():
        raise ValueError("the table name needs letters (e.g. orders, prices)")
    if kind not in ("push", "pull"):
        raise ValueError("kind must be push or pull")
    if kind == "pull":
        check_url(url or "")
    token = secrets.token_urlsafe(24)
    feed = {"id": uuid.uuid4().hex[:12], "name": table, "kind": kind, "url": url if kind == "pull" else None,
            "interval_s": max(30, min(int(interval_s), 86400)), "token_hash": hashlib.sha256(token.encode()).hexdigest(),
            "rows": 0, "last_push_at": None, "last_pull_at": None, "last_error": None, "created_at": _now()}
    _save(user_id, workspace_id, feed)
    return _public(feed) | ({"token": token} if kind == "push" else {})


# Delete a feed and the table rows it brought in.
def delete_feed(user_id: str, workspace_id: str, feed_id: str) -> None:
    feed = _load(user_id, workspace_id, feed_id)
    meta = _meta(user_id, feed)
    if meta:
        engine.remove_dataset(user_id, meta)
    (_dir(user_id, workspace_id) / f"{feed['id']}.json").unlink()


# The feed record if `token` is its push token, else PermissionError (compared in constant time).
def check_token(user_id: str, workspace_id: str, feed_id: str, token: str | None) -> dict:
    feed = _load(user_id, workspace_id, feed_id)
    if feed["kind"] != "push" or not hmac.compare_digest(hashlib.sha256((token or "").encode()).hexdigest(), feed["token_hash"]):
        raise PermissionError("wrong feed token")
    return feed


# ── Rows → Parquet parts ────────────────────────────────────────────────────────────────

# File describing the table a feed fills.
def _meta_path(user_id: str, feed: dict) -> Path:
    return engine.user_dir(user_id) / "datasets" / feed["name"] / f"feed_{feed['id']}.json"


# The feed's table description, or nothing if no rows arrived yet.
def _meta(user_id: str, feed: dict) -> dict | None:
    path = _meta_path(user_id, feed)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# Check a batch and turn it into a table: list of objects, plain values, clean column names, the table's must-haves.
def _frame(rows, table: str) -> pd.DataFrame:
    if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
        raise ValueError('send a non-empty list of rows, each an object like {"ts": "2026-10-30", "close": 101.5}')
    if len(rows) > MAX_ROWS_PER_BATCH:
        raise ValueError(f"at most {MAX_ROWS_PER_BATCH:,} rows per batch")
    clean = []
    for r in rows:
        row = {}
        for k, v in r.items():
            if v is not None and not isinstance(v, (str, int, float, bool)):
                raise ValueError(f"column '{k}' holds a {type(v).__name__}; only text, numbers, true/false or null")
            row[re.sub(r"[^a-z0-9_]+", "_", str(k).strip().lower()).strip("_") or "column"] = v
        clean.append(row)
    df = pd.DataFrame(clean)
    if table in SHAPES:
        missing = [c for c in SHAPES[table]["required"] if c not in df]
        if missing:
            raise ValueError(f"rows for the built-in '{table}' table need column(s) {missing}")
    date_col = engine.DATE_COLUMNS.get(table)
    if date_col in df:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce").dt.date
    return df


# Add rows to a feed's table (written to disk before this returns). Returns the counts.
def append_rows(user_id: str, workspace_id: str, feed_id: str, rows) -> dict:
    feed = _load(user_id, workspace_id, feed_id)
    df = _frame(rows, feed["name"])
    folder = engine.user_dir(user_id) / "datasets" / feed["name"]
    folder.mkdir(parents=True, exist_ok=True)
    with _locks[feed["id"]]:
        part = f"feed_{feed['id']}.part-{time.time_ns()}-{secrets.token_hex(2)}.parquet"
        df.to_parquet(folder / part, index=False)
        meta = _meta(user_id, feed) or {
            "dataset_id": f"feed_{feed['id']}", "workspace_id": engine.safe_id(workspace_id), "shape": feed["name"],
            "feed_id": feed["id"], "files": [], "rows": 0, "columns": [], "warnings": [], "created_at": _now(),
            "source": {"file": f"feed:{feed['name']}", "content_hash": feed["id"], "page": None, "sheet": None}}
        meta["files"].append(part)
        meta["rows"] += len(df)
        meta["columns"] = list(dict.fromkeys(meta["columns"] + list(df.columns)))
        _meta_path(user_id, feed).write_text(json.dumps(meta, default=str), encoding="utf-8")
        _save(user_id, workspace_id, _load(user_id, workspace_id, feed_id) | {"rows": meta["rows"], "last_push_at": _now()})
        if len(meta["files"]) > COMPACT_AFTER:
            _compact_locked(user_id, feed)
    return {"rows_added": len(df), "total_rows": meta["rows"]}


# Merge a feed's part files into one file (called while the feed's lock is held). Returns how many were merged.
# ponytail: a query that listed the old parts just before they are deleted fails once; asking again works.
def _compact_locked(user_id: str, feed: dict) -> int:
    meta = _meta(user_id, feed)
    if not meta or len(meta["files"]) < 2:
        return 0
    folder = engine.user_dir(user_id) / "datasets" / feed["name"]
    old = meta["files"]
    merged = f"feed_{feed['id']}.c{time.time_ns()}.parquet"
    listing = ", ".join("'" + (folder / f).resolve().as_posix().replace("'", "''") + "'" for f in old)
    target = (folder / merged).resolve().as_posix().replace("'", "''")
    con = duckdb.connect(":memory:")
    try:
        con.execute(f"COPY (SELECT * FROM read_parquet([{listing}], union_by_name = true)) TO '{target}' (FORMAT parquet)")
    finally:
        con.close()
    meta["files"] = [merged]
    _meta_path(user_id, feed).write_text(json.dumps(meta, default=str), encoding="utf-8")
    for f in old:
        (folder / f).unlink(missing_ok=True)
    return len(old)


# ── Pull feeds ──────────────────────────────────────────────────────────────────────────

# Refuse URLs that could reach this machine or a private network (the server fetches on the user's behalf).
def check_url(url: str) -> None:
    p = urlsplit(url.strip())
    if p.scheme != "https" or not p.hostname:
        raise ValueError("a pull feed needs an https:// URL")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(p.hostname, p.port or 443)}
    except socket.gaierror:
        raise ValueError(f"can't find the host {p.hostname}") from None
    for a in addresses:
        if not ipaddress.ip_address(a.split("%")[0]).is_global:
            raise ValueError("that URL points at a private or local address, which feeds may not fetch")
    # ponytail: DNS could change between this check and the fetch; pin the resolved IP if this ever serves strangers


# Turn a pulled response into rows: JSON (a list, or {"rows": [...]}) or CSV (all-numeric columns become numbers).
def _rows_from(body: bytes, content_type: str) -> list[dict]:
    text = body.decode("utf-8-sig", errors="replace").strip()
    if "json" in content_type or text[:1] in "[{":
        data = json.loads(text)
        return data.get("rows", []) if isinstance(data, dict) else data
    df = pd.DataFrame(list(csv.DictReader(io.StringIO(text))))
    for col in df:
        numbers = pd.to_numeric(df[col], errors="coerce")
        if numbers.notna().all():
            df[col] = numbers
    return df.astype(object).where(df.notna(), None).to_dict("records")


# Fetch a pull feed now and append what it returned. `client` is only passed in tests.
def pull(user_id: str, workspace_id: str, feed_id: str, client: httpx.Client | None = None) -> dict:
    feed = _load(user_id, workspace_id, feed_id)
    if feed["kind"] != "pull":
        raise ValueError("only pull feeds can be fetched")
    pulled_at = _now()
    try:
        check_url(feed["url"])
        owns = client is None
        client = client or httpx.Client(timeout=20, follow_redirects=False)
        try:
            with client.stream("GET", feed["url"]) as r:
                if r.status_code != 200:
                    raise ValueError(f"the URL answered with status {r.status_code}")
                body = b""
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > MAX_PULL_BYTES:
                        raise ValueError(f"the response is larger than {MAX_PULL_BYTES // (1024 * 1024)} MB")
                content_type = r.headers.get("content-type", "")
        finally:
            if owns:
                client.close()
        result = append_rows(user_id, workspace_id, feed_id, _rows_from(body, content_type))
    except (ValueError, httpx.HTTPError, json.JSONDecodeError) as e:
        error = f"{type(e).__name__}: {e}"[:300]
        _save(user_id, workspace_id, _load(user_id, workspace_id, feed_id) | {"last_pull_at": pulled_at, "last_error": error})
        raise ValueError(f"pull failed: {error}") from None
    _save(user_id, workspace_id, _load(user_id, workspace_id, feed_id) | {"last_pull_at": pulled_at, "last_error": None})
    return result


# Every pull feed whose interval has passed: (user_id, workspace_id, feed_id).
def due_pulls() -> list[tuple[str, str, str]]:
    due, now = [], datetime.now(timezone.utc)
    for path in engine.DATA_ROOT.glob("*/workspaces/*/feeds/*.json"):
        feed = json.loads(path.read_text(encoding="utf-8"))
        last = datetime.fromisoformat(feed["last_pull_at"]) if feed.get("last_pull_at") else None
        if feed["kind"] == "pull" and (last is None or (now - last).total_seconds() >= feed["interval_s"]):
            due.append((path.parents[3].name, path.parents[1].name, feed["id"]))
    return due
