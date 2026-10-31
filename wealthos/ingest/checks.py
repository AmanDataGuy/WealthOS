"""
# checks.py - quick sanity checks on every imported table

They never change the data; they only add plain-English warnings the user sees next to the upload:

| Check | Catches |
|---|---|
| Duplicates | The same trade or transaction imported twice (overlapping downloads) |
| Running balance | Bank rows where previous balance ± amount ≠ printed balance (a missing or misread row) |
| Date gaps | No bank entries for 45+ days (a missing statement page or month) |
| Digit pattern (Benford) | Amounts whose first digits look unusual (typing errors, made-up numbers). Often fine for small or rounded data |
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import date

from . import ExtractedTable


# Run every check that applies to this table's shape and return the warnings.
def run(t: ExtractedTable) -> list[str]:
    if t.shape == "trades":
        return _duplicates(t.rows, ("trade_id",) if any(r.get("trade_id") for r in t.rows)
                           else ("trade_date", "symbol", "side", "quantity", "price"))
    if t.shape == "transactions":
        return (_duplicates(t.rows, ("date", "amount", "direction", "description")) + _balance(t.rows)
                + _gaps(t.rows) + _benford([r["amount"] for r in t.rows]))
    return []


# Rows that are identical on the key columns.
def _duplicates(rows: list[dict], key: tuple) -> list[str]:
    counts = Counter(tuple(r.get(k) for k in key) for r in rows)
    extra = sum(n - 1 for n in counts.values() if n > 1)
    return [f"{extra} possible duplicate row(s) (same {', '.join(key)})"] if extra else []


# Each printed balance should equal the previous balance minus debits plus credits (to the paisa).
def _balance(rows: list[dict]) -> list[str]:
    with_bal = [r for r in rows if r.get("balance") is not None]
    bad = []
    for prev, cur in zip(with_bal, with_bal[1:]):
        change = cur["amount"] if cur["direction"] == "credit" else -cur["amount"]
        if abs(prev["balance"] + change - cur["balance"]) > 0.01:
            bad.append(cur["source_row"])
    if not bad:
        return []
    return [f"running balance does not add up at {len(bad)} row(s) (first: file row {bad[0]}): "
            "a row may be missing or misread, or the file is not in date order"]


# Gaps of 45+ days between consecutive bank entries.
def _gaps(rows: list[dict]) -> list[str]:
    days = sorted({date.fromisoformat(r["date"]) for r in rows})
    gaps = [(a, b) for a, b in zip(days, days[1:]) if (b - a).days >= 45]
    return [f"no entries between {a} and {b}: is a statement page or month missing?" for a, b in gaps[:3]]


# First-digit test (Benford's law). Needs 100+ amounts; chi-square above 20.09 is unusual at the 1% level (8 d.o.f.).
def _benford(amounts: list[float]) -> list[str]:
    firsts = [int(str(f"{a:e}")[0]) for a in amounts if a and a >= 1]
    if len(firsts) < 100:
        return []
    n, seen = len(firsts), Counter(firsts)
    chi2 = sum((seen[d] - n * math.log10(1 + 1 / d)) ** 2 / (n * math.log10(1 + 1 / d)) for d in range(1, 10))
    return [f"amounts have an unusual first-digit pattern (Benford chi-square {chi2:.0f} > 20). "
            "Often fine for small or rounded data; worth a glance."] if chi2 > 20.09 else []
