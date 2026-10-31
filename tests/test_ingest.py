# tests/test_ingest.py
"""File import (ingest/), sanity checks, and the SEC period fix. Synthetic files only, no network."""
import pandas as pd

from wealthos.tools import SEC_FIELDS, _sec_annual_values
from wealthos.ingest import parse_file

ZERODHA = ("symbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,trade_id,order_id,order_execution_time\n"
           "INFY,INE009A01021,2026-04-03,NSE,EQ,EQ,buy,false,10,1450.50,T1,O1,x\n"
           "INFY,INE009A01021,2026-05-10,NSE,EQ,EQ,sell,false,5,1520.00,T2,O2,x\n"
           "INFY,INE009A01021,2026-05-10,NSE,EQ,EQ,sell,false,5,1520.00,T2,O2,x\n")
BANK = ("HDFC BANK Statement of account\nAccount No: XXXX1234\n\n"
        "Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance\n"
        "01/04/26,UPI-SWIGGY,1,01/04/26,450.00,,\"1,00,000.00\"\n"
        "02/04/26,SALARY ACME,2,02/04/26,,\"85,000.00\",\"1,85,000.00\"\n"
        "20/06/26,ATM WDL,3,20/06/26,\"2,000.00\",,\"1,80,000.00\"\n"
        "Total,,,,\"2,450.00\",\"85,000.00\",\n")


# Write text to a file in the test folder and return its path.
def _file(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# True when every key/value in `expected` is in `row`.
def _has(row: dict, expected: dict) -> bool:
    return row.items() >= expected.items()


# A Zerodha tradebook is recognised as trades; ISO dates stay as written; the duplicate trade id is flagged.
def test_broker_tradebook(tmp_path):
    [t] = parse_file(_file(tmp_path, "tb.csv", ZERODHA))
    assert t.shape == "trades" and len(t.rows) == 3
    assert _has(t.rows[0], {"trade_date": "2026-04-03", "symbol": "INFY", "side": "buy", "quantity": 10.0, "price": 1450.5})
    assert any("duplicate" in w for w in t.warnings)


# A bank CSV with title lines, Indian numbers, a Total row, a wrong balance and a gap is read and flagged correctly.
def test_bank_statement(tmp_path):
    [t] = parse_file(_file(tmp_path, "bank.csv", BANK))
    assert t.shape == "transactions" and len(t.rows) == 3
    assert _has(t.rows[0], {"date": "2026-04-01", "amount": 450.0, "direction": "debit", "balance": 100000.0})
    assert t.rows[1]["direction"] == "credit" and t.rows[1]["amount"] == 85000.0
    joined = " ".join(t.warnings)
    assert "Total" in joined and "no entries between" in joined
    assert "running balance" in joined  # 1,85,000 - 2,000 should be 1,83,000; the file says 1,80,000


# A holdings Excel sheet with ₹ values is read as holdings.
def test_holdings_xlsx(tmp_path):
    p = tmp_path / "h.xlsx"
    pd.DataFrame({"Instrument": ["TCS"], "Qty.": [3], "Avg. cost": ["3,900.00"], "Cur. val": ["₹12,300"]}).to_excel(p, index=False)
    [t] = parse_file(p)
    assert t.shape == "holdings"
    assert _has(t.rows[0], {"symbol_or_scheme": "TCS", "quantity": 3.0, "avg_cost": 3900.0, "value": 12300.0})


# Columns we don't know come back as "unknown" with a profile, never guessed.
def test_unknown_table(tmp_path):
    [t] = parse_file(_file(tmp_path, "w.csv", "When,What,Money\n05/04/2026,Rent,25000\n"))
    assert t.shape == "unknown" and t.status == "needs_mapping"
    assert t.profile["Money"]["looks_numeric"] and t.profile["When"]["looks_like_date"]


# SEC figures pick the year by its real end date: a prior-year comparative inside the latest 10-K (same "fy")
# must not be returned as this year's figure.
def test_sec_period_by_end_date():
    entries = [
        {"start": "2023-01-30", "end": "2024-01-28", "val": 60922e6, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-02-26", "accn": "A2"},
        {"start": "2024-01-29", "end": "2025-01-26", "val": 130497e6, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-02-26", "accn": "A2"},
    ]
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": entries}}}}}
    kind, tags = SEC_FIELDS["total_revenue"]
    newest = _sec_annual_values(facts, kind, tags)[0]
    assert (newest["end"], newest["value"]) == ("2025-01-26", 130497e6)


# Only Google Sheets links are fetched (the export URL is built from the id), a private sheet's sign-in
# page is refused, and the CSV that comes back reads like an upload. Mocked HTTP, no network.
def test_google_sheet_csv(tmp_path):
    import asyncio

    import httpx
    import pytest

    from wealthos.ingest.sheets import export_url, fetch_sheet_csv

    assert export_url("https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMn/edit#gid=42") == \
        "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMn/export?format=csv&gid=42"
    assert export_url("https://docs.google.com/spreadsheets/d/e/2PACX-1vAbCdEfGh/pubhtml") == \
        "https://docs.google.com/spreadsheets/d/e/2PACX-1vAbCdEfGh/pub?output=csv"
    for bad in ("http://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMn", "https://evil.com/spreadsheets/d/1AbCdEfGhIjKlMn",
                "https://docs.google.com.evil.com/spreadsheets/d/1AbCdEfGhIjKlMn", "https://docs.google.com/document/d/1AbCdEfGhIjKlMn",
                "file:///etc/passwd"):
        with pytest.raises(ValueError):
            export_url(bad)

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if "PRIVATE" in str(request.url):
            return httpx.Response(200, headers={"content-type": "text/html"}, text="<html>Sign in</html>")
        return httpx.Response(200, headers={"content-type": "text/csv", "content-disposition": 'attachment; filename="Bank - Sheet1.csv"'},
                              text="Date,Narration,Debit,Credit\n05/04/2026,Rent,25000,\n")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    data, name = asyncio.run(fetch_sheet_csv("https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMn/edit", 10_000, client))
    assert name == "Bank - Sheet1.csv" and seen[0].endswith("/export?format=csv")
    path = tmp_path / name
    path.write_bytes(data)
    assert parse_file(path)[0].shape == "transactions"
    with pytest.raises(ValueError, match="Anyone with the link"):
        asyncio.run(fetch_sheet_csv("https://docs.google.com/spreadsheets/d/PRIVATE12345/edit", 10_000,
                                    httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    with pytest.raises(ValueError, match="larger than"):
        asyncio.run(fetch_sheet_csv("https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMn/edit", 10,
                                    httpx.AsyncClient(transport=httpx.MockTransport(handler))))
