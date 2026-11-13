"""
# verify.py - safety checks before anything touches the user's data

## guard_sql: is this SQL safe to run?
The assistant may write its own SQL for questions the metric list doesn't cover ("Exploratory" answers).
Before it runs, it must pass all of these, or it is rejected with a plain reason:

1. Exactly **one** statement, and it is a **SELECT** (no INSERT/DELETE/COPY/ATTACH/INSTALL/SET/PRAGMA...).
2. No functions that read files, URLs, settings or the system (`read_csv`, `glob`, `getenv`, ...).
3. Every table and column exists in this user's data (checked with sqlglot against the real schema).
4. At most 5,000 rows come back (a LIMIT is wrapped around it).

This is the second lock, not the only one: the query also runs on a locked DuckDB connection that can only read
this user's own folder (see `engine.open_readonly`). DuckDB's docs say to treat SQL like code, so we keep both.

## check_numbers: did the assistant's sentence invent a number?
Every number the assistant writes must be one the engine actually returned (or a simple sum, difference or
% change of two of them), within 0.5% or the rounding shown. "₹1.2 lakh" counts as 1,20,000. Years and small
counts like "3 months" are ignored. If any number fails, the sentence is thrown away and the user sees the table.
"""
from __future__ import annotations

import re

import duckdb
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify

MAX_ROWS = 5000
# Function names that reach outside the data: file/URL readers, settings, secrets, extension loading.
BLOCKED_FUNCTION = re.compile(r"^(read_|glob|getenv|current_setting|query|sniff_|parquet_|iceberg|delta_|postgres_|"
                              r"mysql_|sqlite_|duckdb_|pragma_|which_secret|load|install|system|shell|http)", re.I)


# Raised when a query is not allowed; the message says why in plain words.
class SqlRejected(ValueError):
    pass


# Check one SQL query against the user's schema {table: {column: type}} and return it wrapped with a row limit.
def guard_sql(sql: str, schema: dict[str, dict[str, str]], limit: int = MAX_ROWS) -> str:
    sql = sql.strip().rstrip(";").strip()
    try:
        statements = duckdb.connect().extract_statements(sql)  # DuckDB's own parser: how many statements, what kind
    except duckdb.Error as e:
        raise SqlRejected(f"not valid SQL: {e}") from None
    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        raise SqlRejected("only a single SELECT query is allowed")

    try:
        tree = sqlglot.parse_one(sql, dialect="duckdb")
    except sqlglot.errors.ParseError as e:
        raise SqlRejected(f"could not read the query: {e}") from None
    if tree.find(exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Command):
        raise SqlRejected("only a single SELECT query is allowed")  # a write hidden inside a CTE or subquery
    for fn in tree.find_all(exp.Func):
        name = fn.name if isinstance(fn, exp.Anonymous) else fn.sql_name()
        if BLOCKED_FUNCTION.match(name or ""):
            raise SqlRejected(f"function '{name}' is not allowed (it reaches outside your data)")

    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    known = {t.lower() for t in schema}
    for t in tree.find_all(exp.Table):
        if t.name.lower() not in known | ctes:
            raise SqlRejected(f"unknown table '{t.name}'. Your tables: {sorted(schema)}")
    try:
        qualify(tree, schema=schema, dialect="duckdb", validate_qualify_columns=True)
    except sqlglot.errors.OptimizeError as e:
        raise SqlRejected(f"unknown column: {e}") from None
    return f"SELECT * FROM ({sql}) AS q LIMIT {int(limit)}"


# A number in text: digits with Western or Indian commas and decimals, optionally followed by a size word. The
# digits may sit right after a currency word ("Rs2000", "INR 5,000") but not after other letters ("FY26", "Q1").
# Thousands may also be grouped with a space ("20 833.33", as models sometimes write); a false join only makes a
# sentence fail the check, never pass it.
_NUMBER_IN_TEXT = re.compile(r"(?:(?<![\w.])|(?<=rs)|(?<=rs\.)|(?<=inr)|(?<=usd))"
                             r"(\d{1,3}(?:,\d{2,3})+|\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?!\d)|\d+)(\.\d+)?"
                             r"\s*(%|lakhs?|lacs?|crores?|cr|k|thousand|million|mn|billion|bn)?\b", re.I)
_SIZE = {"lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5, "crore": 1e7, "crores": 1e7, "cr": 1e7, "k": 1e3,
         "thousand": 1e3, "million": 1e6, "mn": 1e6, "billion": 1e9, "bn": 1e9}
_INDIAN_SIZES = {"lakh", "lakhs", "lac", "lacs", "crore", "crores", "cr"}
# Currency written just before or just after a number.
_INR_BEFORE = re.compile(r"(?:₹|\b(?:rs\.?|inr))\s?$", re.I)
_USD_BEFORE = re.compile(r"(?:\$|\busd)\s?$", re.I)
_INR_AFTER = re.compile(r"^\s*(?:rupees?|inr)\b", re.I)
_USD_AFTER = re.compile(r"^\s*(?:dollars?|usd)\b", re.I)
# A plain number (no currency, %, size word, comma or decimals) that counts or dates something is not checked:
# "3 months", "top 5", "[p. 12]", "in 2026", "FY 2025-26". Dates are blanked out before the scan.
_COUNT_AFTER = re.compile(r"^\s*(?:months?|days?|years?|weeks?|quarters?|hours?|minutes?|times|trades?|transactions?|"
                          r"stocks?|holdings?|funds?|schemes?|companies|company|merchants?|categories|category|items?|"
                          r"rows?|entries|accounts?|payments?|files?|tables?|metrics?|sections?|pages?)\b", re.I)
