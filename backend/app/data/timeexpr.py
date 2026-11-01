"""
# timeexpr.py - turn words like "this month" or "last FY" into exact start and end dates

Dashboards store *words*, not dates, so pressing Refresh always means "now":
a tile saved with `mtd` shows this month to date today and next month's to date next month.

| Expression | Meaning (today = 2026-10-26) |
|---|---|
| `today` | 2026-10-26 .. 2026-10-26 |
| `mtd` | month to date: 2026-10-01 .. 2026-10-26 |
| `last_month` | 2026-09-01 .. 2026-09-30 |
| `qtd` | quarter to date (calendar quarter): 2026-10-01 .. 2026-10-26 |
| `ytd` | calendar year to date: 2026-01-01 .. 2026-10-26 |
| `fy` | Indian financial year to date (April-March): 2026-04-01 .. 2026-10-26 |
| `last_fy` | previous Indian financial year: 2025-04-01 .. 2026-03-31 |
| `trailing_12m` | the last 12 months: 2025-10-27 .. 2026-10-26 |
| `all` | everything: 1900-01-01 .. today |
| `2026-01-01..2026-03-31` | exactly those dates |

"Today" is the date in India (Asia/Kolkata), whatever the server's clock zone is.
Both dates are inclusive.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

EXPRESSIONS = ["today", "mtd", "last_month", "qtd", "ytd", "fy", "last_fy", "trailing_12m", "all"]


# Today's date in India.
def today_ist() -> date:
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


# The April 1 that starts the Indian financial year containing day d.
def fy_start(d: date) -> date:
    return date(d.year if d.month >= 4 else d.year - 1, 4, 1)


# Resolve an expression to (start, end), both inclusive. Raises ValueError for anything unknown.
def resolve(expr: str, today: date | None = None) -> tuple[date, date]:
    t = today or today_ist()
    e = expr.strip().lower()
    if ".." in e:
        a, b = (date.fromisoformat(x.strip()) for x in e.split("..", 1))
        if a > b:
            raise ValueError(f"start {a} is after end {b}")
        return a, b
    if e == "today":
        return t, t
    if e == "mtd":
        return t.replace(day=1), t
    if e == "last_month":
        end = t.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if e == "qtd":
        return date(t.year, 3 * ((t.month - 1) // 3) + 1, 1), t
    if e == "ytd":
        return date(t.year, 1, 1), t
    if e == "fy":
        return fy_start(t), t
    if e == "last_fy":
        start = fy_start(t)
        return date(start.year - 1, 4, 1), start - timedelta(days=1)
    if e == "trailing_12m":
        try:
            start = t.replace(year=t.year - 1) + timedelta(days=1)
        except ValueError:  # today is Feb 29: a year ago has no Feb 29
            start = date(t.year - 1, 3, 1)
        return start, t
    if e == "all":
        return date(1900, 1, 1), t
    raise ValueError(f"unknown time expression '{expr}'. Use one of {EXPRESSIONS} or 'YYYY-MM-DD..YYYY-MM-DD'.")
