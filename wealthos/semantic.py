"""
# semantic.py - the Semantic Builder: what a workspace's data *means*

Raw tables say `amt`, `cust_id`, `dt`. The semantic model says "amt is the order value in INR (a measure)",
"orders.cust_id points at customers.id", and "GMV = SUM(amt) by dt". The assistant and dashboards read
this instead of guessing. One model per workspace:

| Part | What it holds | Used by |
|---|---|---|
| `columns` | per table, per column: a role (`date` / `measure` / `dimension` / `id`) and a plain description | assistant planner, template slots |
| `relationships` | `{left: "orders.customer_id", right: "customers.id", kind}`: how tables join | assistant SQL joins |
| `metrics` | `metrics.yaml`-style entries for this workspace (a same-named one replaces the built-in) | `engine.run_metric`, so assistant + dashboards |
| definitions | what the user said terms mean (per user, `agent.definitions`) | assistant |

Column roles are **suggested by rules** from each column's type and name (no AI), then the user edits and saves.
Every save is checked against the workspace's real tables, and a metric's SQL is test-run before it is kept.

Stored as `data/users/<user_id>/workspaces/<workspace_id>/semantic.json`.
"""
from __future__ import annotations

import json
import re

import sqlglot
from sqlglot import exp

from wealthos import engine
from wealthos.verify import guard_sql

ROLES = {"date", "measure", "dimension", "id"}
JOIN_KINDS = {"many_to_one", "one_to_one", "one_to_many"}
METRIC_KEY = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
HIDDEN_COLUMNS = {"source_file", "dataset_id", "source_row"}  # bookkeeping columns (where a row came from)


# ── Storage ─────────────────────────────────────────────────────────────────────────────

# Where the workspace's semantic model file lives.
def _path(user_id: str, workspace_id: str):
    return engine.user_dir(user_id) / "workspaces" / engine.safe_id(workspace_id) / "semantic.json"


# The workspace's semantic model: {columns, relationships, metrics} (empty parts when nothing is saved yet).
def load(user_id: str, workspace_id: str) -> dict:
    path = _path(user_id, workspace_id)
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {"columns": {}, "relationships": [], "metrics": {}} | saved


# Write the semantic model to disk and return it.
def _save(user_id: str, workspace_id: str, model: dict) -> dict:
    path = _path(user_id, workspace_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model, indent=1, ensure_ascii=False), encoding="utf-8")
    return model


# The workspace's tables and column types: {table: {column: type}}.
def _schema(user_id: str, workspace_id: str) -> dict[str, dict[str, str]]:
    con = engine.open_readonly(user_id, workspace_id)
    try:
        return engine.schema(con)
    finally:
        con.close()


# ── Column meanings ─────────────────────────────────────────────────────────────────

# A role for one column from its type and name: dates are dates, "*_id" are ids, numbers are measures, text is a dimension.
def _role(column: str, dtype: str) -> str:
    name, dtype = column.lower(), dtype.upper()
    if "DATE" in dtype or "TIME" in dtype:
        return "date"
    if name in ("id", "isin") or name.endswith(("_id", " id")):
        return "id"
    if any(t in dtype for t in ("INT", "DECIMAL", "DOUBLE", "FLOAT", "REAL", "NUMERIC")):
        return "measure"
    return "dimension"


# Every column with its saved meaning, or a rule-based suggestion: {table: {column: {type, role, description, saved}}}.
def suggest_columns(user_id: str, workspace_id: str) -> dict:
    saved = load(user_id, workspace_id)["columns"]
    out: dict[str, dict] = {}
    for table, cols in _schema(user_id, workspace_id).items():
        out[table] = {}
        for col, dtype in cols.items():
            if col in HIDDEN_COLUMNS:
                continue
            mine = saved.get(table, {}).get(col)
            out[table][col] = {"type": dtype, **(mine or {"role": _role(col, dtype), "description": ""}), "saved": bool(mine)}
    return out


