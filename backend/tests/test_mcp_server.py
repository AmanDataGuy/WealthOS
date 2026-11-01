# tests/test_mcp_server.py
"""The read-only MCP server. Its tools answer through the MCP layer with provenance, it only exposes
read tools, and a missing / forged token is refused."""
import asyncio
import json

import jwt
import pytest

from app.data import engine
from app import mcp_server
from app.data.ingest import parse_file

BANK = "Date,Narration,Debit,Credit\n05/04/2026,Rent,25000,\n06/04/2026,Salary,,90000\n10/05/2026,Groceries,4000,\n"


@pytest.fixture()
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DATA_ROOT", tmp_path / "users")
    p = tmp_path / "bank.csv"
    p.write_text(BANK, encoding="utf-8")
    engine.save_tables("u", parse_file(p))
    monkeypatch.setenv("WEALTHOS_JWT_SECRET", "s" * 32)
    monkeypatch.setenv("WEALTHOS_TOKEN", jwt.encode({"sub": "u"}, "s" * 32, algorithm="HS256"))


def _call(name, args=None):
    out = asyncio.run(mcp_server.mcp.call_tool(name, args or {}))
    if isinstance(out, tuple):  # (content blocks, structured result): a list comes back as {"result": [...]}
        return out[1].get("result", out[1])
    return json.loads(out[0].text)


def test_tools_answer_with_provenance(data):
    names = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert names == {"list_workspaces", "list_metrics", "query_metric", "explain_number"}  # read-only, no SQL tool
    assert _call("list_workspaces")[0]["tables"][0]["file"] == "bank.csv"
    assert "total_spend" in _call("list_metrics")["metrics"]
    r = _call("query_metric", {"metrics": ["total_spend"], "time": "2026-04-01..2026-06-30"})
    assert r["rows"] == [{"total_spend": 29000.0}] and r["provenance"]["datasets"][0]["file"] == "bank.csv"
    e = _call("explain_number", {"metric": "total_spend", "time": "2026-04-01..2026-06-30"})
    assert "direction = 'debit'" in e["definition"] and e["period"]["start"] == "2026-04-01" and e["badge"] == "trusted"
    assert _call("list_workspaces")[0]["tables"][0]["rows"] == 3


def test_token_required(data, monkeypatch):
    monkeypatch.setenv("WEALTHOS_TOKEN", jwt.encode({"sub": "u"}, "forged-" + "x" * 32, algorithm="HS256"))
    with pytest.raises(Exception):
        _call("list_metrics")
    monkeypatch.delenv("WEALTHOS_TOKEN")
    with pytest.raises(Exception):
        _call("list_metrics")
    monkeypatch.setenv("WEALTHOS_TOKEN", jwt.encode({"sub": "someone_else"}, "s" * 32, algorithm="HS256"))
    assert _call("list_workspaces")[0]["tables"] == []  # another user sees only their own (empty) data
