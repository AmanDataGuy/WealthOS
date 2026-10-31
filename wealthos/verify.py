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


# A number in text: digits with Western or Indian commas and decimals, optionally followed by a size word.
_NUMBER_IN_TEXT = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{2,3})+|\d+)(\.\d+)?\s*(%|lakhs?|lacs?|crores?|cr|k|thousand|million|mn|billion|bn)?\b", re.I)
_SIZE = {"lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5, "crore": 1e7, "crores": 1e7, "cr": 1e7, "k": 1e3,
         "thousand": 1e3, "million": 1e6, "mn": 1e6, "billion": 1e9, "bn": 1e9}


# Every number written in a piece of text, as plain floats ("₹1.2 lakh" -> 120000.0). Used for numbers the user typed.
def numbers_in(text: str) -> list[float]:
    out = []
    for m in _NUMBER_IN_TEXT.finditer(text or ""):
        n = float(m.group(1).replace(",", "") + (m.group(2) or ""))
        out.append(n * _SIZE.get((m.group(3) or "").lower(), 1.0))
    return out


# Numbers in the text that don't match any computed value (or a sum / difference / % change of two). [] = all good.
def check_numbers(text: str, values: list[float]) -> list[str]:
    allowed = {float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)}
    vals = list(allowed)[:60]  # ponytail: pairwise derivations are O(n^2), so only over the first 60 values
    for i, a in enumerate(vals):  # simple derivations the narrative may state
        for b in vals[i + 1:]:
            allowed |= {a + b, abs(a - b)}
            if b:
                allowed.add((a - b) / abs(b) * 100)
            if a:
                allowed |= {(b - a) / abs(a) * 100, b / a * 100}
    bad = []
    for m in _NUMBER_IN_TEXT.finditer(text):
        whole, decimals, size = m.group(1), m.group(2) or "", (m.group(3) or "").lower()
        n = float(whole.replace(",", "") + decimals)
        if not decimals and not size and "," not in whole and (n <= 31 or 1900 <= n <= 2100):
            continue  # ponytail: small counts ("3 months") and years are not checked; they rarely carry money
        scale = _SIZE.get(size, 1.0)
        n *= scale
        half_step = 0.5 * 10 ** -len(decimals[1:]) * scale if decimals else 0.5 * scale  # rounding shown in the text
        if not any(abs(n - v) <= max(0.005 * abs(v), half_step) or abs(n - abs(v)) <= max(0.005 * abs(v), half_step)
                   for v in allowed):
            bad.append(m.group(0).strip())
    return bad
