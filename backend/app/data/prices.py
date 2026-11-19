"""
# prices.py - free daily prices for stocks, ETFs and mutual funds

| Source | What | Cost / key |
|---|---|---|
| NSE "bhavcopy" (nsearchives.nseindia.com) | every NSE stock / ETF's open, high, low, close, volume for one trading day, official; the current file format from early 2024, the older format before that | free, no key |
| mfapi.in (built on AMFI's NAV files) | a mutual fund scheme's whole daily NAV history; also a scheme search | free, no key |

- One NSE file per trading day (~200 KB zip). Each day is downloaded once and cached, trimmed to the price columns,
  under `data/cache/nse/` (public data, shared by every user); a day missing in both formats is a holiday, cached as
  an empty file.
- NSE closes are **not** adjusted for splits / bonuses (`adjusted = false`); the strategy lab's split guard warns on
  jumps. NAVs need no adjustment (`adjusted = true`).
- `quality_flags` adds plain-English warnings to a fetched source: stale, flat runs, one-day jumps over 20%, and days
  where every symbol repeats its previous close.
- A workspace remembers what it fetched (`prices.json`: source, symbols, start), so `refresh` brings everything up to
  today; `worker.py` does that once a day.
- NSE fetching is off in production unless `WEALTHOS_ENABLE_NSE=1` (NSE data is not ours to redistribute).
- Everything lands in the normal `prices` table (ts, symbol, open, high, low, close, volume, adjusted), one dataset
  per source + symbol list, replaced on every refresh.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.data import engine
from app.core import settings
from app.data import timeexpr
from app.data.ingest import ExtractedTable, SourceRef

NSE_URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
# NSE serves the file above only from early 2024; earlier days are in this older format.
NSE_OLD_URL = "https://nsearchives.nseindia.com/content/historical/EQUITIES/{d:%Y}/{mon}/cm{d:%d}{mon}{d:%Y}bhav.csv.zip"
# Source columns for: series, then symbol, open, high, low, close, volume.
NSE_COLS = ("SctySrs", "TckrSymb", "OpnPric", "HghPric", "LwPric", "ClsPric", "TtlTradgVol")
NSE_OLD_COLS = ("SERIES", "SYMBOL", "OPEN", "HIGH", "LOW", "CLOSE", "TOTTRDQTY")
MFAPI = "https://api.mfapi.in/mf"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}
SERIES = {"EQ", "BE", "BZ", "SM", "ST"}  # equity series (ETFs trade as EQ); others are bonds, warrants ...
MAX_DAYS = 3 * 366  # ponytail: one file per day, so cap a first fetch at ~3 years (~750 downloads, cached after)
STALE_DAYS = 10  # a listed stock or fund quotes every trading day; 10 calendar days without one is more than any holiday run
FLAT_RUN = 5  # five identical closes in a row is a week of no trading or a feed copying the last value
BIG_MOVE = 0.20  # NSE's widest daily price band is 20%, so a bigger one-day move is a split / bonus or a bad tick


# Folder where downloaded NSE files are kept.
def _cache() -> Path:
    return engine.DATA_ROOT.parent / "cache" / "nse"


# One NSE trading day as {symbol: {open, high, low, close, volume}} (cached; {} for a holiday / weekend).
def nse_day(d: date) -> dict[str, dict]:
    path = _cache() / f"{d:%Y%m%d}.csv"
    if not path.exists():
        text = ""  # stays empty when both formats 404: no trading that day
        for url, cols in ((NSE_URL, NSE_COLS), (NSE_OLD_URL, NSE_OLD_COLS)):
            r = httpx.get(url.format(d=d, mon=f"{d:%b}".upper()), headers=HEADERS, timeout=30, follow_redirects=True)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            z = zipfile.ZipFile(io.BytesIO(r.content))
            rows = csv.DictReader(io.StringIO(z.read(z.namelist()[0]).decode("utf-8", "replace")))
            out = io.StringIO()
            w = csv.writer(out)
            w.writerow(["symbol", "open", "high", "low", "close", "volume"])
            for row in rows:
                if (row.get(cols[0]) or "").strip() in SERIES:
                    w.writerow([row[c].strip() for c in cols[1:]])
            text = out.getvalue()
            break
        if d < timeexpr.today_ist() or text:  # today's file may not be published yet: don't cache it as a holiday
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    else:
        text = path.read_text(encoding="utf-8")
    return {r["symbol"]: r for r in csv.DictReader(io.StringIO(text))} if text else {}


# NSE fetching is for self-hosted, personal use: a hosted server must not redistribute NSE data without NSE's
# consent, so production has it off unless WEALTHOS_ENABLE_NSE=1.
def nse_enabled() -> bool:
    return os.getenv("WEALTHOS_ENABLE_NSE", "0" if settings.env() == "production" else "1") == "1"


# Daily bars for NSE symbols between two dates. Returns (rows, warnings).
def fetch_nse(symbols: list[str], start: date, end: date) -> tuple[list[dict], list[str]]:
    if not nse_enabled():
        raise ValueError("NSE prices are disabled on this server (licensing); upload a prices CSV instead")
    start = max(start, end - timedelta(days=MAX_DAYS))
    days = [start + timedelta(n) for n in range((end - start).days + 1) if (start + timedelta(n)).weekday() < 5]
    with ThreadPoolExecutor(4) as pool:  # ponytail: 4 at a time is polite to NSE; cached days cost nothing
        files = dict(zip(days, pool.map(nse_day, days)))
    rows = [{"ts": d, "symbol": s, "open": float(b["open"]), "high": float(b["high"]), "low": float(b["low"]),
             "close": float(b["close"]), "volume": float(b["volume"] or 0), "adjusted": False}
            for d, day in files.items() for s in symbols if (b := day.get(s))]
    found = {r["symbol"] for r in rows}
    warnings = [f"no NSE prices for {s}: check the trading symbol (e.g. INFY, NIFTYBEES)" for s in symbols if s not in found]
    if found:
        warnings.append("NSE closes are not adjusted for splits or bonuses")
    return rows, warnings


# Mutual fund schemes whose name matches the text: [{code, name}] (from mfapi.in).
def search_mf(text: str) -> list[dict]:
    r = httpx.get(f"{MFAPI}/search", params={"q": text}, timeout=30)
    r.raise_for_status()
    return [{"code": str(s["schemeCode"]), "name": s["schemeName"]} for s in r.json()[:20]]


# Daily NAVs for scheme codes since `start`; the symbol is the scheme name (as in a CAS / holdings export).
def fetch_mf(codes: list[str], start: date) -> tuple[list[dict], list[str]]:
    rows, warnings = [], []
    for code in codes:
        r = httpx.get(f"{MFAPI}/{code}", timeout=60)
        data = r.json() if r.status_code == 200 else {}
        if not data.get("data"):
            warnings.append(f"no NAV history for scheme code {code}")
            continue
        name = data["meta"]["scheme_name"]
        for p in data["data"]:
            d = datetime.strptime(p["date"], "%d-%m-%Y").date()
            if d >= start and float(p["nav"]) > 0:
                rows.append({"ts": d, "symbol": name, "open": None, "high": None, "low": None,
                             "close": float(p["nav"]), "volume": None, "adjusted": True})
    return rows, warnings


# Plain-English warnings about suspect prices in a batch of rows (ts, symbol, close): stale, flat, jumpy, or a day
# where every symbol just repeats its previous close. `today` is the date the series should reach.
def quality_flags(rows: list[dict], today: date) -> list[str]:
    series: dict[str, list[tuple[date, float]]] = {}
    for r in sorted(rows, key=lambda r: r["ts"]):
        series.setdefault(r["symbol"], []).append((r["ts"], r["close"]))
    flags, repeats = [], {}
    for sym, pts in series.items():
        if (today - pts[-1][0]).days >= STALE_DAYS:
            flags.append(f"{sym}: no new price since {pts[-1][0]}, {(today - pts[-1][0]).days} days ago")
        run = 1
        for (d0, c0), (d1, c1) in zip(pts, pts[1:]):
            run = run + 1 if c1 == c0 else 1
            if run == FLAT_RUN:
                flags.append(f"{sym}: the same close {c1} for {FLAT_RUN}+ days in a row up to {d1}")
            if c0 and abs(c1 / c0 - 1) > BIG_MOVE:
                flags.append(f"{sym}: moved {c1 / c0 - 1:+.0%} on {d1}, a possible split / bonus or a bad price")
            if c1 == c0:
                repeats.setdefault(d1, set()).add(sym)
    if len(series) > 1:
        flags += [f"every symbol repeats its previous close on {d}: the data source may have copied the day before"
                  for d, syms in sorted(repeats.items()) if len(syms) == len(series)]
    return flags


# ── Saved price sources per workspace ──────────────────────────────────────────────────────────────────────

# File listing a workspace's saved price sources.
def _sources_path(user_id: str, workspace_id: str) -> Path:
    return engine.user_dir(user_id) / "workspaces" / engine.safe_id(workspace_id) / "prices.json"


# The workspace's price sources: [{id, source, symbols, start, last_fetched, rows, last_date, warnings}].
def list_sources(user_id: str, workspace_id: str) -> list[dict]:
    path = _sources_path(user_id, workspace_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


# Write the workspace's price sources to disk.
def _save_sources(user_id: str, workspace_id: str, sources: list[dict]) -> None:
    path = _sources_path(user_id, workspace_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sources, indent=1, default=str), encoding="utf-8")


# Fetch one source (source = "nse" with trading symbols, or "mf" with scheme codes) from `start` to today, save it
# as a prices dataset in the workspace and remember it for refreshes. Returns the source entry.
def add(user_id: str, workspace_id: str, source: str, symbols: list[str], start: date | None = None) -> dict:
    if source not in ("nse", "mf"):
        raise ValueError("source must be 'nse' (stocks / ETFs) or 'mf' (mutual fund scheme codes)")
    symbols = sorted({s.strip().upper() if source == "nse" else s.strip() for s in symbols if s.strip()})
    if not symbols or len(symbols) > 50:
        raise ValueError("give 1 to 50 symbols / scheme codes")
    today = timeexpr.today_ist()
    start = start or today - timedelta(days=365)
    rows, warnings = fetch_nse(symbols, start, today) if source == "nse" else fetch_mf(symbols, start)
    if not rows:
        raise ValueError("; ".join(warnings) or "no prices found")
    warnings += quality_flags(rows, today)
    key = f"{source}:{','.join(symbols)}"
    src = SourceRef(file=f"{'NSE' if source == 'nse' else 'mfapi.in'}: {', '.join(symbols)}"[:200],
                    content_hash=hashlib.sha256(key.encode()).hexdigest())  # same source -> same dataset, replaced
    table = ExtractedTable(shape="prices", columns=list(rows[0]), rows=rows, source=src, status="parsed", warnings=warnings)
    engine.save_tables(user_id, [table], workspace_id)
    entry = {"id": src.content_hash[:12], "source": source, "symbols": symbols, "start": start.isoformat(),
             "last_fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"), "rows": len(rows),
             "last_date": max(r["ts"] for r in rows).isoformat(), "warnings": warnings}
    _save_sources(user_id, workspace_id, [s for s in list_sources(user_id, workspace_id) if s["id"] != entry["id"]] + [entry])
    return entry


# Bring every saved source of a workspace up to today. Returns the refreshed entries (a failure is recorded on the
# entry, with the attempt time, so the worker doesn't retry it in a loop).
def refresh(user_id: str, workspace_id: str) -> list[dict]:
    out = []
    for s in list_sources(user_id, workspace_id):
        try:
            out.append(add(user_id, workspace_id, s["source"], s["symbols"], date.fromisoformat(s["start"])))
        except (ValueError, httpx.HTTPError) as e:
            failed = {**s, "last_fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "warnings": [f"refresh failed: {e}"]}
            _save_sources(user_id, workspace_id, [x for x in list_sources(user_id, workspace_id) if x["id"] != s["id"]] + [failed])
            out.append(failed)
    return out


# Stop refreshing one source; its prices dataset is removed too.
def remove(user_id: str, workspace_id: str, source_id: str) -> None:
    for m in engine.list_datasets(user_id, workspace_id):
        if m["shape"] == "prices" and m["source"]["content_hash"].startswith(source_id):
            engine.remove_dataset(user_id, m)
    _save_sources(user_id, workspace_id, [s for s in list_sources(user_id, workspace_id) if s["id"] != source_id])


# Every (user, workspace) with a price source not refreshed since today's 19:00 IST (NSE publishes the day's file in
# the evening), for worker.py. Before 19:00 nothing is due.
def due(now: datetime | None = None) -> list[tuple[str, str]]:
    ist = timezone(timedelta(hours=5, minutes=30))
    now = (now or datetime.now(timezone.utc)).astimezone(ist)
    cutoff = now.replace(hour=19, minute=0, second=0, microsecond=0)
    if now < cutoff:
        return []
    return [(p.parents[2].name, p.parent.name) for p in engine.DATA_ROOT.glob("*/workspaces/*/prices.json")
            if any(datetime.fromisoformat(s["last_fetched"]) < cutoff for s in json.loads(p.read_text(encoding="utf-8")))]
