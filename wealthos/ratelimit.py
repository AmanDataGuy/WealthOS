"""
# ratelimit.py - how many requests one caller may make

A token bucket per (caller, rule): `rate` requests per `per` seconds, refilled continuously. The API keys callers by
signed-in user when the token is valid, otherwise by IP.

| Rule | Paths | Limit |
|---|---|---|
| auth | `/auth/*` | 10 per minute per IP |
| ai | `/assistant/ask`, strategy parse, dashboard AI edit / report | 20 per minute |
| upload | `/sources/upload` | 30 per hour |
| default | everything else | 300 per minute |

ponytail: in-process buckets, so limits are per API process; move to a Postgres `rate_buckets` table
(`INSERT ... ON CONFLICT DO UPDATE ... RETURNING tokens`) when running more than one replica.
"""
import re
import threading
import time

RULES = [  # (name, path pattern, rate, per seconds); first match wins
    ("auth", re.compile(r"^/auth/"), 10, 60),
    ("ai", re.compile(r"^/assistant/ask$|^/strategy/[^/]+/[^/]+/parse$|^/dashboards/[^/]+/[^/]+/(?:ai-edit|report)$"), 20, 60),
    ("upload", re.compile(r"^/sources/upload$"), 30, 3600),
    ("default", re.compile(r""), 300, 60),
]
_MAX_KEYS = 10_000  # past this, buckets idle for an hour (so full again) are dropped

_buckets: dict[tuple[str, str], tuple[float, float]] = {}  # (caller, rule) -> (tokens left, last update)
_lock = threading.Lock()


# The rule that applies to a path: (name, rate, per).
def rule_for(path: str) -> tuple[str, int, int]:
    return next((name, rate, per) for name, pattern, rate, per in RULES if pattern.search(path))


# Take one request from the caller's bucket. Returns 0 if allowed, else the seconds until the next one is.
def hit(caller: str, path: str) -> float:
    name, rate, per = rule_for(path)
    now = time.monotonic()
    with _lock:
        if len(_buckets) > _MAX_KEYS:
            for key in [k for k, (_, last) in _buckets.items() if now - last > 3600]:
                del _buckets[key]
        tokens, last = _buckets.get((caller, name), (float(rate), now))
        tokens = min(float(rate), tokens + (now - last) * rate / per)
        allowed = tokens >= 1
        _buckets[(caller, name)] = (tokens - 1 if allowed else tokens, now)
    return 0.0 if allowed else (1 - tokens) * per / rate
