# tests/test_mcp_server.py
"""The read-only MCP server. Its tools answer through the MCP layer with provenance, it only exposes
read tools, and a missing / forged token is refused. Every tool is also called on the synthetic sample data."""
import asyncio
import json

import jwt
import pytest

from app.data import engine
from app import mcp_server
from app.data.ingest import parse_file
from app.data.samples import HOLDINGS, write_sample_files

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
    assert names == {"list_workspaces", "list_metrics", "query_metric", "explain_number", "tax_estimate",
                     "portfolio_drift", "list_backtests"}  # read-only, no SQL tool
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


# The synthetic sample data (bank statement, tradebook, holdings) loaded for user "u", read in local mode
# (WEALTHOS_USER with WEALTHOS_ENV=dev and no secret).
@pytest.fixture()
def samples(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DATA_ROOT", tmp_path / "users")
    for path in write_sample_files(tmp_path):
        engine.save_tables("u", parse_file(path))
    monkeypatch.delenv("WEALTHOS_JWT_SECRET", raising=False)
    monkeypatch.setenv("WEALTHOS_ENV", "dev")
    monkeypatch.setenv("WEALTHOS_USER", "u")


def test_list_workspaces_and_metrics_on_samples(samples):
    (ws,) = _call("list_workspaces")
    assert {t["table"]: t["rows"] for t in ws["tables"]} == {"transactions": 121, "trades": 5, "holdings": 2}
    m = _call("list_metrics")
    assert {"total_spend", "portfolio_value", "realized_gains"} <= set(m["metrics"])
    assert m["metrics"]["portfolio_value"]["table"] == "holdings" and "fy" in m["time"]


def test_query_metric_and_explain_on_samples(samples):
    total = sum(v for *_, v in HOLDINGS)
    r = _call("query_metric", {"metrics": ["portfolio_value"], "time": "today"})
    assert r["rows"] == [{"portfolio_value": total}] and r["provenance"]["datasets"][0]["file"] == "holdings.csv"
    e = _call("explain_number", {"metric": "portfolio_value", "time": "today"})
    assert e["value"] == r["rows"] and e["table"] == "holdings" and "sql" in e
    with pytest.raises(Exception):
        _call("explain_number", {"metric": "no_such_metric"})


# Capital gains come from the sample tradebook: INFY 10 sold at +300 after 17 months (long term), TCS 5 at +300
# after 5 months (short term). Both regimes are computed on the same income.
def test_tax_estimate_on_samples(samples):
    t = _call("tax_estimate", {"fy": "2026-27", "salary": 1440000})
    assert (t["capital_gains"]["short_term"], t["capital_gains"]["long_term"]) == (1500.0, 3000.0)
    assert t["lower"] == ("new" if t["new"]["total_tax"] <= t["old"]["total_tax"] else "old")
    assert t["difference"] == abs(t["new"]["total_tax"] - t["old"]["total_tax"])


def test_portfolio_drift_on_samples(samples):
    from app.features.rebalance import save_targets
    assert "no target mix set yet: set one to see drift" in _call("portfolio_drift")["warnings"]
    save_targets("u", {"INFY": 50, "HDFCBANK": 50})
    d = _call("portfolio_drift")
    rows = {r["holding"]: r for r in d["rows"]}
    assert d["total_value"] == 66800.0  # 15,800 INFY + 51,000 HDFCBANK
    assert rows["INFY"]["change_to_target"] == 17600.0 and rows["HDFCBANK"]["change_to_target"] == -17600.0
    assert rows["INFY"]["off_target"] and rows["HDFCBANK"]["drift_pts"] == 26.35


def test_list_backtests_on_samples(samples, tmp_path):
    import pandas as pd
    from app.features import strategy
    assert _call("list_backtests") == []
    days = pd.bdate_range("2026-01-01", periods=10)
    prices = tmp_path / "prices.csv"
    prices.write_text("date,symbol,open,close\n" + "".join(f"{d.date()},FLAT,100,100\n" for d in days), encoding="utf-8")
    engine.save_tables("u", parse_file(prices))
    run = strategy.run("u", None, {"universe": ["FLAT"], "entry": {"op": "gt", "args": [{"op": "close"}, 50]}})
    (card,) = _call("list_backtests")
    assert card["id"] == run["run_card"]["id"] and card["universe"] == ["FLAT"]
