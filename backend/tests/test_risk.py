# tests/test_risk.py
"""The holdings risk report on synthetic holdings and prices with answers worked out by hand: VaR / CVaR on a known
return list, a drawdown with known dates, two uncorrelated holdings, holdings without (enough) prices reported, and
the refusal when there is too little history."""
import math

import pandas as pd
import pytest

from app.data import engine
from app.data.ingest import parse_file
from app.features import risk


@pytest.fixture(autouse=True)
def data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DATA_ROOT", tmp_path / "users")


# Save a CSV under tmp_path and load it into user "u"'s default workspace.
def _upload(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    engine.save_tables("u", parse_file(p))


# Daily closes from 100, one row per day per symbol, following a list of daily returns.
def _prices(tmp_path, series: dict[str, list[float]]):
    days = pd.bdate_range("2026-01-01", periods=max(len(r) for r in series.values()) + 1)
    lines = []
    for sym, rets in series.items():
        price = 100.0
        lines.append(f"{days[0].date()},{sym},{price}")
        for d, r in zip(days[1:], rets):
            price *= 1 + r
            lines.append(f"{d.date()},{sym},{price}")
    _upload(tmp_path, "prices.csv", "date,symbol,close\n" + "\n".join(lines) + "\n")


# 100 returns -5.0% .. +4.9%: the 5% quantile (linear) is -4.505%, and the days at or below it average -4.8%.
def test_var_cvar_on_known_returns():
    var, cvar = risk.var_cvar(pd.Series([i / 1000 for i in range(-50, 50)]))
    assert var == pytest.approx(0.04505) and cvar == pytest.approx(0.048)


# 100 -> 120 -> 90 -> 95 -> 130: the worst fall is 120 -> 90 (-25%), back above 120 on the last day.
def test_drawdown_dates():
    days = pd.bdate_range("2026-01-01", periods=5)
    fall, peak, trough, back = risk.drawdown(pd.Series([100, 120, 90, 95, 130.0], index=days))
    assert fall == pytest.approx(-0.25) and (peak, trough, back) == (days[1], days[2], days[4])
    assert risk.drawdown(pd.Series([100, 120, 90.0], index=days[:3]))[3] is None  # never recovered


# AAA and BBB move +-1% in patterns with zero correlation and the same spread, held 50/50: each carries half the
# risk; the portfolio moves +1%, 0, 0, -1% so the 95% VaR and CVaR are both 1% (₹100 of ₹10,000 priced).
# CCC has no prices and DDD only 31 days: both are left out with their share of value.
def test_report_two_uncorrelated_holdings(tmp_path):
    _upload(tmp_path, "holdings.csv", "Instrument,Qty.,Avg. cost,Cur. val\nAAA,1,1,5000\nBBB,1,1,5000\n"
                                      "CCC,1,1,8000\nDDD,1,1,2000\n")
    _prices(tmp_path, {"AAA": [0.01, -0.01, 0.01, -0.01] * 20, "BBB": [0.01, 0.01, -0.01, -0.01] * 20,
                       "DDD": [0.0] * 30})
    r = risk.report("u")
    assert r["label"] == risk.LABEL and (r["total_value"], r["priced_value"]) == (20000.0, 10000.0)
    assert r["period"]["days"] == 80
    assert {h["holding"]: h["risk_share_pct"] for h in r["holdings"]} == {"AAA": 50.0, "BBB": 50.0}
    assert r["top_correlations"] == [{"a": "AAA", "b": "BBB", "correlation": 0.0}]
    assert r["var_95_1d"] == {"pct": 1.0, "inr": 100.0} and r["cvar_95_1d"] == {"pct": 1.0, "inr": 100.0}
    std = math.sqrt(40 * 0.01 ** 2 / 79)  # 40 days of +-1%, 40 flat, sample std over 80 days
    assert r["volatility_pct"] == round(100 * std * math.sqrt(252), 2)
    # Each 4-day cycle ends at 1.01 x 0.99 = 0.9999 of its start: the peak is day one's 1.01, the low the last day.
    assert r["max_drawdown"]["pct"] == round(100 * (0.9999 ** 20 / 1.01 - 1), 2)
    assert r["max_drawdown"]["trough"] == r["period"]["end"] and r["max_drawdown"]["recovered"] is None
    assert {(x["holding"], x["share_pct"], x["reason"]) for x in r["left_out"]} == {
        ("CCC", 40.0, "no price history"), ("DDD", 10.0, "only 31 days of prices")}
    assert any("50.0% of the value, left out" in w for w in r["warnings"])
    assert r["sectors"] is None and any("sector" in w for w in r["warnings"])


# Too little history is refused with a clear message, as is a workspace with no prices at all.
def test_too_little_history_refused(tmp_path):
    _upload(tmp_path, "holdings.csv", "Instrument,Qty.,Avg. cost,Cur. val\nAAA,1,1,5000\n")
    with pytest.raises(ValueError, match="too little price history"):
        risk.report("u")
    _prices(tmp_path, {"AAA": [0.01, -0.01] * 15})
    with pytest.raises(ValueError, match=f"more than {risk.MIN_DAYS} days"):
        risk.report("u")
