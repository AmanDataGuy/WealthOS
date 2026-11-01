"""
# templates.py - start a dashboard from a template (definitions in `templates.yaml`)

1. **Suggest** (`suggest`): for each table of the workspace, fill the template's slots with columns of the right
   role (from the Semantic Builder: saved meanings first, else the type/name rules), preferring columns whose name
   matches the slot's usual names. The table that fills the most must-have slots wins.
2. **The user confirms** (or changes) the table and the slot -> column mapping.
3. **Apply** (`apply`): the template's metrics become workspace metrics (checked and test-run by `semantic.py`), then
   a new dashboard gets the tiles as ops (checked by `dashboards.py`). Tiles that need an optional slot the user left
   empty are skipped. If anything fails, the half-made dashboard is removed.

Retail templates read a fixed standard table (holdings / transactions / trades) with built-in metrics, so they have
no slots: applying one just needs that table in the workspace.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from wealthos import dashboards
from wealthos import engine
from wealthos import semantic
from wealthos.dashboards import Op

TEMPLATES_FILE = Path(__file__).parent / "templates.yaml"
ROLE_FITS = {"date": {"date"}, "measure": {"measure"}, "id": {"id", "dimension"}, "dimension": {"dimension", "id"}}
SLOT = re.compile(r"\{(\w+)\}")


# All templates from templates.yaml: {template_id: {...}}.
def load_templates() -> dict[str, dict]:
    return yaml.safe_load(TEMPLATES_FILE.read_text(encoding="utf-8"))


# The catalog for the "New dashboard" screen (no tile details).
def catalog() -> list[dict]:
    return [{"id": k, "title": t["title"], "audience": t["audience"], "description": t["description"],
             "table": t.get("table"), "slots": t.get("slots", [])} for k, t in load_templates().items()]


# One template by id. Raises LookupError if there is none.
def _template(template_id: str) -> dict:
    t = load_templates().get(template_id)
    if not t:
        raise engine.NotFound(f"no template '{template_id}'")
    return t


# "Order_Date" -> {"order", "date"}: the words of a name, for matching columns with a slot's usual names.
def _words(name: str) -> set[str]:
    return set(re.sub(r"[^a-z0-9]+", " ", name.lower()).split())


# How well a column name fits a slot: same words as a usual name beats containing one, and earlier names in the
# slot's list beat later ones ("amount" before "revenue"). 0 = no match.
def _score(column: str, slot: dict) -> int:
    col = _words(column)
    names = [_words(slot["name"])] + [_words(n) for n in slot.get("names", [])]
    exact = [200 - i for i, n in enumerate(names) if n == col]
    contained = [100 - i for i, n in enumerate(names) if n <= col]
    return max(exact + contained + [0])


# Fill a template's slots from one table's columns {column: {role, ...}}: {slot: column}. Name matches first;
# then must-have slots take any free column of the right role. Optional slots are only filled by a name match.
def _fill(slots: list[dict], columns: dict[str, dict]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for by_name in (True, False):
        for slot in slots:
            if slot["name"] in mapping or (not by_name and slot.get("optional")):
                continue
            free = [c for c, m in columns.items() if m["role"] in ROLE_FITS[slot["role"]] and c not in mapping.values()]
            if by_name:
                free = [c for c in free if _score(c, slot)]
            if free:
                mapping[slot["name"]] = max(free, key=lambda c: _score(c, slot))
    return mapping


# Suggest a table and slot mapping for a template in this workspace: {template_id, table, mapping, missing}.
def suggest(user_id: str, workspace_id: str, template_id: str) -> dict:
    t = _template(template_id)
    columns = semantic.suggest_columns(user_id, workspace_id)
    if t.get("table"):
        ok = t["table"] in columns
        return {"template_id": template_id, "table": t["table"] if ok else None, "mapping": {},
                "missing": [] if ok else [f"a {t['table']} table (upload one first)"]}
    required = [s["name"] for s in t["slots"] if not s.get("optional")]
    best = {"template_id": template_id, "table": None, "mapping": {}, "missing": required}
    for table, cols in columns.items():
        mapping = _fill(t["slots"], cols)
        missing = [n for n in required if n not in mapping]
        if best["table"] is None or (len(missing), -len(mapping)) < (len(best["missing"]), -len(best["mapping"])):
            best = {"template_id": template_id, "table": table, "mapping": mapping, "missing": missing}
    return best


# Save one template metric as a workspace metric and return its id. The same definition already saved is reused;
# a different one under that id gets a new id (a metric another dashboard uses never changes underneath it).
def _save_metric(user_id: str, workspace_id: str, table: str, name: str, spec: dict) -> str:
    base = re.sub(r"[^a-z0-9_]", "_", f"{name}_{table}".lower())[:38]
    checked = semantic.check_metric(user_id, workspace_id, base, spec)
    existing = semantic.load(user_id, workspace_id)["metrics"]
    key, n = base, 1
    while key in existing and existing[key] != checked:
        n += 1
        key = f"{base}_{n}"
    if key not in existing:
        semantic.save_metric(user_id, workspace_id, key, spec)
    return key


# Create a dashboard from a template with a confirmed table + slot mapping. Returns the new dashboard (with its tiles).
def apply(user_id: str, workspace_id: str, template_id: str, table: str | None = None,
          mapping: dict[str, str] | None = None, title: str | None = None) -> dashboards.DashboardSpec:
    t = _template(template_id)
    mapping = {k: v for k, v in (mapping or {}).items() if v}
    schema, _ = dashboards.workspace_context(user_id, workspace_id)
    table = t.get("table") or table
    if table not in schema:
        raise ValueError(f"this workspace has no {table} table yet" if t.get("table") else f"table must be one of {sorted(schema)}")
    slots = {s["name"]: s for s in t.get("slots", [])}
    wrong = {k: v for k, v in mapping.items() if k not in slots or v not in schema[table]}
    missing = [n for n, s in slots.items() if not s.get("optional") and n not in mapping]
    if wrong or missing:
        raise ValueError(f"unknown slot or column {wrong}" if wrong else f"still to choose: {missing}")
    quoted = {k: '"' + v.replace('"', '""') + '"' for k, v in mapping.items()}

    keys = {}  # template metric name -> workspace metric id
    for name, m in t.get("metrics", {}).items():
        if all(s in mapping for s in SLOT.findall(m["sql"])):
            spec = {"table": table, "date": mapping["date"], "name": m["name"], "unit": m.get("unit", ""),
                    "sql": SLOT.sub(lambda x: quoted[x.group(1)], m["sql"])}
            keys[name] = _save_metric(user_id, workspace_id, table, name, spec)

    defs = engine.load_metrics(user_id, workspace_id)
    time = t.get("time", "fy")
    ops = [Op(op="set_time", value={"time": time})]
    for tile in t["tiles"]:
        if any(s not in mapping for d in tile.get("dimensions", []) for s in SLOT.findall(d)) or \
                any(m in t.get("metrics", {}) and m not in keys for m in tile["metrics"]):
            continue  # needs an optional slot the user left empty
        dims = [SLOT.sub(lambda x: mapping[x.group(1)], d) for d in tile.get("dimensions", [])]
        metrics = [keys.get(m, m) for m in tile["metrics"]]
        fields = {"x": dims[0], "y": metrics[0]} if dims else {"value": metrics[0]}
        if any(defs.get(m, {}).get("python") for m in metrics):
            fields = {}  # FIFO gains / XIRR return their own columns
        x, y, w, h = tile["layout"]
        ops.append(Op(op="add_tile", value={"title": tile["title"], "kind": tile["kind"], "fields": fields,
                                             "query": {"metrics": metrics, "dimensions": dims, "time": time},
                                             "layout": {"x": x, "y": y, "w": w, "h": h}}))

    spec = dashboards.create(user_id, title or t["title"], workspace_id, template_id)
    try:
        return dashboards.save_ops(user_id, spec.id, ops)
    except Exception:
        dashboards.delete(user_id, spec.id)
        raise
