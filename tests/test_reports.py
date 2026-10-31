# tests/test_reports.py
"""A report has a section per tile in reading order; its AI summary is kept only when every number in
it matches the data, and the summary can be switched off (no AI call at all). Fake LLM, no network."""
import asyncio

import pytest

from wealthos import engine
from wealthos import reports
from wealthos.services import llm_client as llm
from wealthos.ingest import parse_file

BANK = "Date,Narration,Debit,Credit\n05/04/2026,Rent,25000,\n06/04/2026,Salary,,90000\n10/05/2026,Groceries,4000,\n"


@pytest.fixture()
def board(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DATA_ROOT", tmp_path / "users")
    p = tmp_path / "bank.csv"
    p.write_text(BANK, encoding="utf-8")
    engine.save_tables("u", parse_file(p))
    from wealthos import templates
    return templates.apply("u", "default", "spending").id


def test_report_sections_and_checked_summary(board, monkeypatch):
    said = iter(["You spent ₹29,000 and received ₹90,000, so you kept ₹61,000.",   # all numbers in the data
                 "You spent ₹31,500 this year."])                                  # invented number
    calls = []

    async def fake_llm(system, user, **kw):
        calls.append(user)
        return next(said)
    monkeypatch.setattr(llm, "call_llm", fake_llm)

    good = asyncio.run(reports.build_report("u", board, subtitle="Household"))
    assert good["verified"] and good["narrative"].startswith("You spent ₹29,000") and good["subtitle"] == "Household"
    titles = [s["title"] for s in good["sections"]]
    assert titles[:4] == ["Spent", "Money in", "Saved", "Savings rate"] and len(titles) == 6  # top-left first
    spent = good["sections"][0]
    assert spent["rows"] == [{"total_spend": 29000.0}] and spent["provenance"]["badge"] == "trusted"

    bad = asyncio.run(reports.build_report("u", board))
    assert not bad["verified"] and "31,500" not in bad["narrative"] and "replaced" in bad["warnings"][0]

    off = asyncio.run(reports.build_report("u", board, narrative=False))
    assert off["narrative"] == "" and len(calls) == 2  # no third AI call
