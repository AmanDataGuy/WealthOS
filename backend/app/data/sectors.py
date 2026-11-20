"""
# sectors.py - the industry of each Nifty 500 stock, by NSE trading symbol

- Source: NSE Indices' public Nifty 500 constituent list (niftyindices.com, `ind_nifty500list.csv`, columns
  Company Name, Industry, Symbol, Series, ISIN Code). Free, no key.
- NSE terms allow personal use only, so this follows the same `WEALTHOS_ENABLE_NSE` switch as NSE prices
  (`prices.nse_enabled()`): off in production unless turned on.
- The file is cached next to the NSE price cache (`data/cache/nifty500.csv`) and downloaded again after
  `REFRESH_DAYS`; if a refresh fails the older copy is still used.
- Returns {} when the switch is off or there is no usable file; the caller says why. Used by `features/risk.py`.
"""
from __future__ import annotations

import csv
import io
import time

import httpx

from app.data import prices

URL = "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv"
REFRESH_DAYS = 30  # the index is reviewed twice a year, so a monthly download stays current without bothering NSE


# Symbol -> industry from the CSV text, or {} when the text is not the expected file (e.g. an error page).
def _parse(text: str) -> dict[str, str]:
    rows = csv.DictReader(io.StringIO(text))
    if not {"Symbol", "Industry"} <= set(rows.fieldnames or []):
        return {}
    return {r["Symbol"].strip().upper(): r["Industry"].strip() for r in rows if r["Symbol"] and r["Industry"]}


# NSE trading symbol (upper case) -> its industry, for every Nifty 500 stock; {} when off or unavailable.
def sector_map() -> dict[str, str]:
    if not prices.nse_enabled():
        return {}
    path = prices._cache().parent / "nifty500.csv"
    if not path.exists() or time.time() - path.stat().st_mtime > REFRESH_DAYS * 86400:
        try:
            r = httpx.get(URL, headers=prices.HEADERS, timeout=30, follow_redirects=True)
            r.raise_for_status()
            if _parse(r.text):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(r.text, encoding="utf-8")
        except httpx.HTTPError:
            pass  # keep the older copy if there is one
    return _parse(path.read_text(encoding="utf-8")) if path.exists() else {}
