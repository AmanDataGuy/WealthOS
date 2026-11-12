# tests/test_engine.py
"""Storage, metrics, time expressions, the SQL guard, mapping, and the calculator / number checker.
Everything runs on synthetic data in a temporary folder; no LLM, no network."""
import json
from datetime import date

import pytest

from app.data import engine
from app.data import mapping
from app.data.engine import MetricQuery
from app.data.ingest import parse_file
from app.data.timeexpr import resolve
from app.ai.tools import calc
from app.ai.verify import SqlRejected, check_numbers

TRADES = ("symbol,trade_date,trade_type,quantity,price\n"
          "INFY,2024-04-03,buy,10,1400\n"
          "INFY,2026-05-10,sell,4,1600\n"
          "TCS,2026-04-15,buy,2,3800\n"
          "TCS,2026-06-20,sell,2,4000\n")
BANK = "Date,Narration,Debit,Credit\n05/04/2026,Rent,25000,\n06/04/2026,Salary,,90000\n10/05/2026,Groceries,4000,\n"


# Each test gets its own empty data folder.
@pytest.fixture(autouse=True)
def data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DATA_ROOT", tmp_path / "users")


# Upload a CSV for user "u" the way the API does (parse, then save) and return the parsed tables.
def _upload(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    tables = parse_file(p)
    engine.save_tables("u", tables)
    return tables


# Time words resolve to the right dates, including the Indian financial year boundaries.
@pytest.mark.parametrize("expr, today, expected", [
    ("fy", date(2026, 10, 26), (date(2026, 4, 1), date(2026, 10, 26))),
    ("fy", date(2026, 3, 31), (date(2025, 4, 1), date(2026, 3, 31))),
    ("last_fy", date(2026, 4, 1), (date(2025, 4, 1), date(2026, 3, 31))),
    ("last_month", date(2026, 3, 15), (date(2026, 2, 1), date(2026, 2, 28))),
    ("qtd", date(2026, 11, 5), (date(2026, 10, 1), date(2026, 11, 5))),
    ("trailing_12m", date(2028, 2, 29), (date(2027, 3, 1), date(2028, 2, 29))),
    ("2026-01-01..2026-03-31", date(2026, 10, 26), (date(2026, 1, 1), date(2026, 3, 31))),
])
def test_time_expressions(expr, today, expected):
    assert resolve(expr, today) == expected


# Spending metrics add up the right rows for the period, and every answer carries its provenance.
def test_flow_metrics(tmp_path):
    _upload(tmp_path, "bank.csv", BANK)
    r = engine.run_metric("u", MetricQuery(metrics=["total_spend", "total_income", "savings_rate_pct"], time="2026-04-01..2026-04-30"))
    assert r.rows == [{"total_spend": 25000.0, "total_income": 90000.0, "savings_rate_pct": pytest.approx(72.2222, 1e-3)}]
    assert r.provenance.badge == "trusted" and r.provenance.datasets[0]["file"] == "bank.csv"


# FIFO realized gains split short / long term by holding period, only for sells inside the period.
def test_realized_gains(tmp_path):
    _upload(tmp_path, "trades.csv", TRADES)
    r = engine.run_metric("u", MetricQuery(metrics=["realized_gains"], dimensions=["symbol"], time="2026-04-01..2027-03-31"))
    by = {row["symbol"]: row for row in r.rows}
    assert by["INFY"]["long_term_gain"] == 800.0 and by["INFY"]["short_term_gain"] == 0.0
    assert by["TCS"]["short_term_gain"] == 400.0 and by["TCS"]["long_term_gain"] == 0.0


# XIRR on a known case: invest 100, get 110 back exactly one year later = 10% a year.
def test_xirr_known_answer():
    assert engine.xirr([(date(2025, 1, 1), -100.0), (date(2026, 1, 1), 110.0)]) == pytest.approx(0.10, abs=1e-6)
    assert engine.xirr([(date(2025, 1, 1), -100.0)]) is None  # no money coming back: no answer, not a guess


# Bad metric names, bad dimensions and SQL that reaches outside the data are refused with a reason.
def test_refusals(tmp_path):
    _upload(tmp_path, "trades.csv", TRADES)
    with pytest.raises(ValueError):
        engine.run_metric("u", MetricQuery(metrics=["nope"]))
    with pytest.raises(ValueError):
        engine.run_metric("u", MetricQuery(metrics=["trade_count"], dimensions=["password"]))
    for sql in ["DELETE FROM trades", "SELECT 1; SELECT 2", "SELECT * FROM read_csv('x.csv')", "SELECT getenv('HOME')",
                "SELECT nope FROM trades", "SELECT * FROM users", "ATTACH 'x.db'", "SELECT * FROM duckdb_settings()"]:
        with pytest.raises(SqlRejected):
            engine.run_sql("u", sql)
    ok = engine.run_sql("u", "SELECT symbol, SUM(quantity) AS q FROM trades GROUP BY symbol ORDER BY symbol")
    assert ok.rows == [{"symbol": "INFY", "q": 14.0}, {"symbol": "TCS", "q": 4.0}] and ok.provenance.badge == "exploratory"


# Even with the guard bypassed, the locked connection cannot read files outside the user's folder.
def test_locked_connection(tmp_path):
    _upload(tmp_path, "trades.csv", TRADES)
    outside = tmp_path / "secret.csv"
    outside.write_text("a\n1\n", encoding="utf-8")
    con = engine.open_readonly("u")
    with pytest.raises(Exception):
        con.execute(f"SELECT * FROM read_csv('{outside.as_posix()}')").fetchall()
    with pytest.raises(Exception):
        con.execute("SET enable_external_access = true")


# Confirming a mapping saves the dataset; the next upload with the same columns maps itself.
def test_mapping_confirm_and_auto_apply(tmp_path):
    [t] = _upload(tmp_path, "mine.csv", "When,What,Money,DrCr\n05/04/2026,Rent,25000,Dr\n06/04/2026,Salary,90000,Cr\n")
    assert t.shape == "unknown"
    parsed, saved = mapping.confirm("u", t, "transactions", {"date": "When", "description": "What", "amount": "Money", "drcr": "DrCr"})
    assert saved and parsed.rows[0]["direction"] == "debit" and parsed.rows[1]["direction"] == "credit"
    again = mapping.auto_apply("u", t)
    assert again.shape == "transactions" and len(again.rows) == 2
    with pytest.raises(ValueError):
        mapping.confirm("u", t, "transactions", {"date": "When"})  # must-have columns missing


# The mapping AI call is charged to the user who uploaded (usage and the per-user cap need the user id).
def test_mapping_ai_call_names_the_user(tmp_path, monkeypatch):
    import asyncio

    from app.ai import llm_client
    [t] = _upload(tmp_path, "mine.csv", "When,What,Money,DrCr\n05/04/2026,Rent,25000,Dr\n")
    seen = {}

    async def fake_llm(**kw):
        seen.update(kw)
        return "{}"
    monkeypatch.setattr(llm_client, "call_llm", fake_llm)
    asyncio.run(mapping.suggest("u", t))
    assert seen["user_id"] == "u"


# Confidence per column: high when rules and AI agree, medium when only one found it, saved once confirmed;
# must-haves nobody found are listed as missing.
def test_mapping_confidence(tmp_path, monkeypatch):
    import asyncio

    [t] = _upload(tmp_path, "mine.csv", "Date,Narration,Amt,Kind\n05/04/2026,Rent,25000,Dr\n")
    ai = {"date": "Date", "amount": "Amt"}  # the AI agrees on the date, finds the amount, misses the narration

    async def fake_ai(headers, profile, user_id):
        return "transactions", ai
    monkeypatch.setattr(mapping, "_ask_ai", fake_ai)
    s = asyncio.run(mapping.suggest("u", t))
    assert s["via"] == "rules+ai" and s["missing"] == []
    assert s["confidence"] == {"date": "high", "description": "medium", "amount": "medium"}

    ai = {}  # the AI finds nothing: the money column is still missing
    s = asyncio.run(mapping.suggest("u", t))
    assert s["confidence"] == {"date": "medium", "description": "medium"} and s["missing"] == ["debit/credit or amount"]

    mapping.confirm("u", t, "transactions", {"date": "Date", "description": "Narration", "amount": "Amt", "drcr": "Kind"})
    s = asyncio.run(mapping.suggest("u", t))
    assert s["via"] == "saved" and set(s["confidence"].values()) == {"saved"} and s["missing"] == []


# Two workspaces never see each other's tables; deleting a workspace removes only its datasets.
def test_workspaces_isolated(tmp_path):
    from app.core import workspaces
    biz = workspaces.create_workspace("u", "Company MIS")
    bank, trades = tmp_path / "bank.csv", tmp_path / "trades.csv"
    bank.write_text(BANK, encoding="utf-8")
    trades.write_text(TRADES, encoding="utf-8")
    engine.save_tables("u", parse_file(bank))                 # My files
    engine.save_tables("u", parse_file(trades), biz["id"])    # Company MIS
    assert set(engine.schema(engine.open_readonly("u", "default"))) == {"transactions"}
    assert set(engine.schema(engine.open_readonly("u", biz["id"]))) == {"trades"}
    with pytest.raises(LookupError):
        engine.run_metric("u", MetricQuery(metrics=["trade_count"], time="all"), workspace_id="default")
    assert [w["datasets"] for w in workspaces.list_workspaces("u")] == [1, 1]
    assert workspaces.delete_workspace("u", biz["id"]) == 1
    assert set(engine.schema(engine.open_readonly("u"))) == {"transactions"}


# The same file uploaded into two workspaces is in both; deleting one workspace keeps the other's copy.
def test_same_file_in_two_workspaces(tmp_path):
    from app.core import workspaces
    biz = workspaces.create_workspace("u", "Company MIS")
    bank = tmp_path / "bank.csv"
    bank.write_text(BANK, encoding="utf-8")
    engine.save_tables("u", parse_file(bank))
    engine.save_tables("u", parse_file(bank), biz["id"])
    assert [w["datasets"] for w in workspaces.list_workspaces("u")] == [1, 1]
    workspaces.delete_workspace("u", biz["id"])
    assert set(engine.schema(engine.open_readonly("u", "default"))) == {"transactions"}
    with pytest.raises(ValueError):
        workspaces.delete_workspace("u", "default")  # My files always stays


# Ops build a dashboard edit by edit, bad ops are refused whole, undo goes back (and can be undone).
def test_dashboard_ops_versions_undo(tmp_path):
    from app.features import dashboards
    from app.features.dashboards import Op
    _upload(tmp_path, "bank.csv", BANK)
    d = dashboards.create("u", "Money")
    kpi = {"title": "Spend", "kind": "kpi", "query": {"metrics": ["total_spend"], "time": "fy"}, "layout": {"x": 0, "y": 0, "w": 4, "h": 3}}
    e2 = dashboards.save_ops("u", d.id, [Op(op="add_tile", tile_id="spend", value=kpi),
                                         Op(op="add_tile", tile_id="note", value={"kind": "text", "text": "Hi"})])
    e3 = dashboards.save_ops("u", d.id, [Op(op="update_tile", tile_id="spend", value={"kind": "bar"}),
                                         Op(op="resize_tile", tile_id="spend", value={"w": 6}),
                                         Op(op="set_style", tile_id="spend", value={"color": "#123456"}),
                                         Op(op="duplicate_tile", tile_id="note"), Op(op="set_time", value={"time": "mtd"})],
                             base_version=2)
    assert e3.version == 3 and e3.tiles["spend"].kind == "bar" and e3.tiles["spend"].layout.w == 6
    assert len(e3.tiles) == 3 and e3.global_time == "mtd" and e3.tiles["spend"].style == {"color": "#123456"}

    bad_ops = [[Op(op="move_tile", tile_id="nope", value={"x": 1})],
               [Op(op="resize_tile", tile_id="spend", value={"w": 13})],                  # off the grid
               [Op(op="update_tile", tile_id="spend", value={"query": {"metrics": ["made_up"]}})],
               [Op(op="update_tile", tile_id="spend", value={"query": None, "sql": "SELECT * FROM read_csv('x')"})],
               [Op(op="set_style", tile_id="spend", value={"onclick": "x"})],
               [Op(op="add_tile", value={"kind": "kpi", "query": {"metrics": ["total_spend"], "dimensions": ["nope"]}})],
               [Op(op="set_time", value={"time": "someday"})],
               [Op(op="rename_dashboard", value={"title": "ok"}), Op(op="delete_tile", tile_id="gone")]]  # all or nothing
    for ops in bad_ops:
        with pytest.raises(ValueError):
            dashboards.save_ops("u", d.id, ops)
    with pytest.raises(ValueError):
        dashboards.save_ops("u", d.id, [Op(op="rename_dashboard", value={"title": "x"})], base_version=2)  # stale screen
    assert dashboards.get("u", d.id).version == 3 and dashboards.get("u", d.id).title == "Money"

    undo = dashboards.revert("u", d.id)                  # back to edit 2
    assert undo.version == 4 and undo.tiles.keys() == e2.tiles.keys() and undo.global_time == "fy"
    assert dashboards.revert("u", d.id).tiles == {}      # undo again -> edit 1 (empty)
    assert dashboards.revert("u", d.id, 3).tiles.keys() == e3.tiles.keys()  # jump to any edit
    assert dashboards.get("u", d.id).version == 6  # every undo / revert is saved as a new edit
    assert dashboards.list_dashboards("u", "default")[0]["tiles"] == 3
    dashboards.delete("u", d.id)
    assert dashboards.list_dashboards("u") == []


# Refresh recomputes every tile without the LLM, a failing tile shows its own error, and the diff shows
# what changed after new data arrives; `time` moves only tiles that follow the dashboard period.
def test_dashboard_refresh(tmp_path, monkeypatch):
    from app.features import dashboards
    from app.ai import llm_client as llm
    from app.features.dashboards import Op

    def no_llm(*a, **k):
        raise AssertionError("refresh must not call the LLM")
    monkeypatch.setattr(llm, "call_llm", no_llm)
    monkeypatch.setattr(llm, "call_json", no_llm)
    _upload(tmp_path, "bank.csv", BANK)
    d = dashboards.create("u", "Money")
    kpi = {"kind": "kpi", "query": {"metrics": ["total_spend"], "time": "fy"}}
    dashboards.save_ops("u", d.id, [
        Op(op="add_tile", tile_id="spend", value=kpi),
        Op(op="add_tile", tile_id="pinned", value=kpi | {"query": {"metrics": ["total_spend"], "time": "2026-04-01..2026-04-30"}}),
        Op(op="add_tile", tile_id="monthly", value={"kind": "bar", "query": {"metrics": ["total_spend"], "dimensions": ["month"]},
                                                    "fields": {"x": "month", "y": "total_spend"}}),
        Op(op="add_tile", tile_id="sql", value={"kind": "table", "sql": "SELECT COUNT(*) AS n FROM transactions"}),
        Op(op="add_tile", tile_id="note", value={"kind": "text", "text": "hello"})])
    first = dashboards.refresh("u", d.id)
    tiles = first["tiles"]
    assert set(tiles) == {"spend", "pinned", "monthly", "sql"} and all("rows" in r for r in tiles.values())
    assert tiles["spend"]["rows"] == [{"total_spend": 29000.0}] and tiles["pinned"]["rows"] == [{"total_spend": 25000.0}]
    assert tiles["monthly"]["rows"] == [{"month": "2026-04", "total_spend": 25000.0}, {"month": "2026-05", "total_spend": 4000.0}]
    assert {c["status"] for c in first["changes"].values()} == {"new"}

    (tmp_path / "more").mkdir()
    _upload(tmp_path / "more", "bank2.csv", "Date,Narration,Debit,Credit\n12/05/2026,Fuel,1000,\n")
    second = dashboards.refresh("u", d.id)
    assert second["changes"]["spend"] == {"status": "changed", "values": {"total_spend": {"before": 29000.0, "after": 30000.0}}}
    assert second["changes"]["pinned"] == {"status": "same"}
    assert dashboards.refresh("u", d.id, "2026-05-01..2026-05-31")["tiles"]["pinned"]["rows"] == [{"total_spend": 25000.0}]

    dashboards.save_ops("u", d.id, [Op(op="set_time", value={"time": "last_fy"})])  # followers move, pinned stays
    spec = dashboards.get("u", d.id)
    assert spec.tiles["spend"].query.time == "last_fy" and spec.tiles["pinned"].query.time == "2026-04-01..2026-04-30"
    (engine.DATA_ROOT / "u" / "datasets").rename(tmp_path / "gone")  # data vanished: each tile reports its own error
    assert all("error" in r for r in dashboards.refresh("u", d.id)["tiles"].values())
    assert dashboards.last_refresh("u", d.id)["version"] == spec.version


# The AI's ops are checked (a bad answer gets one retry with the reason), numbers in titles are refused,
# nothing is saved until the user applies the previewed ops with base_version.
def test_dashboard_ai_edit(tmp_path, monkeypatch):
    import asyncio

    from app.features import dashboards
    from app.ai import llm_client as llm
    from app.features.dashboards import Op
    _upload(tmp_path, "bank.csv", BANK)
    d = dashboards.create("u", "Money")
    dashboards.save_ops("u", d.id, [Op(op="add_tile", tile_id="spend", value={
        "title": "Spending", "kind": "kpi", "query": {"metrics": ["total_spend"], "time": "fy"}})])
    good = {"summary": "Bar chart, plus last year", "ops": [
        {"op": "update_tile", "tile_id": "spend", "value": {"kind": "bar", "query": {"metrics": ["total_spend"], "dimensions": ["month"], "time": "fy"}}},
        {"op": "add_tile", "value": {"title": "Spending last year", "kind": "bar", "layout": {"x": 0, "y": 4, "w": 6, "h": 4},
                                     "query": {"metrics": ["total_spend"], "dimensions": ["month"], "time": "last_fy"}}}]}
    answers = []

    async def fake_json(system, user, model_cls, **kw):
        answers.append(user)
        return model_cls(**script.pop(0))
    monkeypatch.setattr(llm, "call_json", fake_json)

    script = [{"ops": [{"op": "delete_tile", "tile_id": "nope"}]}, good]  # first answer refused, retry succeeds
    out = asyncio.run(dashboards.ai_edit("u", d.id, "make spending a bar chart and add last year"))
    assert "refused" in answers[1] and out["base_version"] == 2
    assert out["diff"]["changed"] == {"spend": ["kind", "query"]} and len(out["diff"]["added"]) == 1
    assert dashboards.get("u", d.id).version == 2  # preview only
    saved = dashboards.save_ops("u", d.id, [Op(**o) for o in out["ops"]], base_version=out["base_version"])
    assert set(saved.tiles) == set(out["preview"]["tiles"]) and saved.tiles["spend"].kind == "bar"

    script = [{"ops": [{"op": "rename_dashboard", "value": {"title": "Spent ₹29,000"}}]}] * 2  # invented number, twice
    with pytest.raises(ValueError):
        asyncio.run(dashboards.ai_edit("u", d.id, "put the total in the title"))


# A chat answer's plan becomes a tile that refreshes; answers not computed from data can't be pinned.
def test_pin_answer(tmp_path):
    from app.features import dashboards
    _upload(tmp_path, "bank.csv", BANK)
    d = dashboards.create("u", "Money")
    dashboards.pin_answer("u", d.id, {"kind": "metric", "metric_query": {"metrics": ["total_spend"], "time": "fy"}}, "Spend?")
    dashboards.pin_answer("u", d.id, {"kind": "metric", "metric_query": {"metrics": ["total_spend"], "dimensions": ["month"]}})
    spec = dashboards.pin_answer("u", d.id, {"kind": "sql", "sql": "SELECT description, amount FROM transactions"})
    kinds = sorted((t.layout.y, t.kind) for t in spec.tiles.values())
    assert kinds == [(0, "kpi"), (4, "line"), (8, "table")]
    assert all("rows" in r for r in dashboards.refresh("u", d.id)["tiles"].values())
    with pytest.raises(ValueError):
        dashboards.pin_answer("u", d.id, {"kind": "news", "news_query": "INFY"})


# The Excel export has a sheet per data tile with the refreshed rows, a Provenance sheet, and text from
# the data is stored as text (no formula injection).
def test_export_xlsx(tmp_path):
    from io import BytesIO

    from openpyxl import load_workbook

    from app.features import dashboards
    _upload(tmp_path, "bank.csv", BANK.replace("Groceries", "=HYPERLINK(\"http://x\")"))
    d = dashboards.create("u", "Money")
    dashboards.pin_answer("u", d.id, {"kind": "metric", "metric_query": {"metrics": ["total_spend"], "time": "fy"}}, "Spend")
    dashboards.pin_answer("u", d.id, {"kind": "sql", "sql": "SELECT description, amount FROM transactions ORDER BY amount"}, "Rows")
    book = load_workbook(BytesIO(dashboards.export_xlsx("u", d.id)))
    assert book.sheetnames == ["Provenance", "Spend", "Rows"]
    assert [c.value for c in book["Spend"][2]] == [29000]
    cell = book["Rows"]["A2"]
    assert cell.value.startswith("=HYPERLINK") and cell.data_type == "s"
    assert book["Provenance"]["C3"].value == "trusted" and "SELECT" in book["Provenance"]["E3"].value


# Every template applies on sample data and refreshes with no errors; one needs its table in the workspace.
def test_templates_render_on_sample_data(tmp_path):
    from app.features import dashboards
    from app.features import templates
    from app.core import workspaces
    ws = workspaces.create_workspace("u", "Other")["id"]
    _upload(tmp_path, "bank.csv", BANK)
    _upload(tmp_path, "trades.csv", TRADES)
    _upload(tmp_path, "holdings.csv", "Instrument,Qty.,Avg. cost,Cur. val\nINFY,6,1400,9600\nTCS,1,3800,4100\n")

    assert sorted(t["id"] for t in templates.catalog()) == ["capital_gains", "portfolio", "spending"]
    assert templates.suggest("u", ws, "portfolio")["missing"]  # no holdings in the other workspace
    for t in templates.catalog():
        s = templates.suggest("u", "default", t["id"])
        assert not s["missing"], (t["id"], s)
        spec = templates.apply("u", "default", t["id"], s["table"], s["mapping"])
        result = dashboards.refresh("u", spec.id)
        errors = {k: v["error"] for k, v in result["tiles"].items() if "error" in v}
        assert len(spec.tiles) >= 3 and not errors, (t["id"], errors)
        assert all(r["rows"] for r in result["tiles"].values()), t["id"]
    with pytest.raises(LookupError):
        templates.suggest("u", ws, "nope")


# The calculator does arithmetic only; anything else is refused.
def test_calc():
    assert calc("1,23,456 * 0.12") == pytest.approx(14814.72)
    assert calc("round(12.3456, 2)") == 12.35
    for bad in ['__import__("os")', "open('x')", "2**1000", "(1).__class__"]:
        with pytest.raises(ValueError):
            calc(bad)


# The number checker passes honest sentences and catches invented numbers.
def test_check_numbers():
    vals = [29000.0, 90000.0, 67.777, 38800.0]
    assert check_numbers("You spent ₹29,000 and received ₹90,000, a savings rate of 67.8%.", vals) == []
    assert check_numbers("You kept ₹61,000 of ₹90,000 (₹0.39 lakh portfolio).", vals) == []
    assert check_numbers("You spent ₹31,500 in the last 3 months of 2026.", vals) == ["31,500"]
    assert check_numbers("NVIDIA revenue was 21,59,380 million.", [215938.0, 215938e6]) == ["21,59,380 million"]


# Planner slips the engine absorbs: a date range written as a filter becomes the period, and "symbol" /
# "symbol_or_scheme" (the instrument, named differently on trades and holdings) work on either table.
def test_forgiving_query_shapes(tmp_path):
    _upload(tmp_path, "trades.csv", TRADES)
    by_filter = engine.run_metric("u", MetricQuery(metrics=["trade_count"], time="all",
                                  filters=[{"column": "trade_date", "op": "between", "value": ["2026-04-01", "2027-03-31"]}]))
    as_text = engine.run_metric("u", MetricQuery(metrics=["trade_count"], time="all",
                                filters=[{"column": "trade_date", "op": "between", "value": "2026-04-01..2027-03-31"}]))
    by_time = engine.run_metric("u", MetricQuery(metrics=["trade_count"], time="2026-04-01..2027-03-31"))
    assert by_filter.rows == by_time.rows == as_text.rows and by_filter.provenance.period["start"] == "2026-04-01"
    split = engine.run_metric("u", MetricQuery(metrics=["trade_count"], dimensions=["symbol_or_scheme"], time="all"))
    assert {r["symbol"] for r in split.rows} == {"INFY", "TCS"}
