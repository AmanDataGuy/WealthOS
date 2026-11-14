# tests/test_settings.py
"""Sign-in fails closed: a weak, placeholder or missing secret stops the API from starting, the old public
demo passwords are locked out, and a failed login looks the same whether or not the user exists."""
import asyncio

import bcrypt
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.core import settings

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


# A fake users table behind main._users: answers the login SELECT and applies the two UPDATEs.
def fake_users_table(monkeypatch, rows):
    async def fake(query, name, *args):
        row = rows.get(name)
        if query.startswith("UPDATE") and row:
            row["failed_logins"], row["locked_until"] = (args[0], args[1]) if args else (0, None)
        return row
    monkeypatch.setattr(main, "_users", fake)


def _row(uid, name, password_hash):
    return {"id": uid, "username": name, "password_hash": password_hash, "failed_logins": 0, "locked_until": None}


# Unknown user, disabled user and wrong password all give the same 401, and each runs one bcrypt check.
def test_login_failures_look_the_same(monkeypatch):
    fake_users_table(monkeypatch, {"gone": _row("1", "gone", "!"), "real": _row("2", "real", main._hash("right-pw"))})
    calls = []
    real_checkpw = bcrypt.checkpw
    monkeypatch.setattr(main.bcrypt, "checkpw", lambda p, h: calls.append(h) or real_checkpw(p, h))
    monkeypatch.setattr(main, "JWT_SECRET", STRONG)
    client = TestClient(main.app)
    for name, pw in [("nobody", "x"), ("gone", "!"), ("real", "wrong")]:
        r = client.post("/auth/login", json={"username": name, "password": pw})
        assert (r.status_code, r.json()["detail"]) == (401, "wrong username or password")
    assert len(calls) == 3
    assert client.post("/auth/login", json={"username": "real", "password": "right-pw"}).status_code == 200


# 10 wrong passwords lock the account: the right password is refused (same 401) until the lock runs out.
def test_lockout_after_ten_failures(monkeypatch):
    rows = {"real": _row("2", "real", main._hash("right-pw"))}
    fake_users_table(monkeypatch, rows)
    monkeypatch.setattr(main, "JWT_SECRET", STRONG)
    monkeypatch.setattr(main.ratelimit, "hit", lambda *a: 0.0)  # 23 logins from one IP would hit the 10/min limit first
    client = TestClient(main.app)
    login = lambda pw: client.post("/auth/login", json={"username": "real", "password": pw})
    for _ in range(9):
        assert login("wrong").status_code == 401
    assert login("right-pw").status_code == 200 and rows["real"]["failed_logins"] == 0  # success resets the count
    for _ in range(11):
        assert login("wrong").status_code == 401
    r = login("right-pw")
    assert (r.status_code, r.json()["detail"]) == (401, "wrong username or password")
    rows["real"]["locked_until"] = main.datetime.now(main.timezone.utc) - main.timedelta(seconds=1)  # lock expired
    assert login("right-pw").status_code == 200


# A guest gets a signed-in account with the sample data already loaded, and nobody can sign in to it with a password.
def test_guest_account_has_sample_data_and_no_password(monkeypatch, tmp_path):
    from app.data import engine
    rows = {}
    async def fake(query, name, *args):
        if query.startswith("INSERT"):
            rows[name] = rows["7"] = _row("7", name, "!")  # the token check looks the user up by id
        return rows.get(name)
    monkeypatch.setattr(main, "_users", fake)
    monkeypatch.setattr(main, "JWT_SECRET", STRONG)
    monkeypatch.setattr(engine, "DATA_ROOT", tmp_path / "users")
    client = TestClient(main.app)
    r = client.post("/auth/guest").json()
    assert r["username"].startswith("guest-") and r["user_id"] == "7"
    datasets = client.get("/sources/7/datasets", headers={"Authorization": f"Bearer {r['token']}"}).json()
    assert {d["shape"] for d in datasets} == {"transactions", "trades", "holdings"}
    assert client.post("/auth/login", json={"username": r["username"], "password": "!"}).status_code == 401
