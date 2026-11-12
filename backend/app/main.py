"""
# main.py - the web API: the app object, middleware, startup, error mapping and the sign-in checks

A thin layer: each route checks who is asking, then calls one plain-Python function that does the work. The routes
live in `app/api/` (one router per area) and are included at the end of this file.

- **Who is asking:** every route with a `{user_id}` in it needs that user's sign-in token; routes with a
  `{workspace_id}` also check that the workspace exists.
- **Errors are mapped once** (bottom of the setup section), so routes stay one or two lines: not found -> 404,
  bad input -> 400, wrong feed token -> 401, too slow -> 408, the day's AI budget used up -> 429,
  LibreOffice / a price site down -> 503 / 502. Server paths are scrubbed from messages; any other error is a
  500 that shows only a request id (also sent as X-Request-ID and stamped on every log line).
- **Rate limits** per signed-in user (or IP before sign-in), see `core/ratelimit.py`; over the limit -> 429.
- Plain `def` routes run in a background thread (FastAPI does that), so slow work never blocks other requests.

Run: `uvicorn app.main:app --port 8000`. Every route is listed at http://localhost:8000/docs.
"""
import contextvars
import logging
import math
import os
import re
import secrets
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg
import bcrypt
import httpx
import jwt
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.data import engine
from app.core import ratelimit, settings
from app.ai.llm_client import CostCapReached

for _stream in (sys.stdout, sys.stderr):  # a ₹ in a log line must never crash a Windows console
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
load_dotenv()
REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


# Stamp each log record with the id of the request it belongs to ("-" outside a request).
class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = REQUEST_ID.get()
        return True


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s [%(request_id)s] %(message)s")
for _handler in logging.getLogger().handlers:
    _handler.addFilter(_RequestIdFilter())
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


POOL: Optional[asyncpg.Pool] = None  # one shared set of Postgres connections, opened at startup


# The shared connection pool; opened here if startup could not reach the database. 503 without a database.
async def _pool() -> asyncpg.Pool:
    global POOL
    if not DB_URL:
        raise HTTPException(503, "sign-in needs the database (WEALTHOS_DB_URL)")
    if POOL is None:  # shortcut: two first requests at once may each open a pool (one leaks); fine for one process
        POOL = await asyncpg.create_pool(DB_URL, min_size=1, max_size=10)
    return POOL


# Bring the Postgres schema up to date (backend/migrations/, core/migrate.py) and secure the accounts in it.
async def _prepare_database() -> None:
    if not DB_URL:
        return
    from app.core.migrate import migrate
    try:
        async with (await _pool()).acquire() as conn:
            if applied := await migrate(conn):
                logger.info("database migrations applied: %s", ", ".join(applied))
            await _secure_accounts(conn)
    except Exception as e:
        logger.warning("database not ready, sign-in will fail until it is: %s", e)


# Refuse to start on a weak or missing secret (settings.py), then set up the database.
@asynccontextmanager
async def lifespan(app: FastAPI):
    global JWT_SECRET, POOL
    JWT_SECRET = settings.check_api()
    if not JWT_SECRET:
        logger.warning("WEALTHOS_DEV_INSECURE_AUTH=1: sign-in checks are OFF (local development only)")
    await _prepare_database()
    yield
    if POOL:
        await POOL.close()
        POOL = None


app = FastAPI(title="WealthOS API", description="Your data in, trusted numbers and dashboards out", lifespan=lifespan)

_REQUEST_ID_OK = re.compile(r"^[A-Za-z0-9-]{8,64}$")
# Proxies (Caddy) whose X-Forwarded-For is believed; from anyone else the header is ignored.
_TRUSTED_PROXIES = {p.strip() for p in os.getenv("WEALTHOS_TRUSTED_PROXIES", "").split(",") if p.strip()}


