# tests/test_company_review.py
"""Earnings review, offline: quarters picked from SEC-shaped facts, locked forecasts and variances, kill rules, the
trade planner's limit checks, and the company comparison on the recorded Apple filing. The quarterly company here is
made up (a calendar-year company with round numbers), so every expected value can be worked out by hand."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.company import compare, quarters, review, sec
from app.data import engine
from app.features.rebalance import plan_trades

FIX = Path(__file__).parent / "fixtures" / "company"


def _f(start, end, val, form="10-Q", filed=None):
    return {"start": start, "end": end, "val": val, "form": form, "filed": filed or end, "accn": f"acc-{end}"}


# Two fiscal years (calendar): 3-month quarters, 9-month year-to-date figures and the 10-K year, $M.
def _facts(q3_2026_revenue=150):
    rev = [_f("2025-01-01", "2025-03-31", 100e6), _f("2025-04-01", "2025-06-30", 110e6), _f("2025-07-01", "2025-09-30", 120e6),
           _f("2025-01-01", "2025-09-30", 330e6), _f("2025-01-01", "2025-12-31", 460e6, "10-K", "2026-02-10"),
           _f("2026-01-01", "2026-03-31", 120e6), _f("2026-04-01", "2026-06-30", 132e6),
           _f("2026-07-01", "2026-09-30", q3_2026_revenue * 1e6, filed="2026-10-28"),
           _f("2026-01-01", "2026-09-30", 402e6, filed="2026-10-28")]
    cost = [{**r, "val": r["val"] * 0.6} for r in rev]
    cost[7]["val"] = 99e6  # Q3 2026 gross margin 34%
    op = [{**r, "val": r["val"] * 0.2} for r in rev]
    eps = [_f("2025-07-01", "2025-09-30", 1.00), _f("2026-04-01", "2026-06-30", 1.10),
           _f("2026-07-01", "2026-09-30", 1.20, filed="2026-10-28"), _f("2025-01-01", "2025-12-31", 4.0, "10-K")]
    usd = lambda rows: {"units": {"USD": rows}}  # noqa: E731
    return {"facts": {"us-gaap": {"Revenues": usd(rev), "CostOfRevenue": usd(cost), "OperatingIncomeLoss": usd(op),
                                  "EarningsPerShareDiluted": {"units": {"USD/shares": eps}}}}}


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DATA_ROOT", tmp_path / "users")


def test_quarters_pick_three_month_facts_and_derive_q4():
    s = {q["end"]: q for q in quarters.series(_facts())}
    assert [s[e]["revenue"] for e in ("2025-03-31", "2025-06-30", "2025-09-30")] == [100, 110, 120]  # not the 330 YTD
    q4 = s["2025-12-31"]
    assert q4["revenue"] == 130 and "revenue" in q4["derived"] and q4["eps_diluted"] is None  # 460 - 330; EPS never derived
    q3 = s["2026-09-30"]
    assert q3["gross_margin_pct"] == 34.0 and q3["revenue_yoy_pct"] == 25.0 and q3["eps_diluted"] == 1.2


def test_ytd_check_passes_and_catches_a_wrong_quarter():
    assert quarters.ytd_check(_facts())["revenue"] == {"tested": 2, "passed": 2, "max_diff_musd": 0.0}
    bad = quarters.ytd_check(_facts(q3_2026_revenue=160))["revenue"]  # 120 + 132 + 160 != 402
    assert bad["tested"] == 2 and bad["passed"] == 1 and bad["max_diff_musd"] == 10.0


def _lock(day: str, **kw):
    body = {"ticker": "demo", "quarter_end": "2026-09-30", "revenue_musd": 160, "gross_margin_pct": 40,
            "operating_margin_pct": 20, "eps_diluted": 1.30, **kw}
    return review.lock("u", review.Forecast(**body), now=datetime.fromisoformat(day + "T09:00:00").replace(tzinfo=timezone.utc))


def test_review_uses_only_a_forecast_locked_before_the_results():
    early = _lock("2026-09-15", consensus_eps=1.25)
    late = _lock("2026-10-29", eps_diluted=1.20)  # after the 10-Q was filed: hindsight
    r = review.review("u", "DEMO", quarters.series(_facts()))["reviews"][0]
    assert r["status"] == "reviewed" and r["forecast"]["id"] == early["id"] and r["late"] == [late["id"]]
    v = {row["driver"]: row["variance"] for row in r["variances"]}
    assert v == {"Revenue ($M)": -6.25, "Gross margin (%)": -6.0, "Operating margin (%)": 0.0,
                 "Diluted EPS ($)": -7.69, "Diluted EPS vs consensus ($)": -4.0}
    assert r["material"] and r["material_reasons"] == ["diluted EPS -7.7% vs your forecast"]


def test_forecasts_are_hashed_and_append_only():
    _lock("2026-09-15")
    _lock("2026-09-20", eps_diluted=1.25)
    rows = review.forecasts("u", "DEMO")
    assert [r["id"] for r in rows] == ["DEMO-2026-09-30-1", "DEMO-2026-09-30-2"] and all(r["intact"] for r in rows)
    assert review.content_hash({**rows[0], "eps_diluted": 9.99}) != rows[0]["sha256"]


def test_kill_rules_one_quarter_two_in_a_row_and_versus_forecast():
    rules = [{"metric": "gross_margin_pct", "op": "<", "value": 38, "quarters": 1},
             {"metric": "gross_margin_pct", "op": "<", "value": 38, "quarters": 2},  # previous quarter was 40%
             {"metric": "eps_vs_forecast_pct", "op": "<=", "value": -5, "quarters": 1}]
    _lock("2026-09-15", kill_rules=rules)
    hits = [k["hit"] for k in review.review("u", "DEMO", quarters.series(_facts()))["reviews"][0]["kill_rules"]]
    assert hits == [True, False, True]


def test_release_flags_keep_verified_quotes_only_and_awaiting_results():
    _lock("2026-09-15")
    release = {"url": "u", "items": [{"kind": "one_off", "name": "gain", "verified": True},
                                     {"kind": "guidance", "name": "made up", "verified": False}]}
    r = review.review("u", "DEMO", quarters.series(_facts()), release)["reviews"][0]
    assert [i["name"] for i in r["release_flags"]] == ["gain"]
    review.lock("u", review.Forecast(ticker="DEMO", quarter_end="2026-12-31", eps_diluted=1.4))
    assert review.review("u", "DEMO", quarters.series(_facts()))["reviews"][0]["status"] == "awaiting results"


def test_accuracy_over_reviewed_quarters():
    rows = lambda e: {"status": "reviewed", "variances": [{"driver": "Diluted EPS ($)", "variance": e}]}  # noqa: E731
    assert review.accuracy([rows(10.0), rows(-2.0), rows(4.0), {"status": "awaiting results"}]) == \
        {"reviewed": 3, "mean_abs_eps_error_pct": 5.33, "mean_signed_eps_error_pct": 4.0, "within_5pct": 2}


def test_verdict_needs_a_real_forecast():
    with pytest.raises(engine.NotFound):
        review.record_verdict("u", review.Verdict(forecast_id="nope", verdict="intact"))
    f = _lock("2026-09-15")
    assert review.record_verdict("u", review.Verdict(forecast_id=f["id"], verdict="weakened"))["verdict"] == "weakened"


BOOK = [{"ticker": "AAA", "shares": 100, "price": 50, "sector": "Tech"},   # 5,000 = 50%
        {"ticker": "BBB", "shares": 40, "price": 75, "sector": "Tech"},    # 3,000 = 30%
        {"ticker": "CCC", "shares": 10, "price": 150, "sector": "Energy"}]  # 1,500 = 15%; cash 500 = 5%


def test_plan_trades_follows_targets_skips_small_and_reports_breaches():
    p = plan_trades(BOOK, 500, {"AAA": 40, "BBB": 30.5, "CCC": 25},
                    {"max_position_pct": 35, "max_sector_pct": 60, "min_cash_pct": 2, "min_trade": 100})
    rows = {r["ticker"]: r for r in p["rows"]}
    assert p["total_value"] == 10000 and rows["AAA"]["trade"] == -1000 and rows["CCC"]["trade"] == 1000
    assert rows["BBB"]["trade"] == 0 and rows["BBB"]["skipped_small"]  # a 50 trade is under the 100 minimum
    assert p["cash_after"] == 500 and p["sold_pct"] == 10.0
    assert p["breaches"] == ["AAA would be 40.0% (limit 35%)", "Tech would be 70.0% (limit 60%)"]


def test_plan_trades_cash_shortfall_and_bad_targets():
    # AAA's 50 sell is skipped as too small, but CCC's 550 buy still runs: 50 more than the 500 cash
    short = plan_trades(BOOK, 500, {"AAA": 49.5, "BBB": 30, "CCC": 20.5}, {"min_trade": 100})
    assert short["breaches"] == ["the trades need 50.0 more cash than the book holds"]
    assert plan_trades(BOOK, 500, {"AAA": 50, "BBB": 30, "CCC": 17}, {"min_cash_pct": 5})["breaches"] == \
        ["cash would be 3.0% (minimum 5%)"]
    with pytest.raises(ValueError):
        plan_trades(BOOK, 500, {"AAA": 80, "BBB": 30}, {})
    with pytest.raises(ValueError):
        plan_trades(BOOK, 500, {"ZZZ": 10}, {})


def test_compare_on_the_recorded_filing_and_a_bad_name(monkeypatch):
    files = {"company_tickers": "company_tickers.json", "submissions": "aapl_submissions.json", "companyfacts": "aapl_companyfacts.json"}
    monkeypatch.setattr(sec, "fetch", lambda url: (FIX / next(v for k, v in files.items() if k in url)).read_bytes())
    out = compare.compare(["AAPL", "zzzz"], {"AAPL": 200.0})
    apple, bad = out["rows"]
    assert apple["ticker"] == "AAPL" and apple["revenue_musd"] > 0 and apple["source"]["accn"]
    assert apple["pe"] == round(200.0 / apple["eps_diluted"], 2) and 0 < apple["gross_margin"] < 100
    assert bad["ticker"] == "ZZZZ" and "not one company" in bad["error"]


# The routes: lock and list a forecast, another user is refused, a plan comes back, bad targets are a 400.
def test_routes(monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main
    monkeypatch.setattr(main, "JWT_SECRET", "test-secret-at-least-32-characters-long")
    me = TestClient(main.app, headers={"Authorization": f"Bearer {main._issue_token('u', 'u')}"})
    other = TestClient(main.app, headers={"Authorization": f"Bearer {main._issue_token('v', 'v')}"})
    locked = me.post("/company/u/forecasts", json={"ticker": "demo", "quarter_end": "2026-09-30", "eps_diluted": 1.3})
    assert locked.status_code == 200 and locked.json()["id"] == "DEMO-2026-09-30-1"
    assert [f["id"] for f in me.get("/company/u/forecasts").json()] == ["DEMO-2026-09-30-1"]
    assert other.get("/company/u/forecasts").status_code == 403
    assert me.post("/company/u/forecasts", json={"ticker": "demo", "quarter_end": "2026-09-30"}).status_code == 422
    plan = me.post("/company/u/plan", json={"positions": BOOK, "cash": 500, "targets": {"AAA": 40}, "max_position_pct": 35})
    assert plan.status_code == 200 and plan.json()["breaches"] == ["AAA would be 40.0% (limit 35%)"]
    assert me.post("/company/u/plan", json={"positions": BOOK, "targets": {"AAA": 120}}).status_code == 400
