# tests/test_prices.py
"""Free daily prices (prices.py): NSE daily files and mfapi.in NAVs land in the prices table; holidays are skipped,
each NSE day is downloaded once, days before the current NSE format fall back to the older one, unknown symbols are
warned about, suspect prices are flagged, refresh failures don't loop, the evening schedule.
The network is faked and every file is synthetic: no NSE / mfapi calls."""
import io
import zipfile
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from app.data import engine
from app.data import prices
from app.data import timeexpr

HEADER = "TradDt,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol\n"
CLOSE = {date(2026, 10, 1): 1100.0, date(2026, 10, 5): 1110.0}  # Oct 2 = holiday (404), Oct 3-4 = weekend


OLD_HEADER = "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN,\n"


def _zip(d, text=None):
    text = text or HEADER + f"{d},INFY,EQ,1,2,0.5,{CLOSE[d]},100\n{d},INFY,N1,9,9,9,9,9\n"  # N1 (a bond series) is ignored
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
        if "nseindia" in url and "_0_0_0_" not in url:
            return httpx.Response(404, request=req)  # older-format file: none for these days
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


# A day the current NSE format doesn't have (before early 2024) is read from the older file, trimmed the same way.
def test_nse_old_format_fallback(net, monkeypatch):
    day = date(2023, 11, 15)
    old = OLD_HEADER + ("INFY,EQ,1400,1420,1390,1410.5,1411,1395,12345,1.7e7,15-NOV-2023,900,INE009A01021,\n"
                        "INFY,N1,9,9,9,9,9,9,9,9,15-NOV-2023,9,X,\n")  # N1 (a bond series) is ignored
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        req = httpx.Request("GET", url)
        return httpx.Response(200, content=_zip(day, old), request=req) if "cm15NOV2023bhav" in url else httpx.Response(404, request=req)
    monkeypatch.setattr(prices.httpx, "get", fake_get)
    assert prices.nse_day(day) == {"INFY": {"symbol": "INFY", "open": "1400", "high": "1420", "low": "1390",
                                            "close": "1410.5", "volume": "12345"}}
    assert calls == ["https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20231115_F_0000.csv.zip",
                     "https://nsearchives.nseindia.com/content/historical/EQUITIES/2023/NOV/cm15NOV2023bhav.csv.zip"]
    prices.nse_day(day)
    assert len(calls) == 2  # cached after the first read


# A 404 from both formats is a holiday: cached as empty, never fetched again.
def test_nse_both_404_cached_as_holiday(net):
    assert prices.nse_day(date(2026, 10, 2)) == {}
    assert len(net) == 2 and "cm02OCT2026bhav" in net[1]
    assert prices.nse_day(date(2026, 10, 2)) == {} and len(net) == 2


# Rows for one symbol: a close per weekday from `start`.
def _rows(sym, closes, start=date(2026, 9, 1)):
    days = [d for d in (start + timedelta(n) for n in range(60)) if d.weekday() < 5]
    return [{"ts": d, "symbol": sym, "close": c} for d, c in zip(days, closes)]


def test_quality_flags():
    clean = _rows("A", [100, 101, 100, 102, 103, 101, 104]) + _rows("B", [50, 51, 52, 51, 53, 54, 55])
    today = clean[-1]["ts"]
    assert prices.quality_flags(clean, today) == []
    stale = prices.quality_flags(clean, today + timedelta(days=10))
    assert len(stale) == 2 and "A: no new price since" in stale[0] and "10 days ago" in stale[0]
    flat = prices.quality_flags(_rows("A", [100, 101, 101, 101, 101, 101, 101, 102]), date(2026, 9, 10))
    assert len(flat) == 1 and "same close 101 for 5+ days" in flat[0]
    jump = prices.quality_flags(_rows("A", [100, 101, 50, 51]), date(2026, 9, 4))
    assert len(jump) == 1 and "A: moved -50% on 2026-09-03" in jump[0]
    repeat = _rows("A", [100, 101, 101, 102]) + _rows("B", [50, 51, 51, 52])
    assert prices.quality_flags(repeat, date(2026, 9, 4)) == [
        "every symbol repeats its previous close on 2026-09-03: the data source may have copied the day before"]
