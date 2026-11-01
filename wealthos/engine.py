"""
# engine.py - where every number in WealthOS is computed

The rule: **the AI never produces a number.** Every figure a user sees comes from here, from SQL on their own data,
and comes back with its *provenance*: the exact SQL, which uploaded files it read, and a data fingerprint.

## Where the data lives (one folder per user, so "delete my data" = delete the folder)
```
data/users/<user_id>/
  raw/<sha256>/<file>              the original upload (+ tables.json, what was read from it)
  datasets/<shape>/<id>.parquet    clean rows, one file per uploaded table
  datasets/<shape>/<id>.json       what that table is: source file, row count, columns, warnings
  mappings.json                    column mappings the user confirmed (re-uploads of the same layout map themselves)
  workspaces/<ws>/                 connection.json (connections.py), semantic.json (semantic.py)
```
Shapes: `transactions` (bank/card), `trades` (buys/sells), `holdings` (what you own), `prices`, `statements` (filings).
Tables copied from a connected database keep their own name (`orders`, `customers` ...) as their "shape".

## How a query runs
1. `open_readonly(user, workspace)` makes an in-memory DuckDB with one view per shape over that workspace's Parquet files, then
   locks it: it can read only that user's folder, no internet, no other files, no extensions, settings frozen.
2. **Metrics** (`run_metric`): the named calculations in `metrics.yaml`, filtered by a time expression
   ("fy", "mtd" ... see `timeexpr.py`) and optional dimensions / filters. Badge: **trusted**.
   Besides real columns, results can be split by `month` or `year` (taken from the metric's date column).
3. **Free SQL** (`run_sql`): checked by `verify.guard_sql` first, then run on the locked connection. Badge: **exploratory**.
4. Every query stops after 10 seconds.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

import duckdb
import pandas as pd
import sqlglot
import yaml
from pydantic import BaseModel
from sqlglot import exp

from wealthos import timeexpr
from wealthos.verify import guard_sql

DATA_ROOT = Path("data") / "users"
METRICS_FILE = Path(__file__).parent / "metrics.yaml"
DATE_COLUMNS = {"transactions": "date", "trades": "trade_date", "holdings": "as_of", "prices": "ts"}
QUERY_TIMEOUT_S = 10
FILTER_OPS = {"=", "!=", ">", ">=", "<", "<=", "in", "contains"}
TIME_GRAINS = {"month": "%Y-%m", "year": "%Y"}  # extra "split by" options built from the metric's date column
LONG_TERM_DAYS = 365  # listed equity: held more than 12 months = long term (Indian tax rule; MF/debt rules differ)


# ── Models ──────────────────────────────────────────────────────────────────────────────

# Something the user asked for doesn't exist (an API 404). A plain KeyError / IndexError is a bug and stays a 500.
class NotFound(LookupError):
    pass


# A question for the metric registry: which metrics, split by what, over which period, with which filters.
class MetricQuery(BaseModel):
    metrics: list[str]
    dimensions: list[str] = []
    time: str = "fy"
    filters: list[dict] = []  # [{"column": "symbol", "op": "=", "value": "INFY"}]


# Where a result came from: the SQL, the files it read, the data fingerprint, and how much to trust it.
class Provenance(BaseModel):
    sql: str
    params: list = []
    datasets: list[dict]
    data_version: str
    badge: Literal["trusted", "exploratory"]
    period: dict | None = None


# A computed answer: the rows, their provenance, and anything the user should know.
class Result(BaseModel):
    rows: list[dict]
    provenance: Provenance
    warnings: list[str] = []


# ── Storage ─────────────────────────────────────────────────────────────────────────────

# This user's folder (the user id is reduced to a plain name, so it can't point anywhere else).
def user_dir(user_id: str) -> Path:
    return DATA_ROOT / Path(str(user_id)).name


# A workspace id reduced to letters, digits, "-" and "_" (it becomes part of file names, so no paths allowed).
def safe_id(text: str) -> str:
    return "".join(c for c in str(text) if c.isalnum() or c in "-_")[:64] or "default"


# Save every parsed table as a Parquet dataset (+ its description) in a workspace. Returns the saved descriptions.
def save_tables(user_id: str, tables: list, workspace_id: str = "default") -> list[dict]:
    saved = []
    for t in tables:
        if t.status != "parsed" or t.shape == "unknown" or not t.rows:
            continue
        sheet = "".join(c if c.isalnum() else "_" for c in (t.source.sheet or "main"))
        dataset_id = f"{t.source.content_hash[:16]}_{sheet}_{t.shape}"
        folder = user_dir(user_id) / "datasets" / t.shape
        folder.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(t.rows)
        if t.shape == "holdings" and "as_of" not in df:
            df["as_of"] = timeexpr.today_ist().isoformat()  # a holdings export with no date = "as of upload day"
        col = DATE_COLUMNS.get(t.shape)
        if col in df:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
        df["source_file"], df["dataset_id"] = t.source.file, dataset_id
        df.to_parquet(folder / f"{dataset_id}.parquet", index=False)
        meta = {"dataset_id": dataset_id, "workspace_id": safe_id(workspace_id), "shape": t.shape,
                "source": t.source.model_dump(), "rows": len(df),
                "columns": list(df.columns), "warnings": t.warnings,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        (folder / f"{dataset_id}.json").write_text(json.dumps(meta, ensure_ascii=False, default=str), encoding="utf-8")
        saved.append(meta)
    return saved


# Delete one dataset: all of its Parquet files and its description.
def remove_dataset(user_id: str, meta: dict) -> None:
    folder = user_dir(user_id) / "datasets" / meta["shape"]
    for name in (meta.get("files") or [f"{meta['dataset_id']}.parquet"]) + [f"{meta['dataset_id']}.json"]:
        (folder / Path(name).name).unlink(missing_ok=True)


# Every dataset this user has (only one workspace's when workspace_id is given), newest first.
def list_datasets(user_id: str, workspace_id: str | None = None) -> list[dict]:
    metas = [json.loads(p.read_text(encoding="utf-8")) for p in (user_dir(user_id) / "datasets").glob("*/*.json")]
    if workspace_id is not None:
        metas = [m for m in metas if m.get("workspace_id", "default") == safe_id(workspace_id)]
    return sorted(metas, key=lambda m: m["created_at"], reverse=True)


# ── Querying ────────────────────────────────────────────────────────────────────────────

# An in-memory DuckDB with one view per shape over this user's Parquet files (one workspace's, when given),
# locked down to the user's folder.
def open_readonly(user_id: str, workspace_id: str | None = None) -> duckdb.DuckDBPyConnection:
    root = (user_dir(user_id) / "datasets").resolve()
    con = duckdb.connect(":memory:")
    by_shape: dict[str, list[Path]] = defaultdict(list)
    for m in list_datasets(user_id, workspace_id):
        for name in m.get("files") or [f"{m['dataset_id']}.parquet"]:  # a live feed keeps several part files
            by_shape[m["shape"]].append(root / m["shape"] / Path(name).name)
    for shape, paths in sorted(by_shape.items()):
        files = sorted(str(p).replace("\\", "/").replace("'", "''") for p in paths if p.exists())
        if files:
            listing = ", ".join(f"'{f}'" for f in files)
            con.execute(f'CREATE VIEW "{shape}" AS SELECT * FROM read_parquet([{listing}], union_by_name = true)')
    allowed = str(root).replace("\\", "/").replace("'", "''")
    for setting in (f"SET allowed_directories = ['{allowed}']", "SET enable_external_access = false",
                    "SET autoload_known_extensions = false", "SET autoinstall_known_extensions = false",
                    "SET allow_community_extensions = false", "SET memory_limit = '1GB'", "SET threads = 2",
                    "SET lock_configuration = true"):
        con.execute(setting)
    return con


# Column names and types of every table this user has: {table: {column: type}}.
def schema(con: duckdb.DuckDBPyConnection) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = defaultdict(dict)
    for table, column, dtype in con.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns ORDER BY table_name, ordinal_position").fetchall():
        out[table][column] = dtype
    return dict(out)


# Run SQL with a time limit (DuckDB is interrupted after QUERY_TIMEOUT_S) and return JSON-friendly rows.
def _execute(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> list[dict]:
    timer = threading.Timer(QUERY_TIMEOUT_S, con.interrupt)
    timer.start()
    try:
        cur = con.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        return [{c: _plain(v) for c, v in zip(cols, row)} for row in cur.fetchall()]
    except duckdb.InterruptException:
        raise TimeoutError(f"query took longer than {QUERY_TIMEOUT_S}s and was stopped") from None
    except duckdb.Error as e:  # a wrong type in a filter, a bad cast ...: a plain refusal, never a crash
        raise ValueError(f"the query could not run on your data: {str(e).splitlines()[0]}") from None
    finally:
        timer.cancel()


# Decimal -> float, date -> "YYYY-MM-DD", so results go straight into JSON.
def _plain(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


# The datasets behind some shapes, and one fingerprint for that exact set of files (changes when data changes).
def _provenance_data(user_id: str, shapes: set[str], workspace_id: str | None = None) -> tuple[list[dict], str]:
    used = [{"dataset_id": m["dataset_id"], "shape": m["shape"], "file": m["source"]["file"], "rows": m["rows"],
             "content_hash": m["source"]["content_hash"]} for m in list_datasets(user_id, workspace_id) if m["shape"] in shapes]
    # dataset ids + row counts: a feed that gained rows gets a new fingerprint even though its id stays the same
    fingerprint = hashlib.sha256("|".join(sorted(f"{d['dataset_id']}:{d['rows']}" for d in used)).encode()).hexdigest()[:16]
    return used, fingerprint


# All metric definitions: metrics.yaml, plus the workspace's own metrics from the Semantic Builder (they win).
def load_metrics(user_id: str | None = None, workspace_id: str | None = None) -> dict[str, dict]:
    metrics = yaml.safe_load(METRICS_FILE.read_text(encoding="utf-8"))
    if user_id and workspace_id:
        path = user_dir(user_id) / "workspaces" / safe_id(workspace_id) / "semantic.json"
        if path.exists():
            metrics |= json.loads(path.read_text(encoding="utf-8")).get("metrics", {})
    return metrics


# Compute registry metrics for a MetricQuery (the "trusted" path), on one workspace's data when given.
def run_metric(user_id: str, q: MetricQuery, today: date | None = None, workspace_id: str | None = None) -> Result:
    defs = load_metrics(user_id, workspace_id)
    unknown = [m for m in q.metrics if m not in defs]
    if unknown or not q.metrics:
        raise ValueError(f"unknown metric(s) {unknown}. Available: {sorted(defs)}")
    shapes = {defs[m]["shape"] for m in q.metrics}
    if len(shapes) > 1:
        raise ValueError("these metrics read different tables; ask for them separately")
    shape = shapes.pop()
    # A date range written as a filter ("date between A and B") is the period: move it into `time`.
    # A [a, b] pair from a list or an "a..b" string, else None.
    two = lambda v: v if isinstance(v, list) and len(v) == 2 else str(v).split("..") if ".." in str(v) else None
    span = [f for f in q.filters if "date" in str(f.get("column")) and f.get("op") == "between" and two(f.get("value"))]
    if span:
        a, b = two(span[0]["value"])
        q = q.model_copy(update={"time": f"{a}..{b}", "filters": [f for f in q.filters if f is not span[0]]})
    start, end = timeexpr.resolve(q.time, today)
    period = {"time": q.time, "start": start.isoformat(), "end": end.isoformat()}

    con = open_readonly(user_id, workspace_id)
    cols = schema(con).get(shape)
    if not cols:
        raise NotFound(f"no {shape} data yet: upload a file that contains {shape} first")
    # "symbol" (trades, prices) and "symbol_or_scheme" (holdings) both name the instrument: accept either on any table.
    # The other name for the instrument column, if this table only has that one.
    same = lambda c: {"symbol": "symbol_or_scheme", "symbol_or_scheme": "symbol"}.get(c) if c not in cols else None
    q = q.model_copy(update={"dimensions": [same(d) if same(d) in cols else d for d in q.dimensions],
                             "filters": [{**f, "column": same(f.get("column"))} if same(f.get("column")) in cols else f
                                         for f in q.filters]})
    bad = [d for d in q.dimensions if d not in cols and (d not in TIME_GRAINS or "python" in defs[q.metrics[0]])]
    if bad:
        raise ValueError(f"cannot split by {bad}; columns available: {sorted(cols)}")

    python_metrics = [m for m in q.metrics if "python" in defs[m]]
    if python_metrics:
        if len(q.metrics) > 1:
            raise ValueError(f"ask for {python_metrics[0]} on its own")
        rows, sql, params, warnings = PYTHON_METRICS[defs[python_metrics[0]]["python"]](con, start, end, q, today)
    else:
        rows, sql, params, warnings = _sql_metrics(con, defs, q, shape, cols, start, end)
    datasets, fingerprint = _provenance_data(user_id, {shape} | ({"holdings"} if "xirr" in q.metrics else set()), workspace_id)
    return Result(rows=rows, warnings=warnings,
                  provenance=Provenance(sql=sql, params=params, datasets=datasets, data_version=fingerprint,
                                        badge="trusted", period=period))


# Build and run the SQL for one or more SQL-defined metrics on the same table.
def _sql_metrics(con, defs, q: MetricQuery, shape: str, cols: dict, start: date, end: date):
    date_col = defs[q.metrics[0]]["date"]
    dims = [f'"{d}"' if d in cols else f"strftime(\"{date_col}\", '{TIME_GRAINS[d]}') AS \"{d}\"" for d in q.dimensions]
    group = [f'"{d}"' if d in cols else f"strftime(\"{date_col}\", '{TIME_GRAINS[d]}')" for d in q.dimensions]
    where, params = [], []
    if any(defs[m]["time_semantics"] == "point_in_time" for m in q.metrics):
        where.append(f'"{date_col}" = (SELECT MAX("{date_col}") FROM "{shape}" WHERE "{date_col}" <= ?)')  # latest snapshot
        params.append(end)
    else:
        where.append(f'"{date_col}" BETWEEN ? AND ?')
        params += [start, end]
    for f in q.filters:
        column, op, value = f.get("column"), str(f.get("op", "=")).lower(), f.get("value")
        if column not in cols or op not in FILTER_OPS:
            raise ValueError(f"bad filter {f}: column must be one of {sorted(cols)}, op one of {sorted(FILTER_OPS)}")
        if op == "in":
            values = value if isinstance(value, list) else [value]
            where.append(f'"{column}" IN ({", ".join("?" for _ in values)})')
            params += values
        elif op == "contains":  # text anywhere in the column, any case ("rent" matches "UPI-RENT-MAY")
            where.append(f'"{column}" ILIKE \'%\' || ? || \'%\'')
            params.append(str(value))
        elif isinstance(value, str) and op in ("=", "!="):  # text equality ignores case ("rent" = "Rent")
            where.append(f'lower(CAST("{column}" AS VARCHAR)) {op} lower(?)')
            params.append(value)
        else:
            where.append(f'"{column}" {op} ?')
            params.append(value)
    select = ", ".join(dims + [f"{defs[m]['sql']} AS {m}" for m in q.metrics])
    sql = f"SELECT {select} FROM \"{shape}\" WHERE {' AND '.join(where)}"
    if dims:
        sql += f" GROUP BY {', '.join(group)} ORDER BY {', '.join(group)}"
    return _execute(con, sql, params), sql, [_plain(p) for p in params], []


# Run a free SQL query written by the assistant or the user (the "exploratory" path), on one workspace when given.
def run_sql(user_id: str, sql: str, workspace_id: str | None = None) -> Result:
    con = open_readonly(user_id, workspace_id)
    tables = schema(con)
    if not tables:
        raise NotFound("no data yet: upload a file first")
    safe_sql = guard_sql(sql, tables)
    used = {t.name.lower() for t in sqlglot.parse_one(sql, dialect="duckdb").find_all(exp.Table)} & set(tables)
    rows = _execute(con, safe_sql)
    datasets, fingerprint = _provenance_data(user_id, used, workspace_id)
    return Result(rows=rows, provenance=Provenance(sql=safe_sql, datasets=datasets, data_version=fingerprint, badge="exploratory"),
                  warnings=[] if len(rows) < 5000 else ["only the first 5,000 rows are shown"])


# ── Metrics that need Python ────────────────────────────────────────────────────────────

# Realized gains, matching each sell to the oldest earlier buys (FIFO), split short / long term.
def _realized_gains(con, start: date, end: date, q: MetricQuery, today):
    sql = "SELECT trade_date, symbol, side, quantity, price FROM trades WHERE trade_date <= ? ORDER BY trade_date, side"
    trades = _execute(con, sql, [end])
    lots: dict[str, deque] = defaultdict(deque)  # symbol -> [ [qty, price, date], ... ] oldest first
    gains: dict[str, dict] = defaultdict(lambda: {"short_term_gain": 0.0, "long_term_gain": 0.0, "sell_value": 0.0, "cost": 0.0})
    unmatched: set[str] = set()
    for t in trades:
        d, qty = date.fromisoformat(t["trade_date"]), float(t["quantity"])
        if t["side"] == "buy":
            lots[t["symbol"]].append([qty, float(t["price"]), d])
            continue
        while qty > 1e-9 and lots[t["symbol"]]:
            lot = lots[t["symbol"]][0]
            used = min(qty, lot[0])
            if start <= d <= end:  # only sells inside the period count
                g = gains[t["symbol"]]
                term = "long_term_gain" if (d - lot[2]).days > LONG_TERM_DAYS else "short_term_gain"
                g[term] += used * (float(t["price"]) - lot[1])
                g["sell_value"] += used * float(t["price"])
                g["cost"] += used * lot[1]
            lot[0] -= used
            qty -= used
            if lot[0] <= 1e-9:
                lots[t["symbol"]].popleft()
        if qty > 1e-9 and start <= d <= end:
            unmatched.add(t["symbol"])
    rows = [{"symbol": s, **{k: round(v, 2) for k, v in g.items()}} for s, g in sorted(gains.items())]
    if "symbol" not in q.dimensions:
        rows = [{k: round(sum(r[k] for r in rows), 2) for k in ("short_term_gain", "long_term_gain", "sell_value", "cost")}]
    warnings = ["charges (brokerage, STT, stamp duty) are not deducted yet",
                f"long term = held more than {LONG_TERM_DAYS} days (listed shares / equity funds); other assets use different rules"]
    if unmatched:
        warnings.append(f"sold more than the bought quantity in your data for {sorted(unmatched)}: older buys are probably "
                        "missing, so part of those sells has no known cost and is left out")
    return rows, sql, [_plain(end)], warnings


# XIRR: the yearly return that makes all buys (money out), sells (money in) and today's holdings value balance out.
def _xirr(con, start: date, end: date, q: MetricQuery, today):
    sql = "SELECT trade_date, side, quantity * price AS amount FROM trades WHERE trade_date <= ?"
    flows = [(date.fromisoformat(t["trade_date"]), -t["amount"] if t["side"] == "buy" else t["amount"])
             for t in _execute(con, sql, [end])]
    warnings = []
    holdings = schema(con).get("holdings")
    value_rows = _execute(con, "SELECT as_of, SUM(value) AS v FROM holdings WHERE as_of = (SELECT MAX(as_of) FROM holdings "
                          "WHERE as_of <= ?) GROUP BY as_of", [end]) if holdings else []
    if value_rows and value_rows[0]["v"]:
        flows.append((date.fromisoformat(value_rows[0]["as_of"]), float(value_rows[0]["v"])))
    else:
        warnings.append("no holdings value found: XIRR uses trades only (upload a holdings file or CAS for today's value)")
    rate = xirr(flows)
    if rate is None:
        warnings.append("XIRR needs both money going in and money coming out (or a current value)")
    return [{"xirr_pct": None if rate is None else round(100 * rate, 2)}], sql, [_plain(end)], warnings


# Solve XIRR by bisection: find r where the sum of amount / (1 + r)^(years since first flow) is zero.
def xirr(flows: list[tuple[date, float]]) -> float | None:
    if not flows or not (any(a < 0 for _, a in flows) and any(a > 0 for _, a in flows)):
        return None
    t0 = min(d for d, _ in flows)

    # Value today of all the flows at yearly rate r.
    def npv(r: float) -> float:
        return sum(a / (1 + r) ** ((d - t0).days / 365.0) for d, a in flows)

    lo, hi = -0.9999, 100.0
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


PYTHON_METRICS = {"realized_gains": _realized_gains, "xirr": _xirr}
