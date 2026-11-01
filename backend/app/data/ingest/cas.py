"""
# cas.py - read a CAS (Consolidated Account Statement) PDF

A CAS is the one statement Indian investors get for everything they hold:
- **CAMS / KFintech CAS**: every mutual fund folio, its units, value and every purchase / redemption.
- **NSDL / CDSL CAS**: demat holdings (shares, ETFs, bonds) plus mutual funds.

The PDF is password protected (usually PAN in capitals, or as the email says). The `casparser` library does the
reading; this file only turns its output into our standard shapes:
- `holdings`: what you own on the statement date (scheme / share, ISIN, units, cost, value).
- `trades`: mutual fund purchases and redemptions with units and NAV (needed later for XIRR).

The password is used once to open the file and is never stored.
"""
from __future__ import annotations

from pathlib import Path

from . import ExtractedTable, SourceRef


# Read a CAS PDF into holdings (+ mutual fund trades); None if the file is not a CAS or the password is wrong.
def read_cas(path: Path, password: str, src: SourceRef) -> list[ExtractedTable] | None:
    try:
        import casparser
        data = casparser.read_cas_pdf(str(path), password)  # plain dicts
    except Exception:
        return None  # not a CAS (or a wrong password): the caller tries the next reader
    as_of = str((data.get("statement_period") or {}).get("to") or "") or None
    holdings, trades, warnings = [], [], [str(w) for w in data.get("parse_warnings") or []]

    for folio in data.get("folios") or []:  # CAMS / KFintech layout
        for s in folio.get("schemes") or []:
            val = s.get("valuation") or {}
            units = _f(s.get("close"))
            holdings.append({"as_of": str(val.get("date") or as_of), "symbol_or_scheme": s.get("scheme"), "isin": s.get("isin"),
                             "asset_class": "mf", "quantity": units, "avg_cost": _avg(_f(val.get("cost")), units),
                             "value": _f(val.get("value")), "folio": folio.get("folio")})
            for t in s.get("transactions") or []:
                if t.get("units"):  # skip rows without units (dividend notes, stamp duty lines)
                    trades.append({"trade_date": str(t.get("date")), "symbol": s.get("scheme"), "isin": s.get("isin"),
                                   "side": "buy" if _f(t["units"]) > 0 else "sell", "quantity": abs(_f(t["units"])),
                                   "price": _f(t.get("nav")), "amount": _f(t.get("amount")), "type": t.get("type")})

    for acc in data.get("accounts") or []:  # NSDL / CDSL layout
        for e in acc.get("equities") or []:
            holdings.append({"as_of": as_of, "symbol_or_scheme": e.get("symbol") or e.get("name"), "isin": e.get("isin"),
                             "asset_class": "equity", "quantity": _f(e.get("num_shares")), "avg_cost": None, "value": _f(e.get("value")),
                             "folio": acc.get("client_id")})
        for m in acc.get("mutual_funds") or []:
            holdings.append({"as_of": as_of, "symbol_or_scheme": m.get("name"), "isin": m.get("isin"), "asset_class": "mf",
                             "quantity": _f(m.get("balance")), "avg_cost": _f(m.get("avg_cost")), "value": _f(m.get("value")),
                             "folio": m.get("folio")})
        if acc.get("bonds"):
            warnings.append(f"{len(acc['bonds'])} bond holding(s) not imported yet")

    tables = [ExtractedTable(shape="holdings", columns=list(holdings[0]) if holdings else [], rows=holdings,
                             source=src, status="parsed", warnings=warnings)]
    if trades:
        tables.append(ExtractedTable(shape="trades", columns=list(trades[0]), rows=trades, source=src, status="parsed"))
    return tables


# Average cost per unit = total cost / units (None when either is missing).
def _avg(cost, units):
    return round(cost / units, 4) if cost and units else None


# casparser gives Decimal numbers; turn them into plain floats (None stays None).
def _f(v):
    return None if v is None else float(v)
