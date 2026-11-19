# tests/test_company.py
"""Company mode, offline and keyless: trimmed public SEC data for Apple (tests/fixtures/company) stands in for EDGAR,
and a fake model stands in for the one AI step."""
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import openpyxl
import pytest

from app.company import analysis, pipeline, releases, sec, statements, tieout, workbook
from app.data import engine

FIX = Path(__file__).parent / "fixtures" / "company"
RELEASE = """<html><style>p {color: red}</style><body><p>Apple reports fourth quarter results</p>
<p>The Company posted quarterly revenue of $102.5 billion, up 8 percent year over year.</p>
<script>var x = 999;</script><p>Services revenue reached an all-time high of $28.8 billion.</p></body></html>"""


# Serve SEC URLs from the fixtures; anything else would be a network call, which the test must not make.
def _fake_fetch(url: str) -> bytes:
    for part, name in (("company_tickers", "company_tickers.json"), ("submissions", "aapl_submissions.json"),
                       ("companyfacts", "aapl_companyfacts.json")):
        if part in url:
            return (FIX / name).read_bytes()
    raise AssertionError(f"unexpected network call: {url}")


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(sec, "fetch", _fake_fetch)
    monkeypatch.setattr(engine, "DATA_ROOT", tmp_path / "users")


def _facts():
    return json.loads((FIX / "aapl_companyfacts.json").read_text())


def test_resolve_ticker_name_ambiguous_and_unknown():
    assert sec.resolve("AAPL")["cik"] == "0000320193"
    assert sec.resolve("apple inc")["ticker"] == "AAPL"
    many = sec.resolve("General")["candidates"]
    assert {"GE", "GD", "GM"} <= {c["ticker"] for c in many}
    assert sec.resolve("zzzz") == {"candidates": []}


def test_filings_lists_annual_quarterly_and_earnings_and_refuses_banks(monkeypatch):
    f = sec.filings("0000320193")
    assert [len(f[k]) for k in ("annual", "quarterly", "earnings")] == [5, 1, 1]
    assert all(x["form"] == "10-K" for x in f["annual"]) and "2.02" in f["earnings"][0]["items"]
    bank = json.loads((FIX / "aapl_submissions.json").read_text()) | {"sic": "6021"}
    monkeypatch.setattr(sec, "fetch", lambda url: json.dumps(bank).encode())
    with pytest.raises(ValueError, match="bank"):
        sec.filings("0000320193")


def test_statements_tie_out_on_the_recorded_filing():
    st = statements.build(_facts())
    assert len(st["years"]) == 5 and st["years"] == sorted(st["years"])
    v = st["values"]
    assert v["gross_profit"][-1] == pytest.approx(v["gross_profit_reported"][-1])
    assert st["sources"]["revenue"][-1]["concept"].startswith("us-gaap:")
    checks = {c["check"]: c for c in tieout.run(v, st["years"])}
    assert all(c["status"] == "pass" for c in checks.values()), checks


def test_ratios_and_dcf_are_consistent():
    v = statements.build(_facts())["values"]
    r = analysis.ratios(v, 5)
    assert r["gross_margin"][-1] == pytest.approx(v["gross_profit"][-1] / v["revenue"][-1])
    assert r["revenue_growth"][0] is None  # no year before the first column
    inputs = analysis.dcf_inputs(v)
    d = analysis.dcf(inputs)
    assert d["enterprise_value"] == pytest.approx(sum(d["pv"]) + d["pv_terminal"])
    assert 0 <= inputs["growth"] <= analysis.ASSUMPTIONS["growth_cap"]


def test_release_numbers_are_verified_against_the_text():
    text = releases.html_text(RELEASE)
    assert "999" not in text and "color" not in text
    real = {"quote": "quarterly revenue of $102.5 billion", "value": 102.5}
    planted = {"quote": "quarterly revenue of $110.0 billion", "value": 110.0}
    wrong_number = {"quote": "quarterly revenue of $102.5 billion", "value": 105.0}
    assert releases.verify(real, text) and not releases.verify(planted, text) and not releases.verify(wrong_number, text)


# Run the whole pipeline for Apple with the release fetch and the AI step faked.
def _run_apple(monkeypatch) -> dict:
    async def fake_call_json(system, user, model_cls, max_tokens=800, user_id=None):
        return model_cls(items=[
            {"kind": "kpi", "name": "Services revenue", "value": 28.8, "unit": "USD billion", "period": "Q4",
             "quote": "Services revenue reached an all-time high of $28.8 billion"},
            {"kind": "guidance", "name": "Next-quarter revenue", "value": 130, "unit": "USD billion", "period": "Q1",
             "quote": "revenue guidance of $130 billion"}])  # planted: not in the release
    monkeypatch.setattr(releases, "call_json", fake_call_json)
    monkeypatch.setattr(releases, "fetch_release", lambda filing: ("https://example.test/ex99.htm", releases.html_text(RELEASE)))
    return pipeline.run("AAPL", "u1")


