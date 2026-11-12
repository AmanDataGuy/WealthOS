# tests/test_cas.py
"""
# test_cas.py - mutual fund / demat CAS statements

`casparser` only reads real CAMS / KFintech / NSDL PDFs, so these tests replace its `read_cas_pdf` with a small
synthetic dict in its documented shape (folios -> schemes -> transactions; accounts -> equities / mutual funds)
and check what `cas.read_cas` makes of it. No real statements.
"""
from decimal import Decimal

import casparser
import pytest

from app.data.ingest import SourceRef
from app.data.ingest.cas import read_cas

SRC = SourceRef(file="cas.pdf", content_hash="x")
CAMS = {
    "statement_period": {"from": "01-Apr-2025", "to": "31-Mar-2026"},
    "folios": [{"folio": "1234567/89", "schemes": [{
        "scheme": "Sample Flexi Cap Fund - Direct Growth", "isin": "INF000000001", "close": Decimal("150.000"),
        "valuation": {"date": "2026-03-31", "value": Decimal("18000.00"), "cost": Decimal("15000.00")},
        "transactions": [
            {"date": "2025-05-10", "type": "PURCHASE_SIP", "units": Decimal("100.000"), "nav": Decimal("90.00"), "amount": Decimal("9000.00")},
            {"date": "2025-06-10", "type": "STAMP_DUTY_TAX", "units": None, "nav": None, "amount": Decimal("0.45")},
            {"date": "2025-08-10", "type": "PURCHASE", "units": Decimal("80.000"), "nav": Decimal("100.00"), "amount": Decimal("8000.00")},
            {"date": "2026-01-15", "type": "REDEMPTION", "units": Decimal("-30.000"), "nav": Decimal("110.00"), "amount": Decimal("-3300.00")},
        ]}]}],
    "accounts": [{"client_id": "IN300000", "equities": [{"symbol": "SAMPLECO", "isin": "INE000000001",
                                                          "num_shares": Decimal("10"), "value": Decimal("25000")}]}],
}


# Pretend casparser read this dict from the PDF.
@pytest.fixture
def cas_returns(monkeypatch):
    def use(data):
        monkeypatch.setattr(casparser, "read_cas_pdf", lambda path, password: data)
    return use


# A CAMS-style CAS becomes holdings (MF + demat equity) and MF trades; stamp-duty rows without units are skipped.
def test_cas_holdings_and_trades(cas_returns):
    cas_returns(CAMS)
    holdings, trades = read_cas("cas.pdf", "ABCDE1234F", SRC)
    assert holdings.shape == "holdings" and trades.shape == "trades"
    mf, eq = holdings.rows
    assert (mf["symbol_or_scheme"], mf["quantity"], mf["avg_cost"], mf["value"]) == \
        ("Sample Flexi Cap Fund - Direct Growth", 150.0, 100.0, 18000.0)  # avg cost = 15000 / 150
    assert (eq["asset_class"], eq["quantity"], eq["as_of"]) == ("equity", 10.0, "31-Mar-2026")
    assert [(t["side"], t["quantity"], t["price"]) for t in trades.rows] == [("buy", 100.0, 90.0), ("buy", 80.0, 100.0), ("sell", 30.0, 110.0)]
    assert {"trade_date", "symbol", "isin", "side", "quantity", "price"} <= set(trades.columns)


# A wrong password (casparser raises) gives None, so the caller tries the next reader.
def test_cas_wrong_password(monkeypatch):
    def boom(path, password):
        raise casparser.exceptions.IncorrectPasswordError("Incorrect PDF password!")
    monkeypatch.setattr(casparser, "read_cas_pdf", boom)
    assert read_cas("cas.pdf", "wrong", SRC) is None


# A CAS with no folios and no accounts gives one empty holdings table, not an error.
def test_cas_empty(cas_returns):
    cas_returns({"statement_period": {"to": "31-Mar-2026"}, "folios": [], "accounts": []})
    [holdings] = read_cas("cas.pdf", "ABCDE1234F", SRC)
    assert holdings.shape == "holdings" and holdings.rows == []
