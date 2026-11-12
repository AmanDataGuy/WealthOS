"""
# tabular.py - read a CSV or Excel export and recognise what it is

Works for broker tradebooks (Zerodha, Groww, Upstox ...), bank / credit-card statements, holdings exports and daily
price files (Date / Open / High / Low / Close / Adj Close / Volume; one file per stock is fine).

1. **Find the header row.** Exports often have a few title lines ("Statement for ...") above the table, so we
   pick the row whose cells look most like known column names.
2. **Recognise the shape.** Each column name is compared with lists of names banks and brokers use
   ("Trade Date", "trade_date", "Txn Date", "Narration", "Withdrawal Amt." ...). If all the must-have columns of a
   shape are found, the table is that shape.
3. **Clean the values.** Dates become YYYY-MM-DD (Indian day-first dates), numbers like "1,23,456.00" or "₹500"
   become 123456.0 / 500.0, "B"/"Buy" becomes buy, debit/credit columns become one positive amount + a direction.
4. **Otherwise** the table comes back as shape "unknown" with a short profile of each column, so the user can
   map it by hand (Data tab → Map columns).

Rows that say "Total ..." are dropped (they are sums, not transactions) and reported as a warning.
Plain name matching - no AI.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

import pandas as pd

from app.data.finx.tables import parse_number

from . import ExtractedTable, SourceRef

# For each shape: the columns it must have, and the names files use for each of our standard columns.
SHAPES = {
    "trades": {
        "required": ["trade_date", "symbol", "side", "quantity", "price"],
        "names": {
            "trade_date": ["trade date", "date", "execution date and time", "order execution time", "trade time", "date time"],
            "symbol":     ["symbol", "tradingsymbol", "stock name", "scrip", "scrip name", "instrument", "stock", "company name",
                           "scheme name"],
            "isin":       ["isin"],
            "exchange":   ["exchange"],
            "segment":    ["segment"],
            "side":       ["trade type", "type", "buy sell", "buy or sell", "transaction type", "side", "action"],
            "quantity":   ["quantity", "qty", "shares", "units"],
            "price":      ["price", "trade price", "avg price", "average price", "rate"],
            "trade_id":   ["trade id", "trade no"],
            "order_id":   ["order id", "exchange order id", "order no"],
        },
    },
    "transactions": {
        "required": ["date", "description"],  # plus a debit/credit pair or an amount column (checked below)
        "names": {
            "date":        ["date", "txn date", "transaction date", "value date", "posting date", "tran date", "value dt"],
            "description": ["description", "narration", "particulars", "remarks", "details", "transaction details", "transaction remarks",
                            "transaction description"],
            "debit":       ["debit", "withdrawal", "withdrawal amt", "withdrawals", "withdrawal amount", "debit amount", "dr amount",
                            "withdrawal amount inr", "withdrawal dr", "dr"],
            "credit":      ["credit", "deposit", "deposit amt", "deposits", "deposit amount", "credit amount", "cr amount",
                            "deposit amount inr", "deposit cr", "cr"],
            "amount":      ["amount", "transaction amount", "amount inr", "amount rs", "amount dr cr", "amount in rs"],
            "drcr":        ["dr cr", "cr dr", "debit credit", "type"],
            "balance":     ["balance", "closing balance", "running balance", "balance inr", "bal"],
        },
    },
    "holdings": {
        "required": ["symbol_or_scheme", "quantity"],
        "names": {
            "symbol_or_scheme": ["instrument", "symbol", "scheme", "scheme name", "stock name", "company name", "security", "tradingsymbol"],
            "isin":             ["isin"],
            "quantity":         ["qty", "quantity", "units", "balance units", "quantity available", "shares"],
            "avg_cost":         ["avg cost", "average price", "avg price", "buy average", "average cost", "avg buy price",
                                 "average buy price"],
            "value":            ["cur val", "current value", "market value", "value", "valuation", "present value", "closing value"],
        },
    },
    "prices": {  # daily bars; without a symbol column the file name is the symbol (one file per stock)
        "required": ["ts", "close"],
        "names": {
            "ts":        ["date", "ts", "timestamp", "datetime", "trade date"],
            "symbol":    ["symbol", "ticker", "tradingsymbol", "scrip", "instrument"],
            "open":      ["open", "open price"],
            "high":      ["high", "high price"],
            "low":       ["low", "low price"],
            "close":     ["close", "close price", "closing price", "nav"],
            "adj_close": ["adj close", "adjusted close", "adj close price"],
            "volume":    ["volume", "shares traded", "total traded quantity"],
        },
    },
}
KNOWN_NAMES = {n for s in SHAPES.values() for names in s["names"].values() for n in names}


# "Withdrawal Amt." -> "withdrawal amt", "trade_date" -> "trade date": lower case, punctuation to spaces.
def _norm(name) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name).lower()).strip()


# Read every table in a CSV or Excel file (one per sheet) and recognise each.
def read_tables(path: Path, src: SourceRef) -> list[ExtractedTable]:
    if path.suffix.lower() == ".csv":
        sheets = {None: _read_csv_grid(path)}
    else:
        sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=str, keep_default_na=False)
    tables = []
    for sheet, grid in sheets.items():
        table = _read_grid(grid, src.model_copy(update={"sheet": sheet}))
        if table:
            tables.append(table)
    return tables


# Read a CSV as a plain grid. Title lines above the table have fewer cells, so rows are padded to the widest;
# bank exports are not always UTF-8, so fall back to Latin-1 (which can read any byte).
def _read_csv_grid(path: Path) -> pd.DataFrame:
    for enc in ("utf-8-sig", "latin-1"):
        try:
            with open(path, newline="", encoding=enc) as f:
                rows = list(csv.reader(f))
            break
        except UnicodeDecodeError:
            continue
    width = max((len(r) for r in rows), default=0)
    return pd.DataFrame([r + [""] * (width - len(r)) for r in rows])


# The row that looks most like a header: the most cells that are known column names (at least two).
def _header_row(grid: pd.DataFrame) -> int | None:
    best, best_hits = None, 1
    for i in range(min(len(grid), 30)):
        hits = sum(_norm(v) in KNOWN_NAMES for v in grid.iloc[i])
        if hits > best_hits:
            best, best_hits = i, hits
    return best


# Turn one sheet into a recognised table (or an "unknown" one with a profile); None if the sheet is empty.
def _read_grid(grid: pd.DataFrame, src: SourceRef) -> ExtractedTable | None:
    h = _header_row(grid)
    if h is None:
        h = next((i for i in range(len(grid)) if any(str(v).strip() for v in grid.iloc[i])), None)
        if h is None:
            return None
    headers = [str(v).strip() or f"column_{j + 1}" for j, v in enumerate(grid.iloc[h])]
    records, totals = [], 0
    for i in range(h + 1, len(grid)):
        cells = [str(v).strip() for v in grid.iloc[i]]
        if not any(cells):
            continue
        first_text = next((c for c in cells if c), "")
        if re.match(r"(grand\s+)?totals?\b", first_text, re.I):
            totals += 1
            continue
        records.append({**dict(zip(headers, cells)), "source_row": i + 1})  # 1-based row number in the file
    warnings = [f"removed {totals} 'Total' row(s) (they are sums, not entries)"] if totals else []

    for shape in ("trades", "transactions", "holdings", "prices"):
        colmap = _match_columns(headers, SHAPES[shape])
        if colmap is not None:
            rows, bad = clean(shape, colmap, records, default_symbol=Path(src.file).stem.upper())
            if bad:
                warnings.append(f"{bad} row(s) skipped because a required value could not be read")
            if shape == "transactions" and not {"debit", "credit", "drcr"} & colmap.keys() and rows \
                    and all(r["direction"] == "credit" for r in rows):
                warnings.append("every amount is positive and there is no debit/credit column, so all rows were read as "
                                "money IN; if these are expenses, add a Dr/Cr column or use negative amounts for spends")
            return ExtractedTable(shape=shape, columns=list(colmap) + ["source_row"], rows=rows, source=src,
                                  status="parsed", warnings=warnings)
    return ExtractedTable(shape="unknown", columns=headers, rows=records, source=src, status="needs_mapping",
                          warnings=warnings + ["columns not recognised: use Map columns (Data tab) to say what each one is"],
                          profile=_profile(headers, records))


# Every standard column we can recognise by name, even if some must-have ones are missing: {standard: file column}.
def match_partial(headers: list[str], spec: dict) -> dict[str, str]:
    colmap, used = {}, set()
    for std, names in spec["names"].items():
        for h in headers:
            if h not in used and _norm(h) in names:
                colmap[std] = h
                used.add(h)
                break
    return colmap


# The must-have standard columns this mapping still lacks (a bank statement also needs some money column).
def missing_required(colmap: dict[str, str], spec: dict) -> list[str]:
    missing = [r for r in spec["required"] if r not in colmap]
    if "description" in spec["required"] and not ({"debit", "credit", "amount"} & colmap.keys()):
        missing.append("debit/credit or amount")
    return missing


# Which file column is each standard column? None if a must-have column is missing.
def _match_columns(headers: list[str], spec: dict) -> dict[str, str] | None:
    colmap = match_partial(headers, spec)
    return None if missing_required(colmap, spec) else colmap


# Read a date the Indian way (day first): "05/04/2026" -> "2026-04-05"; ISO "2026-04-05" stays as it is.
def _date(v: str) -> str | None:
    d = pd.to_datetime(v, dayfirst=not re.match(r"\d{4}-\d{2}-\d{2}", str(v).strip()), errors="coerce")
    return None if pd.isna(d) else d.date().isoformat()


# Read a money/quantity cell with finx's number reader (handles 1,23,456 / ₹ / brackets); None if empty or bad.
def _num(v: str) -> float | None:
    v = re.sub(r"\s*(cr|dr)\.?$", "", str(v).strip(), flags=re.I).replace(" ", "")
    return parse_number(v) if v else None


# Rename and clean every row for its shape; returns (clean rows, number of rows skipped). Also used by mapping.py.
def clean(shape: str, colmap: dict[str, str], records: list[dict], default_symbol: str | None = None) -> tuple[list[dict], int]:
    out, bad = [], 0
    for rec in records:
        raw = {std: rec.get(col, "") for std, col in colmap.items()}
        row = {"source_row": rec["source_row"]}
        if shape == "trades":
            side = raw["side"].strip().lower()
            row |= {"trade_date": _date(raw["trade_date"]), "symbol": raw["symbol"] or None,
                    "side": "buy" if side.startswith("b") else "sell" if side.startswith("s") else None,
                    "quantity": _num(raw["quantity"]), "price": _num(raw["price"])}
            row |= {k: raw[k] or None for k in ("isin", "exchange", "segment", "trade_id", "order_id") if k in raw}
            required = ("trade_date", "symbol", "side", "quantity", "price")
        elif shape == "transactions":
            amount, direction = _money(raw)
            row |= {"date": _date(raw["date"]), "description": raw["description"], "amount": amount, "direction": direction,
                    "balance": _num(raw["balance"]) if "balance" in raw else None}
            required = ("date", "amount", "direction")
        elif shape == "prices":  # with an "Adj Close" column, close/open are split- and dividend-adjusted
            close, adj = _num(raw["close"]), _num(raw.get("adj_close", ""))
            factor = adj / close if adj and close else 1.0
            row |= {"ts": _date(raw["ts"]), "symbol": (raw.get("symbol") or default_symbol or "").upper() or None,
                    **{k: (_num(raw.get(k, "")) or 0) * factor or None for k in ("open", "high", "low")},
                    "close": adj or close, "volume": _num(raw.get("volume", "")), "adjusted": True if adj else None}
            required = ("ts", "symbol", "close")
        else:
            row |= {"symbol_or_scheme": raw["symbol_or_scheme"] or None, "quantity": _num(raw["quantity"]),
                    "isin": raw.get("isin") or None, "avg_cost": _num(raw.get("avg_cost", "")), "value": _num(raw.get("value", ""))}
            required = ("symbol_or_scheme", "quantity")
        if any(row[k] is None for k in required):
            bad += 1
            continue
        out.append(row)
    return out, bad


# One positive amount and its direction, from separate debit/credit columns or one amount (+ Dr/Cr) column.
def _money(raw: dict) -> tuple[float | None, str | None]:
    debit, credit = _num(raw.get("debit", "")), _num(raw.get("credit", ""))
    if debit:
        return abs(debit), "debit"
    if credit:
        return abs(credit), "credit"
    amount = _num(raw.get("amount", ""))
    if amount is None:
        return None, None
    marker = (raw.get("drcr", "") + " " + raw.get("amount", "")).lower()
    is_debit = amount < 0 or re.search(r"\b(dr|debit)\b", marker) is not None
    return abs(amount), "debit" if is_debit else "credit"


# What each column of an unknown table looks like: how often it is empty, how many distinct values, examples.
def _profile(headers: list[str], records: list[dict]) -> dict:
    prof = {}
    for h in headers:
        vals = [r.get(h, "") for r in records]
        filled = [v for v in vals if v]
        nums = [n for n in (_num(v) for v in filled) if n is not None]
        prof[h] = {"empty_pct": round(100 * (1 - len(filled) / max(len(vals), 1)), 1), "distinct": len(set(filled)),
                   "examples": list(dict.fromkeys(filled))[:3],
                   "looks_numeric": bool(filled) and len(nums) / len(filled) > 0.9,
                   "looks_like_date": bool(filled) and sum(_date(v) is not None for v in filled[:20]) / min(len(filled), 20) > 0.9}
    return prof