# Who is calling, for rate limits: the signed-in user when the token checks out, otherwise the client IP.
def _caller(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if JWT_SECRET and auth.startswith("Bearer ") and not request.url.path.startswith("/auth/"):
        try:
            return "user:" + str(jwt.decode(auth[7:].strip(), JWT_SECRET, algorithms=["HS256"])["sub"])
        except (jwt.InvalidTokenError, KeyError):
            pass
    ip = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for", "")
    if ip in _TRUSTED_PROXIES and forwarded:
        ip = forwarded.split(",")[-1].strip()  # the proxy appends the address it saw
    return "ip:" + ip


# Per-caller request limits (core/ratelimit.py); over the limit -> 429 with Retry-After. /health is never limited.
@app.middleware("http")
async def rate_limit(request: Request, call_next):
    if request.url.path != "/health" and request.method != "OPTIONS":
        wait = ratelimit.hit(_caller(request), request.url.path)
        if wait:
            return JSONResponse(status_code=429, headers={"Retry-After": str(math.ceil(wait))},
                                content={"detail": f"too many requests, try again in {math.ceil(wait)} s"})
    return await call_next(request)


# Give every request an id (the caller's X-Request-ID if it looks sane), put it on log lines and the response,
# and turn any error nobody mapped (a bug: KeyError, IndexError ...) into a 500 that names only the id.
@app.middleware("http")
async def request_id(request: Request, call_next):
    rid = request.headers.get("x-request-id", "")
    rid = rid if _REQUEST_ID_OK.match(rid) else uuid.uuid4().hex
    REQUEST_ID.set(rid)
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("unhandled error")
        response = JSONResponse(status_code=500, content={"detail": f"the request could not be completed (ref {rid})"})
    response.headers["X-Request-ID"] = rid
    return response


app.add_middleware(CORSMiddleware, allow_origins=os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(","),
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# Errors the modules raise on purpose, with messages written for the user. A plain LookupError (KeyError ...) is
# not here: only engine.NotFound means "not found"; the rest are bugs and get the generic 500 above.
ERRORS = [(engine.NotFound, 404), (ValueError, 400), (ArithmeticError, 400), (PermissionError, 401),
          (TimeoutError, 408), (CostCapReached, 429), (RuntimeError, 503), (subprocess.TimeoutExpired, 504),
          (httpx.HTTPError, 502)]
GENERIC = {502: "a data source this needs did not answer: try again later",
           504: "the job took too long and was stopped"}
# Server file paths (D:\..., /app/data/..., data/users/...) that library messages (DuckDB, LibreOffice) carry.
_PATHS = re.compile(r"[A-Za-z]:[\\/]\S*|(?<![\w.])/(?:[\w.\-]+/)+[\w.\-]*|data[\\/]users\S*")


# Turn an error raised by the modules into an HTTP answer with a readable message (server paths removed).
def _error_handler(status: int):
    # Answer with the error's status code and its message; 5xx are logged with the request id.
    async def handle(_request: Request, exc: Exception):
        if isinstance(exc, ArithmeticError):
            detail = "that number is too large (or divides by zero) to calculate"
        else:
            detail = GENERIC.get(status) or _PATHS.sub("<path>", str(exc))
        if status >= 500:
            logger.warning("%s: %s", type(exc).__name__, exc)
            detail = f"{detail} (ref {REQUEST_ID.get()})"
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


_USER_SEEN: dict[str, float] = {}  # user id -> when the users table last confirmed it (monotonic seconds)


# Does the user still exist? A deleted account's tokens stop working within a minute (the answer is cached that long).
async def _user_exists(user_id: str) -> bool:
    if not DB_URL or time.monotonic() - _USER_SEEN.get(user_id, -1e9) < 60:
        return True
    try:
        row = await _users("SELECT 1 FROM users WHERE id::text = $1", user_id)
    except Exception as e:  # the token's signature is already checked; a database blip must not sign everyone out
        logger.warning("user lookup unavailable: %s", e)
        return True
    if row:
        _USER_SEEN[user_id] = time.monotonic()
    return bool(row)


# Check the Bearer token belongs to `user_id` (and the account still exists). No secret = refuse, unless insecure
# dev auth was switched on.
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
    if not await _user_exists(user_id):
        raise HTTPException(401, "this account no longer exists")
    return user_id


# 404 unless the workspace exists for this user.
def require_workspace(user_id: str, workspace_id: str) -> None:
    from app.core.workspaces import get_workspace
    if not get_workspace(user_id, workspace_id):
        raise HTTPException(404, "workspace not found")


# Check the token, then that the workspace exists (for routes with both in the path).
async def verify_user_workspace(user_id: str, workspace_id: str, authorization: Optional[str] = Header(None)) -> None:
    await verify_user_token(user_id, authorization)
    require_workspace(user_id, workspace_id)


USER = [Depends(verify_user_token)]
USER_WS = [Depends(verify_user_workspace)]


# Run one query against the users table.
async def _users(query: str, *args):
    return await (await _pool()).fetchrow(query, *args)


# ── Routes: one router per area (app/api/); imported last because they use the sign-in checks above ─────────

from app.api import account, assistant, company, dashboards, sources, strategy  # noqa: E402

for _router in (account, sources, assistant, dashboards, strategy, company):
    app.include_router(_router.router)