# Save column meanings {table: {column: {role, description}}} (merged into what was saved before).
def save_columns(user_id: str, workspace_id: str, columns: dict[str, dict[str, dict]]) -> dict:
    schema = _schema(user_id, workspace_id)
    model = load(user_id, workspace_id)
    for table, cols in columns.items():
        for col, meaning in cols.items():
            if col not in schema.get(table, {}):
                raise ValueError(f"no column '{table}.{col}' in this workspace")
            if meaning.get("role") not in ROLES:
                raise ValueError(f"role for '{table}.{col}' must be one of {sorted(ROLES)}")
            model["columns"].setdefault(table, {})[col] = {"role": meaning["role"],
                                                           "description": str(meaning.get("description", ""))[:300]}
    return _save(user_id, workspace_id, model)


# ── Relationships ───────────────────────────────────────────────────────────────────

# Replace the workspace's relationships with a checked list of {left: "table.col", right: "table.col", kind}.
def save_relationships(user_id: str, workspace_id: str, relationships: list[dict]) -> dict:
    schema = _schema(user_id, workspace_id)
    clean = []
    for r in relationships:
        left, right, kind = str(r.get("left", "")), str(r.get("right", "")), r.get("kind", "many_to_one")
        for side in (left, right):
            table, _, col = side.partition(".")
            if col not in schema.get(table, {}):
                raise ValueError(f"'{side}' is not a table.column in this workspace")
        if kind not in JOIN_KINDS or left.partition(".")[0] == right.partition(".")[0]:
            raise ValueError(f"a relationship joins two different tables; kind is one of {sorted(JOIN_KINDS)}")
        clean.append({"left": left, "right": right, "kind": kind})
    return _save(user_id, workspace_id, load(user_id, workspace_id) | {"relationships": clean})


# ── Workspace metrics ───────────────────────────────────────────────────────────────

# Check a metric definition against the workspace's real data and test-run it; returns it in metrics.yaml form.
def check_metric(user_id: str, workspace_id: str, key: str, spec: dict) -> dict:
    if not METRIC_KEY.match(key):
        raise ValueError("metric id: lower-case letters, digits and _ (e.g. gross_sales)")
    table, date_col = spec.get("table") or spec.get("shape"), spec.get("date")
    schema = _schema(user_id, workspace_id)
    if table not in schema:
        raise ValueError(f"table must be one of {sorted(schema)}")
    if _role(str(date_col), schema[table].get(date_col, "")) != "date":
        raise ValueError(f"'date' must be a date column of {table}, so periods (mtd, fy ...) can filter it")
    if spec.get("time_semantics", "flow") not in ("flow", "point_in_time"):
        raise ValueError("time_semantics must be flow (add up over the period) or point_in_time (latest value)")

    try:
        tree = sqlglot.parse_one(str(spec.get("sql", "")), dialect="duckdb")
    except sqlglot.errors.ParseError as e:
        raise ValueError(f"could not read the SQL expression: {e}") from None
    if isinstance(tree, exp.Query) or tree.find(exp.Query) or not tree.find(exp.AggFunc):
        raise ValueError("sql must be one aggregate expression, e.g. SUM(amount) or COUNT(DISTINCT customer_id)")
    sql = tree.sql(dialect="duckdb")  # re-written by the parser: comments and tricks are gone
    probe = guard_sql(f'SELECT {sql} AS m FROM "{table}"', schema)  # same function / column checks as free SQL
    con = engine.open_readonly(user_id, workspace_id)
    try:
        engine._execute(con, probe)  # a metric that fails on the data raises ValueError with the reason
    finally:
        con.close()
    return {"name": str(spec.get("name") or key)[:80], "shape": table, "date": date_col,
            "time_semantics": spec.get("time_semantics", "flow"), "unit": str(spec.get("unit", ""))[:20], "sql": sql}


# Add or replace one workspace metric (a built-in with the same id is replaced in this workspace only).
def save_metric(user_id: str, workspace_id: str, key: str, spec: dict) -> dict:
    model = load(user_id, workspace_id)
    model["metrics"][key] = check_metric(user_id, workspace_id, key, spec)
    return _save(user_id, workspace_id, model)


# Remove one workspace metric (a built-in it replaced comes back).
def delete_metric(user_id: str, workspace_id: str, key: str) -> dict:
    model = load(user_id, workspace_id)
    if model["metrics"].pop(key, None) is None:
        raise ValueError(f"no workspace metric '{key}'")
    return _save(user_id, workspace_id, model)
