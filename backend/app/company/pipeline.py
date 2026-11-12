"""
# pipeline.py - company name or ticker in, checked Excel model out

`run(query, user_id)` calls the steps in order (see `company/__init__.py`), saves the run under
`storage/users/<user>/company/<run id>.json` with its `.xlsx` beside it, and returns the run. An ambiguous or unknown
name returns `{"status": "choose", "candidates": [...]}` instead, so the user can pick the ticker.

Used by the `/company/...` API routes and `evals/run_company.py`.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.company import analysis, releases, sec, statements, tieout, workbook
from app.data import engine


# Folder holding one user's company runs.
def runs_dir(user_id: str) -> Path:
    return engine.user_dir(user_id) / "company"


# Plain-English notes for the Cover sheet: rows the filings do not tag, and what the model leaves out.
def _notes(st: dict) -> list[str]:
    missing = [line["label"] for rows in st["rows"].values() for line in rows
               if line["formula"] is None and st["values"][line["key"]][-1] is None]
    notes = ["Per-share amounts and share counts of older years are as filed, so they are not restated for later "
             "stock splits.", "Segments are not included: SEC company facts carry no segment breakdown."]
    if missing:
        notes.insert(0, "Not tagged in the latest 10-K (left blank, ratios that need them too): " + ", ".join(missing) + ".")
    return notes


# Build the company model end to end and save it. Raises ValueError for companies the model cannot cover.
def run(query: str, user_id: str) -> dict:
    company = sec.resolve(query)
    if "candidates" in company:
        return {"status": "choose", "query": query, "candidates": company["candidates"]}
    filings = sec.filings(company["cik"])
    st = statements.build(sec.company_facts(company["cik"]))
    years, values = st["years"], st["values"]
    inputs = analysis.dcf_inputs(values)
    valuation = {"inputs": inputs, "skip": inputs["skip"]} if "skip" in inputs else {"inputs": inputs, **analysis.dcf(inputs)}
    rel = (releases.read(filings["earnings"][0], company["name"], user_id) if filings["earnings"]
           else {"items": [], "error": "no earnings release (8-K Item 2.02) found"})
    built = datetime.now(timezone.utc)
    run_id = f"{company['ticker']}-{built:%Y%m%d%H%M%S}"
    result = {"status": "done", "run_id": run_id, "query": query, "company": company, "filings": filings,
              "built_at": built.isoformat(timespec="seconds"), "statements": st, "ratios": analysis.ratios(values, len(years)),
              "valuation": valuation, "releases": rel, "tieouts": tieout.run(values, years), "notes": _notes(st)}
    path = runs_dir(user_id) / f"{run_id}.xlsx"
    expected = workbook.build(result, path)
    result["tieouts"].append(tieout.workbook_check(path, expected))
    (runs_dir(user_id) / f"{run_id}.json").write_text(json.dumps(result, default=str), encoding="utf-8")
    return result


# The saved workbook of one run (404 when it is not this user's).
def workbook_path(user_id: str, run_id: str) -> Path:
    path = runs_dir(user_id) / f"{engine.safe_id(run_id)}.xlsx"
    if not path.exists():
        raise engine.NotFound("no such company run")
    return path
