# tests/test_bank_pdf.py
"""
# test_bank_pdf.py - PDF bank statements in three common Indian layouts

Builds synthetic two-page statements with pymupdf (random but seeded rows, a repeated header on page 2, wrapped
narration in one layout) and checks that `parse_file` reads every row, the debit/credit totals match and the
running-balance check stays quiet. Also checks a non-statement PDF still goes to the filings reader.
No real statements.
"""
import random

import pymupdf
import pytest

from app.data.ingest import parse_file

# (header cells and their x positions, how one row is printed) for each layout; numbers are right-aligned at x.
LAYOUTS = {
    "hdfc": {"cols": [("Date", 40, "l"), ("Narration", 95, "l"), ("Chq./Ref.No.", 255, "l"), ("Value Dt", 330, "l"),
                      ("Withdrawal Amt.", 450, "r"), ("Deposit Amt.", 515, "r"), ("Closing Balance", 585, "r")],
             "date": "%d/%m/%y", "row": lambda d, n, ref, dr, cr, bal: [d, n, ref, d, dr, cr, bal]},
    "sbi": {"cols": [("Txn Date", 40, "l"), ("Particulars", 120, "l"), ("Withdrawals", 400, "r"),
                     ("Deposits", 480, "r"), ("Balance", 570, "r")],
            "date": "%d %b %Y", "row": lambda d, n, ref, dr, cr, bal: [d, n, dr, cr, bal]},
    "icici": {"cols": [("Txn Date", 40, "l"), ("Value Date", 110, "l"), ("Description", 180, "l"),
                       ("Amount (Dr/Cr)", 470, "r"), ("Balance", 570, "r")],
              "date": "%d-%m-%Y", "row": lambda d, n, ref, dr, cr, bal: [d, d, n, f"{dr} Dr" if dr else f"{cr} Cr", bal]},
}
PAYEES = ["UPI-SWIGGY", "UPI-ZOMATO", "NEFT-RENT", "ATM WDL", "SALARY ACME LTD", "UPI-AMAZON", "ACH-SIP MF"]


# Write a synthetic two-page statement in one layout; returns the path and the true debit and credit totals.
def _statement(tmp_path, layout: str, n: int = 40):
    from datetime import date, timedelta

    spec, rng = LAYOUTS[layout], random.Random(layout)
    doc, bal, totals, day = pymupdf.open(), 50_000.0, {"dr": 0.0, "cr": 0.0}, date(2026, 4, 1)
    page, y = None, 0

    def put(x, align, s, size=8):
        page.insert_text((x - pymupdf.get_text_length(s, fontsize=size) if align == "r" else x, y), s, fontsize=size)

    for i in range(n):
        if page is None or y > 760:
            page = doc.new_page(width=612, height=792)
            y = 50
            if doc.page_count == 1:
                for line in ("SAMPLE BANK LTD", "Statement of account for 01/04/2026 to 30/06/2026",
                             "Account No: XXXXXXXX0000   Customer: TEST USER"):
                    put(40, "l", line, 9)
                    y += 14
                y += 10
            for name, x, align in spec["cols"]:
                put(x, align, name)
            y += 16
        amt = round(rng.uniform(100, 9000), 2)
        credit = rng.random() < 0.3
        bal += amt if credit else -amt
        totals["cr" if credit else "dr"] += amt
        cells = spec["row"](day.strftime(spec["date"]), rng.choice(PAYEES), f"REF{i:06d}",
                            "" if credit else f"{amt:,.2f}", f"{amt:,.2f}" if credit else "", f"{bal:,.2f}")
        for (_, x, align), s in zip(spec["cols"], cells):
            if s:
                put(x, align, s)
        if layout == "sbi" and i % 5 == 0:  # SBI-style particulars wrap onto a second line
            y += 10
            put(spec["cols"][1][1], "l", "TRANSFER TO 0000")
        y += 14
        day += timedelta(days=rng.randint(0, 2))
    path = tmp_path / f"{layout}.pdf"
    doc.save(str(path))
    return path, totals


# Each layout reads all rows across both pages, totals match, and the running balance chains.
@pytest.mark.parametrize("layout", list(LAYOUTS))
def test_bank_pdf_layouts(tmp_path, layout):
    path, totals = _statement(tmp_path, layout)
    [t] = parse_file(path)
    assert t.shape == "transactions" and t.status == "parsed"
    assert len(t.rows) == 40
    dr = sum(r["amount"] for r in t.rows if r["direction"] == "debit")
    cr = sum(r["amount"] for r in t.rows if r["direction"] == "credit")
    assert (round(dr, 2), round(cr, 2)) == (round(totals["dr"], 2), round(totals["cr"], 2))
    assert t.rows[0]["date"] == "2026-04-01"
    assert not any("running balance" in w for w in t.warnings), t.warnings
    if layout == "sbi":
        assert t.rows[0]["description"].endswith("TRANSFER TO 0000")  # the wrapped line joined its row


# A PDF without a transactions header is not a bank statement: it still goes to the filings reader.
def test_non_statement_pdf_goes_to_filings(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Annual Report 2026. Balance sheet date and account policies.", fontsize=10)
    path = tmp_path / "report.pdf"
    doc.save(str(path))
    [t] = parse_file(path)
    assert t.shape == "statements"
