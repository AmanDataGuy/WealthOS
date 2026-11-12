"""
# sec.py - everything company mode reads from SEC EDGAR

- `fetch(url)`: one SEC download through `tools._sec_get` (User-Agent from `SEC_USER_AGENT`, under 10 requests/s),
  cached on disk under `backend/storage/sec_cache/` for a day (filing documents never change, so they are kept).
- `resolve(query)`: ticker or name -> {ticker, cik, name}, or the candidates when the name is ambiguous.
- `filings(cik)`: the last five 10-Ks, the latest 10-Q and the latest earnings 8-K (Item 2.02); refuses banks,
  insurers and REITs (their statements have another shape) and companies without a 10-K.
- `company_facts(cik)`: SEC's XBRL "companyfacts" JSON (every figure the company ever tagged, with its filing).

Used by `pipeline.py` and `releases.py`.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from app.ai import tools
from app.data import engine

DAY = 86_400


# Where SEC answers are cached (public data, shared by every user).
def _cache_dir():
    return engine.DATA_ROOT.parent / "sec_cache"


# Download a SEC URL as bytes, from the disk cache when fresh enough. Archive files are immutable, so never refetched.
def fetch(url: str) -> bytes:
    path = _cache_dir() / hashlib.sha256(url.encode()).hexdigest()
    if path.exists() and ("/Archives/" in url or time.time() - path.stat().st_mtime < DAY):
        return path.read_bytes()
    data = tools._sec_get(url, raw=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


# Lowercase letters and digits only, with "inc", "corp" and the like dropped: "Apple Inc." -> "apple".
def _norm(name: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    return " ".join(w for w in words if w not in {"inc", "corp", "corporation", "co", "company", "ltd", "plc", "the", "holdings"})


# Find the company: exact ticker, then exact name, then a partial name that only one company has.
def resolve(query: str) -> dict:
    rows = json.loads(fetch("https://www.sec.gov/files/company_tickers.json")).values()
    companies = [{"ticker": r["ticker"].upper(), "cik": str(r["cik_str"]).zfill(10), "name": r["title"]} for r in rows]
    q = query.strip()
    for match in ([c for c in companies if c["ticker"] == q.upper().replace(".", "-")],
                  [c for c in companies if _norm(c["name"]) == _norm(q)],
                  [c for c in companies if _norm(q) and _norm(q) in _norm(c["name"])]):
        by_cik = {c["cik"]: c for c in match}  # one company can list several share classes
        if len(by_cik) == 1:
            return match[0]
        if by_cik:
            return {"candidates": list(by_cik.values())[:15]}
    return {"candidates": []}


# The filings the model is built from, newest first, each with a link to its SEC index page.
def filings(cik: str) -> dict:
    sub = json.loads(fetch(f"https://data.sec.gov/submissions/CIK{cik}.json"))
    sic = int(sub.get("sic") or 0)
    if 6000 <= sic <= 6799:
        raise ValueError(f"{sub.get('name')} is a bank, insurer or real-estate trust (SIC {sic}): "
                         "their statements have a different shape, so company mode does not cover them")
    recent = sub["filings"]["recent"]
    rows = [dict(zip(recent, values)) for values in zip(*recent.values())]
    for r in rows:
        folder = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{r['accessionNumber'].replace('-', '')}"
        r.update(folder=folder, url=f"{folder}/{r['accessionNumber']}-index.htm")
    keep = ("form", "accessionNumber", "filingDate", "reportDate", "primaryDocument", "items", "folder", "url")
    pick = lambda rs: [{k: r.get(k, "") for k in keep} for r in rs]  # noqa: E731
    annual = [r for r in rows if r["form"] == "10-K"][:5]
    if not annual:
        raise ValueError(f"{sub.get('name')} files no 10-K (a foreign filer?): company mode needs US annual reports")
    return {"name": sub.get("name"), "sic": sic, "sic_description": sub.get("sicDescription", ""),
            "fiscal_year_end": sub.get("fiscalYearEnd", ""), "annual": pick(annual),
            "quarterly": pick([r for r in rows if r["form"] == "10-Q"][:1]),
            "earnings": pick([r for r in rows if r["form"] == "8-K" and "2.02" in (r.get("items") or "")][:1])}


# Every XBRL figure the company reported, as SEC publishes it.
def company_facts(cik: str) -> dict:
    return json.loads(fetch(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"))