_COUNT_BEFORE = re.compile(r"(?:\b(?:top|bottom|first|last|page|pages|note|section|item|rank|q|h)|\bp\.)\s*$", re.I)
_MONTHS = (r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
           r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?")
_YEAR_BEFORE = re.compile(rf"\b(?:fy|in|of|since|from|to|year|by|until|during|before|after|{_MONTHS})\s*$", re.I)
_DATES = re.compile(rf"\b\d{{4}}-\d{{2}}-\d{{2}}\b|\b\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{2,4}}\b"
                    rf"|\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})(?:,?\s+\d{{4}})?\b"
                    rf"|\b(?:{_MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b"
                    rf"|\[p\.?\s*\d+\]", re.I)
# Models write dates with non-breaking hyphens (U+2011) and numbers with narrow spaces (U+202F): read them plain.
_PLAIN = str.maketrans(dict.fromkeys(map(chr, (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2212)), "-")
                       | dict.fromkeys(map(chr, (0xA0, 0x202F, 0x2009)), " "))
MAX_DERIVE = 20  # pairwise sums / differences / % changes are taken over at most this many values per group


# Every number written in a piece of text, as plain floats ("₹1.2 lakh" -> 120000.0). Used for numbers the user typed.
def numbers_in(text: str) -> list[float]:
    out = []
    for m in _NUMBER_IN_TEXT.finditer(text or ""):
        n = float(re.sub(r"[, \u00a0\u202f]", "", m.group(1)) + (m.group(2) or ""))
        out.append(n * _SIZE.get((m.group(3) or "").lower(), 1.0))
    return out


# One number check_numbers refused. It compares equal to the text it was written as; `.reason` says why.
class NumberIssue(str):
    reason: str

    def __new__(cls, text: str, reason: str):
        issue = super().__new__(cls, text)
        issue.reason = reason  # unmatched | currency_mismatch
        return issue


# A real number (bool is an int in Python, but True is not 1 rupee).
def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# What a narration may state from one group of related numbers: the numbers, optionally their total, and pairwise
# sums, differences and % changes over the first MAX_DERIVE of them.
def _derived(group: list[float], total: bool) -> set[float]:
    out = set(group) | ({sum(group)} if total and group else set())
    head = group[:MAX_DERIVE]
    for i, a in enumerate(head):
        for b in head[i + 1:]:
            out |= {a + b, abs(a - b)}
            if b:
                out.add((a - b) / abs(b) * 100)
            if a:
                out |= {(b - a) / abs(a) * 100, b / a * 100}
    return out


# Is this plain number a count, an ordinal or a year rather than an amount?
def _is_count_or_year(text: str, m: re.Match) -> bool:
    before, after, n = text[:m.start()], text[m.end():], int(m.group(1))
    if _COUNT_AFTER.match(after) or _COUNT_BEFORE.search(before) or re.search(r"(?<!\d)(?:19|20)\d{2}[-–]$", before):
        return True  # the last one is the "26" of "2025-26"
    return 1900 <= n <= 2100 and bool(_YEAR_BEFORE.search(before) or re.match(r"[-–]\d{2}\b", after))


# Numbers in the text that the results don't support. [] = all good. Each issue is the number as written, with a
# reason: "unmatched" (not a value, nor a total / sum / difference / % change within one row, one column, or
# `values`), or "currency_mismatch" ("$" on rupee results, "₹" or "lakh" on dollar results).
# `unit` is the currency of the results: "INR", "USD", or "mixed" (no currency check).
def check_numbers(text: str, values: list[float], unit: str = "mixed",
                  rows: list[dict] | None = None) -> list[NumberIssue]:
    rows = rows or []
    allowed = _derived([float(v) for v in values if _is_number(v)], total=False)
    for row in rows:  # "you kept 61,000 of 90,000": income minus spend, two columns of one row
        allowed |= _derived([float(v) for v in row.values() if _is_number(v)], total=False)
    totals = []
    for col in {k for r in rows for k in r}:  # "12,000 more than TCS": two rows of one column, or the column total
        cells = [float(r[col]) for r in rows if _is_number(r.get(col))]
        allowed |= _derived(cells, total=True)
        totals += [sum(cells)] if cells else []
    allowed |= _derived(totals, total=True)  # "₹3,000 long-term + ₹1,500 short-term = ₹4,500": two column totals
    text = _DATES.sub(" ", (text or "").translate(_PLAIN))
    bad = []
    for m in _NUMBER_IN_TEXT.finditer(text):
        whole, decimals, size = m.group(1), m.group(2) or "", (m.group(3) or "").lower()
        before, after = text[max(0, m.start() - 5):m.start()], text[m.end():m.end() + 10]
        inr = bool(_INR_BEFORE.search(before) or _INR_AFTER.match(after)) or size in _INDIAN_SIZES
        usd = bool(_USD_BEFORE.search(before) or _USD_AFTER.match(after))
        if (unit == "INR" and usd) or (unit == "USD" and inr):
            bad.append(NumberIssue(m.group(0).strip(), "currency_mismatch"))
            continue
        if not (inr or usd or size or decimals or re.search(r"[, \u00a0\u202f]", whole)) and _is_count_or_year(text, m):
            continue
        scale = _SIZE.get(size, 1.0)
        n = float(re.sub(r"[, \u00a0\u202f]", "", whole) + decimals) * scale
        half_step = 0.5 * 10 ** -len(decimals[1:]) * scale if decimals else 0.5 * scale  # rounding shown in the text
        if not any(abs(n - v) <= max(0.005 * abs(v), half_step) or abs(n - abs(v)) <= max(0.005 * abs(v), half_step)
                   for v in allowed):
            bad.append(NumberIssue(m.group(0).strip(), "unmatched"))
    return bad
