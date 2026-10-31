# tests/test_extraction.py
"""finx (PDF -> verified JSON) wired into WealthOS. Builds a tiny income statement PDF in code, so no fixture files."""
import pymupdf
import pytest

from wealthos.finx.pipeline import run
from wealthos.finx.tables import find_unit, parse_number

ROWS = [("Revenue", "1,000", "800"), ("Cost of revenue", "400", "300"), ("Gross profit", "600", "500"),
        ("Research and development", "100", "90"), ("Selling, general and administrative", "100", "60"),
        ("Total operating expenses", "200", "150"), ("Operating income", "400", "350"),
        ("Provision for income taxes", "100", "50"), ("Net income", "300", "300")]


# Make a one-page income statement PDF from (label, this year, last year) rows.
def _income_statement_pdf(path, rows=ROWS, unit_line="(In millions, except per share amounts)") -> str:
    doc = pymupdf.open()
    page = doc.new_page()

    def right(x, y, s, size=10):  # right-aligned numbers form finx's columns
        page.insert_text((x - pymupdf.get_text_length(s, fontsize=size), y), s, fontsize=size)

    page.insert_text((72, 72), "CONSOLIDATED STATEMENTS OF INCOME", fontsize=12, fontname="hebo")
    page.insert_text((72, 90), unit_line, fontsize=9)
    right(420, 110, "Year Ended December 31,", 9)
    right(400, 124, "2026", 9)
    right(480, 124, "2025", 9)
    y = 144
    for label, a, b in rows:
        page.insert_text((72, y), label, fontsize=10)
        right(400, y, a)
        right(480, y, b)
        y += 16
    doc.save(str(path))
    return str(path)


# A clean statement is found and every value passes the arithmetic checks, on both PDF readers.
def test_statement_found_and_verified(tmp_path):
    res = run(_income_statement_pdf(tmp_path / "is.pdf"))
    st = res["statements"]["income_statement"]
    assert st["unit"] == "millions"
    assert st["rows"][0]["label"] == "Revenue" and st["rows"][0]["values"] == [1000.0, 800.0]
    assert res["assurance"]["certified_pct"] == 100.0
    assert all(c["passed"] for c in res["checks"])


# A wrong number is never marked "verified".
def test_planted_wrong_number_is_caught(tmp_path):
    rows = [r if r[0] != "Net income" else ("Net income", "310", "300") for r in ROWS]  # 400 - 100 != 310
    res = run(_income_statement_pdf(tmp_path / "bad.pdf", rows))
    # No subtotal pattern fits the bad column, so its values are left "unverified" (never silently "verified").
    by_label = {r["label"]: r for r in res["statements"]["income_statement"]["rows"]}
    assert by_label["Net income"]["verified"] == [False, True]
    assert by_label["Net income"]["status"] == "unverified"
    assert res["assurance"]["certified_pct"] < 100


# Western numbers and units still read the same way.
def test_number_and_unit_parsing():
    assert parse_number("1,234,567") == 1234567.0
    assert parse_number("(1,234)") == -1234.0
    assert parse_number("1,23") is None  # not a valid number in either system
    assert find_unit(["(In millions)"]) == ("millions", 1e6)


# Indian digit grouping, rupee signs and lakh/crore units are understood.
@pytest.mark.parametrize("raw, value", [
    ("1,23,456", 123456.0), ("12,34,56,789", 123456789.0), ("(1,23,456)", -123456.0),
    ("₹1,234", 1234.0), ("₹1,23,456.50", 123456.5), ("Rs. 1,234", 1234.0), ("Rs.1,23,456", 123456.0), ("INR 5,000", 5000.0),
])
def test_indian_numbers(raw, value):
    assert parse_number(raw) == value


# Lakh and crore headings give the right multiplier.
@pytest.mark.parametrize("text, unit", [
    ("(₹ in crore)", ("crore", 1e7)), ("Amount in ₹ Crores", ("crore", 1e7)), ("(Rs. in lakhs)", ("lakh", 1e5)),
    ("(₹ crore)", ("crore", 1e7)), ("(in Rs. lacs)", ("lakh", 1e5)),
])
def test_indian_units(text, unit):
    assert find_unit([text]) == unit


# An Indian-style statement (Rs. in crore, 1,23,456 grouping) is found and verified end to end.
def test_indian_statement_pdf(tmp_path):
    rows = [("Revenue from operations", "1,23,456", "1,00,000"), ("Cost of materials consumed", "23,456", "20,000"),
            ("Gross profit", "1,00,000", "80,000"), ("Employee benefits expense", "40,000", "30,000"),
            ("Operating income", "60,000", "50,000"), ("Tax expense", "15,000", "12,500"), ("Net income", "45,000", "37,500")]
    res = run(_income_statement_pdf(tmp_path / "in.pdf", rows, "(Rs. in crore)"))
    st = res["statements"]["income_statement"]
    assert (st["unit"], st["scale"]) == ("crore", 1e7)
    assert st["rows"][0]["values"] == [123456.0, 100000.0]
    assert res["assurance"]["certified_pct"] == 100.0