def test_pipeline_end_to_end_builds_a_workbook_that_recalculates(monkeypatch):
    result = _run_apple(monkeypatch)
    assert result["status"] == "done"
    assert (result["releases"]["verified"], result["releases"]["unverified"]) == (1, 1)
    checks = {c["check"]: c for c in result["tieouts"]}
    assert checks["workbook_recalculates"]["status"] == "pass" and checks["workbook_recalculates"]["years_tested"] > 50
    wb = openpyxl.load_workbook(pipeline.workbook_path("u1", result["run_id"]))
    assert wb.sheetnames == ["Cover", "Income Statement", "Balance Sheet", "Cash Flow", "Ratios", "Valuation", "Releases", "Checks"]
    assert wb["Income Statement"]["B7"].value.startswith("=")  # gross profit is a formula
    with pytest.raises(engine.NotFound):
        pipeline.workbook_path("someone-else", result["run_id"])


# Past its deadline the build stops before the AI step and saves nothing (the API has already answered 408).
def test_build_past_deadline_stops_without_calling_the_ai_or_saving(monkeypatch):
    clock = iter([0.0, 0.0, 100.0])  # in time for the filings list and the facts, late before the release step
    monkeypatch.setattr(pipeline, "time", SimpleNamespace(monotonic=lambda: next(clock)))  # this module's clock only
    monkeypatch.setattr(releases, "read", lambda *a: pytest.fail("the AI step ran after the deadline"))
    with pytest.raises(TimeoutError):
        pipeline.run("AAPL", "u1", deadline=50.0)
    assert not pipeline.runs_dir("u1").exists()


def test_ambiguous_query_returns_candidates_without_building():
    out = pipeline.run("General", "u1")
    assert out["status"] == "choose" and len(out["candidates"]) >= 3


def test_recalc_follows_references_across_sheets():
    wb = openpyxl.Workbook()
    a = wb.active
    a.title = "A"
    b = wb.create_sheet("B s")
    a["B2"], b["C3"], a["D4"] = 10, "='A'!B2*2", "=('B s'!C3 - 5)^2 / B2"
    assert tieout.recalc(wb, "A", "D4") == pytest.approx(22.5)


def test_sensitivity_grid_centre_is_the_value_and_falls_with_rate_and_growth_below_it():
    d = analysis.dcf(analysis.dcf_inputs(statements.build(_facts())["values"]))
    grid = d["sensitivity"]
    assert grid[2][2] == pytest.approx(d["per_share"])
    for row in grid:  # more terminal growth -> more value
        assert row == sorted(row)
    for col in zip(*grid):  # higher discount rate -> less value
        assert list(col) == sorted(col, reverse=True)


def test_sensitivity_leaves_cells_blank_where_growth_reaches_the_rate(monkeypatch, tmp_path):
    result = _run_apple(monkeypatch)
    inputs = result["valuation"]["inputs"] | {"discount_rate": 0.04}  # rates 2%-6% against growth 2%-4%
    result["valuation"] = {"inputs": inputs, **analysis.dcf(inputs)}
    expected = workbook.build(result, tmp_path / "low_rate.xlsx")
    ws = openpyxl.load_workbook(tmp_path / "low_rate.xlsx")["Valuation"]
    for j, dr in enumerate(analysis.RATE_STEPS):
        for k, dg in enumerate(analysis.GROWTH_STEPS):
            blank = 0.03 + dg >= 0.04 + dr - 1e-12
            assert (result["valuation"]["sensitivity"][j][k] is None) == blank
            assert (ws.cell(row=40 + j, column=2 + k).value is None) == blank
    assert tieout.workbook_check(tmp_path / "low_rate.xlsx", expected)["status"] == "pass"


def test_terminal_cross_check_matches_a_hand_calculation():
    v = statements.build(_facts())["values"]
    d = analysis.dcf(analysis.dcf_inputs(v))
    rev = [x for x in v["revenue"] if x]
    g = min(max((rev[-1] / rev[0]) ** (1 / (len(rev) - 1)) - 1, 0.0), 0.15)
    fcf5 = v["fcf"][-1] * (1 + g) ** 5
    terminal = fcf5 * 1.03 / (0.09 - 0.03)
    ebitda5 = (v["operating_income"][-1] + v["dna"][-1]) * (1 + g) ** 5
    assert d["exit_ev_to_ebitda"] == pytest.approx(terminal / ebitda5)
    assert d["terminal_share"] == pytest.approx((terminal / 1.09 ** 5) / d["enterprise_value"])
    assert 0 < d["terminal_share"] < 1


# The new Valuation cells, recalculated by LibreOffice itself, equal the Python values.
@pytest.mark.skipif(not shutil.which("soffice"), reason="LibreOffice (soffice) is not installed")
def test_libreoffice_recalculates_the_new_valuation_cells(monkeypatch, tmp_path):
    result = _run_apple(monkeypatch)
    expected = workbook.build(result, tmp_path / "in" / "model.xlsx")
    subprocess.run(["soffice", f"-env:UserInstallation=file://{tmp_path}/lo", "--headless", "--convert-to", "xlsx",
                    "--outdir", str(tmp_path / "out"), str(tmp_path / "in" / "model.xlsx")], check=True, capture_output=True, timeout=120)
    ws = openpyxl.load_workbook(tmp_path / "out" / "model.xlsx", data_only=True)["Valuation"]
    new = {c: want for (sheet, c), want in expected.items() if sheet == "Valuation" and int(c.lstrip("ABCDEF")) >= 35}
    assert len(new) == 2 + 5 + 5 + 25
    for coord, want in new.items():
        assert ws[coord].value == pytest.approx(want, rel=1e-9), coord
    assert ws["D42"].value == pytest.approx(result["valuation"]["per_share"])
