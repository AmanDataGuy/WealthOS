"""
# wealthos/settings.py - startup checks for the settings that decide whether auth is safe

The API and the MCP server read these at startup and refuse to run on a bad value, so a copied `.env.example`
or a forgotten variable can never quietly turn sign-in off.

- `WEALTHOS_ENV` is dev, test or production. **Unset means production**: forgetting it fails closed.
- `WEALTHOS_JWT_SECRET` must be at least 32 bytes and not a placeholder. The only way to run without one is
  `WEALTHOS_ENV=dev` plus `WEALTHOS_DEV_INSECURE_AUTH=1` (every route then trusts the user id in the path).

ponytail: plain functions over os.environ, no settings object; add one when a second module needs >3 values.
"""
import os

ENVS = {"dev", "test", "production"}
PLACEHOLDERS = {"...", "changeme", "secret", "your_secret", "change-me"}


# dev, test or production; unset is production, an unknown value is a startup error.
def env() -> str:
    value = os.getenv("WEALTHOS_ENV", "").strip().lower() or "production"
    if value not in ENVS:
        raise RuntimeError(f"WEALTHOS_ENV must be one of {sorted(ENVS)}, not {value!r}")
    return value


# True only for an explicit local opt-out of sign-in checks (never in test or production).
def insecure_dev_auth() -> bool:
    return env() == "dev" and os.getenv("WEALTHOS_DEV_INSECURE_AUTH") == "1"


# The token-signing secret, checked; "" only when insecure dev auth is switched on.
def jwt_secret() -> str:
    secret = os.getenv("WEALTHOS_JWT_SECRET", "").strip()
    if not secret and insecure_dev_auth():
        return ""
    if secret in PLACEHOLDERS or len(secret.encode()) < 32:
        raise RuntimeError("WEALTHOS_JWT_SECRET must be at least 32 bytes and not a placeholder. Generate one: "
                           "python -c \"import secrets; print(secrets.token_hex(32))\"")
    return secret


# How long a sign-in token lasts (default 7 days).
def token_days() -> int:
    return int(os.getenv("WEALTHOS_TOKEN_DAYS", "7"))


# Everything the API needs before it accepts a request; raises RuntimeError naming the first problem.
def check_api() -> str:
    secret = jwt_secret()
    if env() == "production" and not os.getenv("WEALTHOS_DB_URL"):
        raise RuntimeError("WEALTHOS_DB_URL is required in production (sign-in lives in Postgres)")
    return secret
