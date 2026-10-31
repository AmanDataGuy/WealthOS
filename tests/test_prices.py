# tests/test_prices.py
"""Free daily prices (prices.py): NSE daily files and mfapi.in NAVs land in the prices table; holidays are skipped,
each NSE day is downloaded once, unknown symbols are warned about, refresh failures don't loop, the evening schedule.
The network is faked: no NSE / mfapi calls."""
import io
import zipfile
from datetime import date, datetime, timezone

import httpx
import pytest

from wealthos import engine
from wealthos import prices
from wealthos import timeexpr

HEADER = "TradDt,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol\n"
CLOSE = {date(2026, 10, 1): 1100.0, date(2026, 10, 5): 1110.0}  # Oct 2 = holiday (404), Oct 3-4 = weekend


def _zip(d):
    text = HEADER + f"{d},INFY,EQ,1,2,0.5,{CLOSE[d]},100\n{d},INFY,N1,9,9,9,9,9\n"  # N1 (a bond series) is ignored
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("bhav.csv", text)
    return buf.getvalue()


@pytest.fixture()
def net(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DATA_ROOT", tmp_path / "users")
    monkeypatch.setattr(timeexpr, "today_ist", lambda: date(2026, 10, 5))
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        req = httpx.Request("GET", url)
        if "nseindia" in url:
            d = datetime.strptime(url.split("_0_0_0_")[1][:8], "%Y%m%d").date()
            return httpx.Response(200, content=_zip(d), request=req) if d in CLOSE else httpx.Response(404, request=req)
        if url.endswith("/mf/122639"):
            return httpx.Response(200, json={"meta": {"scheme_name": "Flexi Cap - Direct - Growth"},
                                             "data": [{"date": "05-10-2026", "nav": "90.5"}, {"date": "01-10-2026", "nav": "89.0"}]}, request=req)
        return httpx.Response(404, json={}, request=req)
    monkeypatch.setattr(prices.httpx, "get", fake_get)
    return calls


def test_nse_and_mf_into_prices(net):
    e = prices.add("u", "default", "nse", ["infy", "NOPE"], date(2026, 10, 1))
    assert e["rows"] == 2 and e["last_date"] == "2026-10-05" and "NOPE" in e["warnings"][0]
    first = len(net)
    prices.add("u", "default", "nse", ["INFY", "NOPE"], date(2026, 10, 1))  # same source again: cached days, same dataset
    assert len(net) == first and len(prices.list_sources("u", "default")) == 1
    prices.add("u", "default", "mf", ["122639"], date(2026, 10, 1))
    rows = engine.run_sql("u", "SELECT symbol, ts, close, adjusted FROM prices ORDER BY symbol, ts").rows
    assert [(r["symbol"], r["close"], r["adjusted"]) for r in rows] == [
        ("Flexi Cap - Direct - Growth", 89.0, True), ("Flexi Cap - Direct - Growth", 90.5, True),
        ("INFY", 1100.0, False), ("INFY", 1110.0, False)]
    with pytest.raises(ValueError):
        prices.add("u", "default", "nse", ["NOPE"], date(2026, 10, 1))  # nothing found: refused with the reason
    prices.remove("u", "default", e["id"])
    assert {r["symbol"] for r in engine.run_sql("u", "SELECT DISTINCT symbol FROM prices").rows} == {"Flexi Cap - Direct - Growth"}


def test_refresh_failure_recorded_and_evening_schedule(net, monkeypatch):
    prices.add("u", "default", "mf", ["122639"], date(2026, 10, 1))
    prices._save_sources("u", "default", [{**s, "last_fetched": "2026-10-05T14:00:00+00:00"}  # fetched yesterday evening
                                          for s in prices.list_sources("u", "default")])
    ist_noon = datetime(2026, 10, 6, 6, 30, tzinfo=timezone.utc)      # 12:00 IST: nothing due yet
    ist_evening = datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc)   # 19:30 IST: the source fetched earlier is due
    assert prices.due(ist_noon) == [] and prices.due(ist_evening) == [("u", "default")]

    def down(url, **kw):
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(prices.httpx, "get", down)
    out = prices.refresh("u", "default")
    assert "refresh failed" in out[0]["warnings"][0]
    assert prices.list_sources("u", "default")[0]["last_fetched"] > "2026-10-06"  # attempt recorded: no retry loop
