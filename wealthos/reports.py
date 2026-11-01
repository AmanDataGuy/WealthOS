"""
# reports.py - a dashboard as a report you can print or send

A report is the dashboard's tiles, freshly computed for a period, in order (top to bottom, left to right), with:
- a header: title, an optional subtitle (company name, "Prepared for ..."), the period and when it was generated;
- an optional **narrative**: one AI call that sees only the computed rows and writes 3-5 sentences. Every number in it
  is checked against those rows (`verify.check_numbers`); if one doesn't match, the narrative is replaced by a plain
  sentence and a warning, never "fixed up";
- a provenance appendix: for each section, the SQL, period, source files and data fingerprint.

Text tiles become paragraphs. The web app renders the report and prints it to PDF; Excel comes from the dashboard export.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from wealthos import dashboards
from wealthos.verify import check_numbers

NARRATOR = """You write the summary paragraph of a business / personal-finance report.
Use ONLY numbers that appear in the data (you may round them as shown, or give a simple difference or % change of two
of them). Rupee amounts: ₹ with Indian grouping (₹1,23,456) or lakh/crore. 3-5 sentences, plain words, no headings.
Say what stands out (largest, smallest, changes). Never give buy/sell/hold advice or predictions."""


# Build the report for a dashboard (refreshed for `time`, or its own period). `narrative` turns the AI summary on.
async def build_report(user_id: str, dashboard_id: str, time: str | None = None, narrative: bool = True,
                       subtitle: str = "") -> dict:
    spec = await asyncio.to_thread(dashboards.get, user_id, dashboard_id)
    snap = await asyncio.to_thread(dashboards.refresh, user_id, dashboard_id, time)
    sections = []
    for tile in sorted(spec.tiles.values(), key=lambda t: (t.layout.y, t.layout.x)):
        result = snap["tiles"].get(tile.id, {})
        sections.append({"tile_id": tile.id, "title": tile.title or tile.id, "kind": tile.kind, "text": tile.text,
                         "rows": result.get("rows", []), "error": result.get("error"),
                         "warnings": result.get("warnings", []), "provenance": result.get("provenance")})
    report = {"title": spec.title, "subtitle": subtitle.strip()[:200], "period": snap["time"],
              "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "sections": sections,
              "narrative": "", "verified": True, "warnings": [],
              "disclaimer": "Analytics on your own data, not investment advice (WealthOS is not SEBI-registered)."}
    data = [{"section": s["title"], "rows": s["rows"][:20]} for s in sections if s["rows"]]
    if narrative and data:
        report["narrative"], report["verified"], report["warnings"] = await _narrate(user_id, spec.title, snap["time"], data)
    return report


# One AI call for the summary, checked number by number. Returns (text, verified, warnings).
async def _narrate(user_id: str, title: str, period: str, data: list[dict]) -> tuple[str, bool, list[str]]:
    from wealthos.services.llm_client import call_llm

    raw = await call_llm(NARRATOR, f"Report: {title}\nPeriod: {period}\nData: {json.dumps(data, default=str)[:12000]}",
                         max_tokens=400, temperature=0, user_id=user_id)
    if not raw:
        return "", True, ["the summary could not be written (the language model did not answer); the numbers below are complete"]
    bad = check_numbers(raw, [], "INR", rows=[r for d in data for r in d["rows"]])
    if bad:
        return (f"This report covers {len(data)} section(s) for the period {period}; the figures are in the sections below.",
                False, [f"the written summary had numbers not in the data ({', '.join(bad[:3])}), so it was replaced"])
    return raw.strip(), True, []
