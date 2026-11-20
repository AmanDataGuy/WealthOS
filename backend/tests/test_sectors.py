# tests/test_sectors.py
"""The Nifty 500 sector map with the download patched out: a synthetic CSV parses to the right map, the file is
cached and fetched again only after the refresh age, a failed refresh keeps the old copy, and the NSE switch off
gives {} without any download."""
import os
import time

import httpx
import pytest

from app.data import engine, sectors

CSV = ("Company Name,Industry,Symbol,Series,ISIN Code\n"
       "Alpha Ltd.,Information Technology,alpha,EQ,INE000000001\n"
       "Beta Bank Ltd.,Financial Services,BETA ,EQ,INE000000002\n")


@pytest.fixture(autouse=True)
def data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DATA_ROOT", tmp_path / "users")
    monkeypatch.setenv("WEALTHOS_ENABLE_NSE", "1")


# Patch httpx.get to answer with `text` (or raise), counting the calls.
def _fake(monkeypatch, text=CSV, fail=False):
    calls = []

    def get(url, **kw):
        calls.append(url)
        if fail:
            raise httpx.ConnectError("offline")
        return httpx.Response(200, text=text, request=httpx.Request("GET", url))
    monkeypatch.setattr(sectors.httpx, "get", get)
    return calls


def test_csv_gives_map_and_is_cached(monkeypatch):
    calls = _fake(monkeypatch)
    assert sectors.sector_map() == {"ALPHA": "Information Technology", "BETA": "Financial Services"}
    assert sectors.sector_map() == {"ALPHA": "Information Technology", "BETA": "Financial Services"}
    assert len(calls) == 1  # second call read the cached file


def test_refresh_after_age_and_failure_keeps_old_copy(monkeypatch):
    _fake(monkeypatch)
    sectors.sector_map()
    path = engine.DATA_ROOT.parent / "cache" / "nifty500.csv"
    old = time.time() - (sectors.REFRESH_DAYS + 1) * 86400
    os.utime(path, (old, old))
    calls = _fake(monkeypatch, text=CSV.replace("Financial Services", "Banks"))
    assert sectors.sector_map()["BETA"] == "Banks" and len(calls) == 1
    os.utime(path, (old, old))
    calls = _fake(monkeypatch, fail=True)
    assert sectors.sector_map()["BETA"] == "Banks" and len(calls) == 1


def test_failed_download_or_bad_file_gives_empty(monkeypatch):
    _fake(monkeypatch, fail=True)
    assert sectors.sector_map() == {}
    _fake(monkeypatch, text="<html>blocked</html>")
    assert sectors.sector_map() == {}
    assert not (engine.DATA_ROOT.parent / "cache" / "nifty500.csv").exists()


def test_switch_off_gives_empty_without_download(monkeypatch):
    monkeypatch.setenv("WEALTHOS_ENABLE_NSE", "0")
    calls = _fake(monkeypatch)
    assert sectors.sector_map() == {} and calls == []
