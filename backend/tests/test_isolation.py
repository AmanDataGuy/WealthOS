# tests/test_isolation.py
"""One user can never reach another user's data. Every route with a user id is checked with someone
else's token, and a route that forgets the check fails this test. No network, LLM or database."""
import inspect

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.data import engine

OPEN_ROUTES = {"/health", "/metrics", "/templates", "/auth/signup", "/auth/login", "/auth/guest"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DATA_ROOT", tmp_path / "users")
    monkeypatch.setattr(main, "JWT_SECRET", "test-secret-at-least-32-characters-long")
    return lambda user: TestClient(main.app, headers={"Authorization": f"Bearer {main._issue_token(user, user)}"})


# Every route depends on a token check (alone or with the workspace check), calls one itself, or is open on purpose.
def test_every_user_route_checks_the_token():
    unguarded = []
    for route in main.app.routes:
        if not hasattr(route, "endpoint") or route.path in OPEN_ROUTES or route.path.startswith(("/docs", "/redoc", "/openapi")):
            continue
        deps = {d.call for d in route.dependant.dependencies}
        checks = {main.verify_user_token, main.verify_user_workspace}
        if not deps & checks and not any(c.__name__ in inspect.getsource(route.endpoint) for c in checks):
            unguarded.append(route.path)
    assert unguarded == []


# User a, holding a valid token, is refused on every one of user b's routes; b's data stays b's.
def test_user_cannot_reach_another_users_data(client):
    a, b = client("a"), client("b")
    bank = b"Date,Narration,Debit,Credit\n05/04/2026,Rent,25000,\n06/04/2026,Salary,,90000\n"
    assert b.post("/sources/upload", data={"user_id": "b"}, files={"file": ("bank.csv", bank)}).status_code == 200
    board = b.post("/templates/b/default/spending/apply", json={}).json()["id"]

    gets = ["/workspaces/b", "/sources/b/datasets", "/semantic/b/default", "/dashboards/b",
            f"/dashboards/b/{board}", f"/dashboards/b/{board}/refresh",
            f"/dashboards/b/{board}/export.xlsx", "/strategy/b/runs", "/assistant/b/definitions", "/assistant/b/chats", "/research/b/default", "/prices/b/default", "/excel/b/default",
            "/templates/b/default/spending/suggest"]
    for path in gets:
        assert a.get(path).status_code == 403, path
    posts = {"/dashboards/b": {"title": "x"}, f"/dashboards/b/{board}/ops": {"ops": []},
             f"/dashboards/b/{board}/refresh": {}, f"/dashboards/b/{board}/revert": {},
             "/query/metric": {"user_id": "b", "metrics": ["total_spend"]}, "/query/sql": {"user_id": "b", "sql": "SELECT 1"},
             "/assistant/ask": {"user_id": "b", "question": "how much did I spend"},
             "/strategy/b/default/run": {"spec": {}}, "/prices/b/default": {"source": "nse", "symbols": ["X"]}, "/templates/b/default/spending/apply": {}}
    for path, body in posts.items():
        assert a.post(path, json=body).status_code == 403, path
    assert a.post("/sources/upload", data={"user_id": "b"}, files={"file": ("x.csv", bank)}).status_code == 403
    assert a.delete(f"/dashboards/b/{board}").status_code == 403
    assert TestClient(main.app).get("/workspaces/b").status_code == 401  # no token at all

    # a's own view is empty; b's data is untouched
    assert a.get("/sources/a/datasets").json() == [] and a.get("/dashboards/a").json() == []
    assert b.post("/query/metric", json={"user_id": "b", "metrics": ["total_spend"], "time": "fy"}).json()["rows"] == [{"total_spend": 25000.0}]
    assert a.post("/query/metric", json={"user_id": "a", "metrics": ["total_spend"], "time": "fy"}).status_code == 404  # no data
    assert b.get(f"/dashboards/b/{board}").status_code == 200


# "Load sample data" fills only the caller's own account: bank, trades and holdings, each with a raw file kept.
def test_sample_data_loads_for_own_user_only(client):
    a, b = client("a"), client("b")
    assert a.post("/sources/b/sample").status_code == 403
    r = b.post("/sources/b/sample")
    assert r.status_code == 200 and [t["tables"][0]["shape"] for t in r.json()] == ["transactions", "trades", "holdings"]
    assert {d["shape"] for d in b.get("/sources/b/datasets").json()} == {"transactions", "trades", "holdings"}
    assert not list((engine.user_dir("b") / "raw").glob("tmp*"))  # the temporary folder is gone
