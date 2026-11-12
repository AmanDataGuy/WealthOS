"""
# account.py - sign-up, sign-in, the health check, and taking out or deleting an account

- Routes: `/auth/signup`, `/auth/login`, `/health`, `/account/{user_id}/export`, `DELETE /account/{user_id}`.
- The sign-in machinery (tokens, the users-table query, the pool) lives in `main.py`; it is reached through the
  `main` module so tests can swap `main._users` for a fake table.
"""
import asyncio
import csv
import io
import logging
from datetime import datetime, timedelta, timezone

import asyncpg
import bcrypt
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app import main
from app.main import USER

router = APIRouter()
logger = logging.getLogger("app.main")  # same logger name as before the split, so log lines read the same

MAX_FAILED_LOGINS, LOCK_MINUTES = 10, 15
_DUMMY_HASH = bcrypt.hashpw(b"no-such-user", bcrypt.gensalt()).decode()


# What sign-up and sign-in send.
class Credentials(BaseModel):
    username: str
    password: str


@router.post("/auth/signup")
async def signup(body: Credentials):
    """Create an account (username 3+ characters, password 6+) and sign in."""
    name = body.username.strip()
    if len(name) < 3 or len(body.password) < 6:
        raise HTTPException(400, "username needs 3+ characters and password 6+")
    try:
        row = await main._users("INSERT INTO users (username, password_hash) VALUES ($1, $2) RETURNING id::text, username",
                           name, main._hash(body.password))
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, "username already taken")
    return {"user_id": row["id"], "username": row["username"], "token": main._issue_token(row["id"], row["username"])}


@router.post("/auth/login")
async def login(body: Credentials):
    """Sign in with username and password. 10 wrong passwords lock the account for 15 minutes."""
    name = body.username.strip()
    row = await main._users("SELECT id::text, username, password_hash, failed_logins, locked_until "
                       "FROM users WHERE username = $1", name)
    now = datetime.now(timezone.utc)
    locked = bool(row and row["locked_until"] and row["locked_until"] > now)
    stored = row["password_hash"] if row and row["password_hash"].startswith("$2") else None  # '!' = disabled
    # Unknown, disabled and locked users still pay for one bcrypt check, so timing reveals nothing, and all
    # failures read the same.
    ok = bcrypt.checkpw(body.password.encode()[:72], (stored or _DUMMY_HASH).encode()) and bool(stored) and not locked
    if row and not ok and not locked:
        failed = row["failed_logins"] + 1
        until = now + timedelta(minutes=LOCK_MINUTES) if failed >= MAX_FAILED_LOGINS else None
        await main._users("UPDATE users SET failed_logins = $2, locked_until = $3 WHERE username = $1",
                     name, 0 if until else failed, until)
    if not ok:
        raise HTTPException(401, "wrong username or password")
    if row["failed_logins"]:
        await main._users("UPDATE users SET failed_logins = 0, locked_until = NULL WHERE username = $1", name)
    return {"user_id": row["id"], "username": row["username"], "token": main._issue_token(row["id"], row["username"])}


@router.get("/health")
def health():
    """Is the API up."""
    return {"status": "ok"}


# ── Account: take everything you stored with you, or delete it ─────────────────────────────────────────────

# Deleting an account: the username typed again, so it can't happen by accident.
class DeleteAccount(BaseModel):
    confirm: str


@router.get("/account/{user_id}/export", dependencies=USER)
async def account_export(user_id: str):
    """Everything you stored, plus your AI usage, as one zip (saved database passwords are left out)."""
    from app.core import account
    usage = []
    if main.DB_URL:
        usage = await (await main._pool()).fetch("SELECT created_at, provider, model, prompt_tokens, completion_tokens, "
                                            "estimated_cost_usd FROM llm_usage WHERE user_id = $1 ORDER BY created_at",
                                            user_id)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["created_at", "provider", "model", "prompt_tokens", "completion_tokens", "estimated_cost_usd"])
    writer.writerows([list(r.values()) for r in usage])
    data = await asyncio.to_thread(account.export_zip, user_id, {"ai_usage.csv": out.getvalue()})
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="wealthos-export.zip"'})


@router.delete("/account/{user_id}", dependencies=USER)
async def account_delete(user_id: str, body: DeleteAccount):
    """Delete your account and everything you stored; send your username as `confirm`. AI usage rows are kept
    without your id (server spend totals only)."""
    from app.core import account
    row = await main._users("SELECT username FROM users WHERE id::text = $1", user_id)
    if not row or row["username"] != body.confirm.strip():
        raise HTTPException(400, "type your username to confirm")
    files = await asyncio.to_thread(account.delete_files, user_id)
    async with (await main._pool()).acquire() as conn, conn.transaction():
        await conn.execute("UPDATE llm_usage SET user_id = NULL WHERE user_id = $1", user_id)
        await conn.execute("DELETE FROM users WHERE id::text = $1", user_id)
    main._USER_SEEN.pop(user_id, None)
    logger.info("account deleted (%d files)", files)
    return {"deleted_files": files, "at": datetime.now(timezone.utc).isoformat()}
