# tests/test_stress.py
"""Hostile and broken input on every kind of route: the API must answer with a clear 4xx message, never crash
(500) and never touch another user's or another folder's files. No network: the AI is faked to say nothing."""
import io

import openpyxl
import pytest
from fastapi.testclient import TestClient

import api.main as main
from wealthos import engine, tools
from wealthos.services import llm_client as llm

BANK = b"Date,Narration,Debit,Credit\n05/04/2026,Rent,25000,\n06/04/2026,Salary,,90000\n"


@pytest.fixture()
def c(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DATA_ROOT", tmp_path / "users")
    monkeypatch.setattr(main, "JWT_SECRET", "a-test-secret-that-is-at-least-32-characters")

    async def silent(*a, **kw):  # the AI never answers: every feature must still fail politely
        return ""

    async def silent_json(*a, **kw):
        return None
    monkeypatch.setattr(llm, "call_llm", silent)
    monkeypatch.setattr(llm, "call_json", silent_json)
    client = TestClient(main.app, raise_server_exceptions=False,
                        headers={"Authorization": f"Bearer {main._issue_token('u', 'u')}"})
    assert client.post("/sources/upload", data={"user_id": "u"}, files={"file": ("bank.csv", BANK)}).status_code == 200
    return client


# Assert an answer is a handled refusal (4xx with a message), not a crash.
def refused(r, *codes):
    assert r.status_code < 500, (r.status_code, r.text[:300])
    assert not codes or r.status_code in codes, (r.status_code, r.text[:300])
    return r


def test_paths_cannot_escape(c, tmp_path):
    for path in ["/dashboards/u/..%2F..%2Fsecrets", "/assistant/u/chats/..%2F..%2Fx", "/strategy/u/runs/..%2F..%2Fx",
                 "/sources/u/..%2F..%2F/tables/0/mapping", "/excel/u/default/..%2F..%2Fx/filled.xlsx",
                 "/workspaces/u/..%2F..%2Fdefault", "/prices/u/..%2F"]:
        refused(c.get(path))
    refused(c.delete("/research/u/default/..%2F..%2Fanything"))
    assert not (tmp_path / "secrets").exists()


def test_bad_uploads(c):
    for name, data in [("empty.csv", b""), ("x.exe", b"MZ"), ("bad.xlsx", b"not a zip"), ("bad.pdf", b"%PDF-1.4 garbage"),
                       ("latin1.csv", "Date,Narration,Debit\n05/04/2026,Caf\xe9,10\n".encode("latin-1")),
                       ("wide.csv", (",".join(f"c{i}" for i in range(500)) + "\n" + ",".join("1" * 500)).encode()),
                       ("header_only.csv", b"Date,Narration,Debit,Credit\n")]:
        refused(c.post("/sources/upload", data={"user_id": "u"}, files={"file": (name, data)}))
    refused(c.post("/sources/upload", data={"user_id": "u", "workspace_id": "nope"}, files={"file": ("a.csv", BANK)}), 404)
    refused(c.post("/sources/sheet", json={"user_id": "u", "url": "http://169.254.169.254/latest"}), 400)
    refused(c.post("/sources/sheet", json={"user_id": "u", "url": "https://evil.example.com/spreadsheets/d/x"}), 400)


def test_bad_queries(c):
    q = lambda **kw: c.post("/query/metric", json={"user_id": "u", "metrics": ["total_spend"]} | kw)
    refused(q(metrics=["nope"]), 400)
    refused(q(time="the year of the dragon"), 400)
    refused(q(dimensions=["password"]), 400)
    refused(q(filters=[{"column": "amount", "op": "; DROP TABLE x", "value": 1}]), 400)
    refused(q(filters=[{"column": "amount", "op": ">", "value": "lots"}]), 400)
    refused(q(metrics=["total_spend", "portfolio_value"]), 400)  # two different tables
    for sql in ["DROP TABLE transactions", "SELECT * FROM read_csv('/etc/passwd')", "SELECT 1; SELECT 2",
                "SELECT * FROM transactions WHERE 1=1 UNION SELECT * FROM duckdb_settings()", "", "SELECT"]:
        refused(c.post("/query/sql", json={"user_id": "u", "sql": sql}), 400)


def test_calculator_bombs():
    for expr in ["9**9**9", "(10**100)**100", "__import__('os')", "1/0", "x", "[1]*10**9", "round(1, 2, 3)"]:
        with pytest.raises((ValueError, ZeroDivisionError, OverflowError, TypeError)):
            tools.calc(expr)


def test_bad_dashboard_and_feed_input(c):
    d = c.post("/dashboards/u", json={"title": "T"}).json()
    refused(c.post(f"/dashboards/u/{d['id']}/ops", json={"ops": [{"op": "explode"}]}), 400)
    refused(c.post(f"/dashboards/u/{d['id']}/ops", json={"ops": [{"op": "add_tile", "value": {"kind": "kpi"}}]}), 400)
    refused(c.post(f"/dashboards/u/{d['id']}/ops", json={"ops": [], "base_version": 999}), 400)  # a stale screen
    refused(c.post(f"/dashboards/u/{d['id']}/revert", json={"version": 42}), 404)
    refused(c.post("/dashboards/u", json={"title": "x", "workspace_id": "nope"}), 404)
    refused(c.post(f"/dashboards/u/{d['id']}/ai-edit", json={"instruction": "make it nice"}))  # AI says nothing
    refused(c.post("/feeds/u/default", json={"name": "x", "kind": "pull", "url": "http://127.0.0.1:8000/secret"}), 400)
    refused(c.post("/feeds/u/default", json={"name": "x", "kind": "teleport"}), 422)
    feed = c.post("/feeds/u/default", json={"name": "orders"}).json()
    push = lambda body, token=feed["token"]: TestClient(main.app, raise_server_exceptions=False).post(
        f"/feeds/u/default/{feed['id']}/push", json=body, headers={"X-Feed-Token": token})
    refused(push([{"a": 1}], "guess"), 401)
    for body in [[], "rows", {"rows": [{"a": {"b": 1}}]}, [{"a": 1}] * 5001, [1, 2, 3]]:
        refused(push(body), 400)
    assert push([{"amount": 5, "city": "Pune"}]).json()["rows_added"] == 1


def test_bad_other_features(c):
    refused(c.post("/tax/u/default", json={"fy": "1999-00"}), 400)
    refused(c.post("/tax/u/default", json={"age": "ancient"}), 422)
    assert c.post("/tax/u/default", json={"salary": -5}).json()["new"]["total_tax"] == 0
    refused(c.post("/prices/u/default", json={"source": "crypto", "symbols": ["BTC"]}), 422)
    refused(c.post("/prices/u/default", json={"source": "nse", "symbols": []}), 400)
    refused(c.post("/prices/u/default", json={"source": "nse", "symbols": [f"S{i}" for i in range(51)]}), 400)
    refused(c.post("/strategy/u/default/run", json={"spec": {"universe": ["X"], "entry": "always"}}), 400)
    refused(c.post("/strategy/u/default/parse", json={"text": "buy low sell high"}))  # AI says nothing
    refused(c.delete("/workspaces/u/default"), 400)  # My files can't be deleted
    refused(c.patch("/workspaces/u/default", json={"name": "   "}))
    refused(c.post("/workspaces/u", json={"name": ""}), 400)
    wb = openpyxl.Workbook()
    buf = io.BytesIO()
    wb.save(buf)
    refused(c.post("/excel/u/default", data={"data_table": "transactions"},
                   files={"file": ("t.xlsx", buf.getvalue())}), 400)  # no Data sheet, no named cells
    refused(c.post("/excel/u/default", data={"data_table": "transactions"}, files={"file": ("t.xlsm", b"x")}), 400)
    answer = c.post("/assistant/ask", json={"user_id": "u", "question": "how much did I spend?"})
    assert answer.status_code == 200 and "unavailable" in answer.json()["text"]  # the AI is down: said plainly
    refused(c.post("/assistant/ask", json={"user_id": "u", "question": "hi", "chat_id": "no-such-chat"}), 404)


# Errors never leak server paths or internals: mapped errors are scrubbed, unmapped ones (a bug) become a generic
# 500 naming only the request id, and that id comes back in X-Request-ID.
@pytest.mark.parametrize("exc, status", [
    (ValueError('IO Error: No files found that match "/app/data/users/u/ws/x.parquet"'), 400),
    (ValueError(r"could not open D:\projects\WealthOS\data\users\u\x.parquet"), 400),
    (RuntimeError("LibreOffice failed: /tmp/lo_profile/x.xlsx"), 503),
    (KeyError("internal_column_name"), 500),
    (IndexError("list index out of range"), 500),
    (OverflowError("(34, 'Result too large')"), 400),
])
def test_errors_do_not_leak(c, monkeypatch, exc, status):
    from wealthos import workspaces

    def boom(*_a, **_kw):
        raise exc
    monkeypatch.setattr(workspaces, "list_workspaces", boom)
    r = c.get("/workspaces/u", headers={"X-Request-ID": "test-req-12345"})
    body = r.json()["detail"]
    assert r.status_code == status and r.headers["X-Request-ID"] == "test-req-12345"
    assert "data" not in body.replace("your data", "") and "projects" not in body and "lo_profile" not in body
    assert "internal_column_name" not in body and "index out of range" not in body
    if status >= 500:
        assert "ref test-req-12345" in body


def test_request_id_is_made_when_missing_or_odd(c):
    for sent in (None, "bad id with spaces", "x" * 100):
        r = c.get("/health", headers={"X-Request-ID": sent} if sent else {})
        assert len(r.headers["X-Request-ID"]) == 32 and r.headers["X-Request-ID"] != sent


def test_not_found_is_still_404(c):
    r = c.get("/dashboards/u/no-such-dashboard")
    assert r.status_code == 404 and "not found" in r.json()["detail"]
