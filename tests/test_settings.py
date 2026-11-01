# tests/test_settings.py
"""Sign-in fails closed: a weak, placeholder or missing secret stops the API from starting, the old public
demo passwords are locked out, and a failed login looks the same whether or not the user exists."""
import asyncio

import bcrypt
import pytest
from fastapi.testclient import TestClient

import api.main as main
from wealthos import settings

STRONG = "x" * 32


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("WEALTHOS_ENV", "WEALTHOS_JWT_SECRET", "WEALTHOS_DEV_INSECURE_AUTH", "WEALTHOS_DEMO_SEED"):
        monkeypatch.delenv(k, raising=False)


@pytest.mark.parametrize("secret", ["...", "changeme", "x" * 31, ""])
def test_weak_or_missing_secret_raises(monkeypatch, secret):
    monkeypatch.setenv("WEALTHOS_JWT_SECRET", secret)
    with pytest.raises(RuntimeError):
        settings.jwt_secret()


def test_unset_env_is_production_and_unknown_env_raises(monkeypatch):
    assert settings.env() == "production"
    monkeypatch.setenv("WEALTHOS_ENV", "staging")
    with pytest.raises(RuntimeError):
        settings.env()


def test_insecure_auth_only_in_dev(monkeypatch):
    monkeypatch.setenv("WEALTHOS_DEV_INSECURE_AUTH", "1")
    monkeypatch.setenv("WEALTHOS_ENV", "dev")
    assert settings.jwt_secret() == ""
    for env in ("production", "test"):
        monkeypatch.setenv("WEALTHOS_ENV", env)
        with pytest.raises(RuntimeError):
            settings.jwt_secret()
    monkeypatch.setenv("WEALTHOS_JWT_SECRET", STRONG)
    assert settings.jwt_secret() == STRONG


# The app with no secret, outside dev, refuses to start; a route reached without startup refuses too.
def test_no_fail_open_without_secret(monkeypatch):
    with pytest.raises(RuntimeError):
        with TestClient(main.app):
            pass
    monkeypatch.setattr(main, "JWT_SECRET", "")
    assert TestClient(main.app).get("/workspaces/someone").status_code == 503


# A fake asyncpg connection holding the users table as a dict.
class FakeConn:
    def __init__(self, users):
        self.users = users

    async def fetchrow(self, _q, name):
        return {"password_hash": self.users[name]} if name in self.users else None

    async def execute(self, q, *args):
        if q.startswith("UPDATE"):
            self.users[args[0]] = "!"
        else:
            self.users[args[0]] = args[1]


def test_old_default_accounts_disabled_and_no_seed_by_default():
    conn = FakeConn({"admin": main._hash("wealthos123"), "demo": main._hash("changed-by-owner")})
    asyncio.run(main._secure_accounts(conn))
    assert conn.users["admin"] == "!"  # public password -> locked
    assert conn.users["demo"] != "!"   # owner had changed it -> untouched
    assert set(conn.users) == {"admin", "demo"}  # no seed without WEALTHOS_DEMO_SEED


def test_demo_seed_is_random(monkeypatch, capsys):
    monkeypatch.setenv("WEALTHOS_DEMO_SEED", "1")
    conn = FakeConn({})
    asyncio.run(main._secure_accounts(conn))
    (name,) = conn.users
    assert name.startswith("demo-") and name != "demo" and "wealthos123" not in capsys.readouterr().out


# Unknown user, disabled user and wrong password all give the same 401, and each runs one bcrypt check.
def test_login_failures_look_the_same(monkeypatch):
    rows = {"gone": {"id": "1", "username": "gone", "password_hash": "!"},
            "real": {"id": "2", "username": "real", "password_hash": main._hash("right-password")}}

    async def fake_users(_q, name):
        return rows.get(name)

    calls = []
    real_checkpw = bcrypt.checkpw
    monkeypatch.setattr(main, "_users", fake_users)
    monkeypatch.setattr(main.bcrypt, "checkpw", lambda p, h: calls.append(h) or real_checkpw(p, h))
    monkeypatch.setattr(main, "JWT_SECRET", STRONG)
    client = TestClient(main.app)
    for name, pw in [("nobody", "x"), ("gone", "!"), ("real", "wrong")]:
        r = client.post("/auth/login", json={"username": name, "password": pw})
        assert (r.status_code, r.json()["detail"]) == (401, "wrong username or password")
    assert len(calls) == 3
    assert client.post("/auth/login", json={"username": "real", "password": "right-password"}).status_code == 200
