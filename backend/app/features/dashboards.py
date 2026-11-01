"""
# dashboards.py - dashboards as specs, edited only through checked ops, every edit kept

A dashboard is a **spec**, not a picture: a title, a period (`global_time`) and tiles on a 12-column grid. Each tile
stores *what to compute* (a metric query or a guarded SQL query), never the numbers, so Refresh can recompute
it at any time without AI.

Every edit, by mouse (React editor) or by AI, is a list of **ops**:

| Op | `tile_id` | `value` |
|---|---|---|
| `add_tile` | optional new id | the tile: `{title, kind, query or sql or text, layout, style, fields}` |
| `update_tile` | yes | fields to change, e.g. `{"kind": "bar"}` or `{"query": {...}}` |
| `move_tile` / `resize_tile` | yes | `{"x", "y"}` / `{"w", "h"}` |
| `delete_tile` / `duplicate_tile` | yes | - |
| `set_style` | yes | style keys to change, e.g. `{"color": "#1f77b4"}` |
| `rename_dashboard` / `set_time` | - | `{"title": ...}` / `{"time": "fy"}` |

**Periods:** a metric tile whose period equals the dashboard's (`global_time`) follows it: `set_time` and Refresh
with a time move those tiles together, while a tile pinned to another period (e.g. "last_fy") keeps it.

`apply_ops` checks everything before anything is saved: tile ids exist, tiles stay inside the grid, metric queries
use this workspace's metrics and columns, SQL passes `verify.guard_sql`. One bad op = the whole list is refused.

**Edit history:** every save writes a new file `data/users/<user_id>/dashboards/<id>/v<n>.json`; earlier edits are
never changed. Each edit remembers its `parent_version`, so **undo** = go back to the parent (written as a new
edit, so undo itself can be undone), and you can also revert to any edit from the history.

**AI edit** (`ai_edit`): one language-model call turns "make revenue a bar chart and add last year" into ops, using
only the spec, the metric list and the column names (never the data). The ops go through the same checks, a preview +
diff comes back, and nothing is saved until the user applies the ops (with `base_version`). Titles and text with
numbers in them are refused: the AI never writes numbers.

**Refresh** (`refresh`): runs every tile's query again in parallel (each with the engine's 10 s limit), saves the
rows as `last_refresh.json` with each tile's data fingerprint, and reports per tile "new" / "same" / "changed" (with
before/after for single numbers). No AI is called; a failing tile carries its own error and the rest still show.
"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.data import engine
from app.data import timeexpr
from app.data.engine import MetricQuery
from app.ai.verify import guard_sql

GRID_COLUMNS = 12
MAX_TILE_HEIGHT = 40
TILE_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
STYLE_KEYS = {"color", "colors", "number_format", "font_size", "show_legend", "background", "align"}


# ── The spec ────────────────────────────────────────────────────────────────────────────

# Where a tile sits on the 12-column grid (react-grid-layout units).
class Layout(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: int = 0
    y: int = 0
    w: int = 4
    h: int = 4


# One chart / KPI / table / text box. A data tile has exactly one of `query` (trusted) or `sql` (exploratory).
class Tile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str = ""
    kind: Literal["kpi", "line", "bar", "stacked_bar", "area", "pie", "donut", "table", "waterfall", "text"]
    query: MetricQuery | None = None
    sql: str | None = None
    text: str | None = None
    layout: Layout = Layout()
    style: dict = {}
    fields: dict = {}  # chart encoding, e.g. {"x": "month", "y": "total_spend", "series": "category"}


# A whole dashboard at one edit.
class DashboardSpec(BaseModel):
    id: str
    title: str
    version: int
    workspace_id: str
    tiles: dict[str, Tile] = {}
    global_time: str = "fy"
    template_id: str | None = None
    parent_version: int | None = None
    note: str = ""
    created_at: str = ""


# One edit. Mouse and AI edits are both lists of these.
class Op(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["add_tile", "update_tile", "move_tile", "resize_tile", "delete_tile", "duplicate_tile",
                "rename_dashboard", "set_time", "set_style"]
    tile_id: str | None = None
    value: dict = {}


# ── Checking ────────────────────────────────────────────────────────────────────────────

# What tiles may reference in this workspace: its tables {table: {column: type}} and its metrics.
def workspace_context(user_id: str, workspace_id: str) -> tuple[dict, dict]:
    con = engine.open_readonly(user_id, workspace_id)
    try:
        return engine.schema(con), engine.load_metrics(user_id, workspace_id)
    finally:
        con.close()


# Raise ValueError (in plain words) unless the tile fits the grid and only reads this workspace's data.
def check_tile(tile: Tile, schema: dict, metrics: dict) -> None:
    lay = tile.layout
    if lay.x < 0 or lay.y < 0 or lay.w < 1 or lay.h < 1 or lay.x + lay.w > GRID_COLUMNS or lay.h > MAX_TILE_HEIGHT:
        raise ValueError(f"tile '{tile.id}' is outside the {GRID_COLUMNS}-column grid: {lay.model_dump()}")
    if set(tile.style) - STYLE_KEYS:
        raise ValueError(f"unknown style key(s) {sorted(set(tile.style) - STYLE_KEYS)}; allowed: {sorted(STYLE_KEYS)}")
    if tile.kind == "text":
        if not tile.text or tile.query or tile.sql:
            raise ValueError(f"text tile '{tile.id}' needs text and no query")
        return
    if (tile.query is None) == (tile.sql is None):
        raise ValueError(f"tile '{tile.id}' needs exactly one of a metric query or SQL")
    if tile.sql:
        guard_sql(tile.sql, schema)  # SqlRejected is a ValueError
        return

    q = tile.query
    unknown = [m for m in q.metrics if m not in metrics]
    if unknown or not q.metrics:
        raise ValueError(f"tile '{tile.id}': unknown metric(s) {unknown}")
    tables = {metrics[m]["shape"] for m in q.metrics}
    if len(tables) > 1:
        raise ValueError(f"tile '{tile.id}': its metrics read different tables; use one tile each")
    table = tables.pop()
    cols = schema.get(table)
    if not cols:
        raise ValueError(f"tile '{tile.id}': this workspace has no {table} data")
    bad = [c for c in q.dimensions if c not in cols and c not in engine.TIME_GRAINS]
    bad += [f.get("column") for f in q.filters if f.get("column") not in cols]
    if bad:
        raise ValueError(f"tile '{tile.id}': no column(s) {bad} in {table}")
    timeexpr.resolve(q.time)
    if not any("python" in metrics[m] for m in q.metrics):  # FIFO gains / XIRR return their own column names
        wrong = [v for v in tile.fields.values() if v not in q.metrics + q.dimensions]
        if wrong:
            raise ValueError(f"tile '{tile.id}': chart fields {wrong} are not among its metrics/dimensions")


# A text tile computes nothing, so a leftover query / SQL (e.g. after turning a chart into text) is dropped.
def _text_only(tile: Tile) -> Tile:
    return tile.model_copy(update={"query": None, "sql": None, "fields": {}}) if tile.kind == "text" else tile


# Apply ops to a spec and return the edited copy (the input is untouched). Every touched tile is checked.
def apply_ops(spec: DashboardSpec, ops: list[Op], schema: dict, metrics: dict) -> DashboardSpec:
    spec = spec.model_copy(deep=True)
    touched: set[str] = set()
    for op in ops:
        tile = spec.tiles.get(op.tile_id or "")
        if op.op not in ("add_tile", "rename_dashboard", "set_time") and tile is None:
            raise ValueError(f"{op.op}: no tile '{op.tile_id}' on this dashboard")
        v = op.value
        if op.op == "add_tile":
            tid = op.tile_id or f"t{uuid.uuid4().hex[:8]}"
            if not TILE_ID.match(tid) or tid in spec.tiles:
                raise ValueError(f"add_tile: tile id '{tid}' is invalid or already used")
            spec.tiles[tid] = _text_only(Tile(**(v | {"id": tid})))
            touched.add(tid)
        elif op.op == "update_tile":
            spec.tiles[tile.id] = _text_only(Tile(**(tile.model_dump() | v | {"id": tile.id})))
            touched.add(tile.id)
        elif op.op in ("move_tile", "resize_tile"):
            keys = ("x", "y") if op.op == "move_tile" else ("w", "h")
            tile.layout = Layout(**(tile.layout.model_dump() | {k: v[k] for k in keys if k in v}))
            touched.add(tile.id)
        elif op.op == "set_style":
            tile.style = tile.style | v
            touched.add(tile.id)
        elif op.op == "delete_tile":
            del spec.tiles[tile.id]
            touched.discard(tile.id)
        elif op.op == "duplicate_tile":
            tid = f"t{uuid.uuid4().hex[:8]}"
            copy = tile.model_copy(deep=True, update={"id": tid})
            copy.layout.y = tile.layout.y + tile.layout.h  # placed right under the original
            spec.tiles[tid] = copy
            touched.add(tid)
        elif op.op == "rename_dashboard":
            title = str(v.get("title", "")).strip()[:120]
            if not title:
                raise ValueError("rename_dashboard needs a title")
            spec.title = title
        elif op.op == "set_time":
            timeexpr.resolve(str(v.get("time", "")))
            for t in spec.tiles.values():  # tiles on the dashboard's period move with it; pinned ones ("last_fy") stay
                if t.query and t.query.time == spec.global_time:
                    t.query.time = v["time"]
            spec.global_time = v["time"]
    for tid in touched:
        check_tile(spec.tiles[tid], schema, metrics)
    return spec


# ── Storage and edit history ────────────────────────────────────────────────────────────────

# The folder holding one dashboard's saved edits.
def _dir(user_id: str, dashboard_id: str):
    return engine.user_dir(user_id) / "dashboards" / engine.safe_id(dashboard_id)


# The edit numbers saved for a dashboard, oldest first.
def _edit_numbers(user_id: str, dashboard_id: str) -> list[int]:
    return sorted(int(p.stem[1:]) for p in _dir(user_id, dashboard_id).glob("v*.json") if p.stem[1:].isdigit())


# A dashboard at one edit (default: the latest). Raises LookupError if it doesn't exist.
def get(user_id: str, dashboard_id: str, version: int | None = None) -> DashboardSpec:
    saved = _edit_numbers(user_id, dashboard_id)
    version = version or (saved[-1] if saved else None)
    if version not in saved:
        raise engine.NotFound("dashboard or edit not found")
    path = _dir(user_id, dashboard_id) / f"v{version}.json"
    return DashboardSpec(**json.loads(path.read_text(encoding="utf-8")))


# Write a spec as the next edit. Two saves racing for the same number: the second is refused, not lost.
def _write_next(user_id: str, spec: DashboardSpec, parent: int | None, note: str) -> DashboardSpec:
    saved = _edit_numbers(user_id, spec.id)
    spec = spec.model_copy(update={"version": (saved[-1] if saved else 0) + 1, "parent_version": parent, "note": note,
                                   "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    folder = _dir(user_id, spec.id)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        with (folder / f"v{spec.version}.json").open("x", encoding="utf-8") as f:
            f.write(spec.model_dump_json(indent=1))
    except FileExistsError:
        raise ValueError("someone else saved this dashboard at the same moment; reload and try again") from None
    return spec


# A new, empty dashboard in a workspace (edit 1).
def create(user_id: str, title: str, workspace_id: str = "default", template_id: str | None = None) -> DashboardSpec:
    title = title.strip()[:120]
    if not title:
        raise ValueError("a dashboard needs a title")
    spec = DashboardSpec(id=uuid.uuid4().hex[:12], title=title, version=0, workspace_id=engine.safe_id(workspace_id),
                         template_id=template_id)
    return _write_next(user_id, spec, None, "created")


# All of a user's dashboards (one workspace's when given), latest edit of each, newest first.
def list_dashboards(user_id: str, workspace_id: str | None = None) -> list[dict]:
    out = []
    for folder in (engine.user_dir(user_id) / "dashboards").glob("*"):
        if _edit_numbers(user_id, folder.name):
            s = get(user_id, folder.name)
            if workspace_id is None or s.workspace_id == engine.safe_id(workspace_id):
                out.append({"id": s.id, "title": s.title, "workspace_id": s.workspace_id, "version": s.version,
                            "tiles": len(s.tiles), "updated_at": s.created_at})
    return sorted(out, key=lambda d: d["updated_at"], reverse=True)


# Apply ops to the latest edit and save the result as a new edit. If `base_version` is given and is not the
# latest, the edit was made on an old screen and is refused.
def save_ops(user_id: str, dashboard_id: str, ops: list[Op], base_version: int | None = None) -> DashboardSpec:
    spec = get(user_id, dashboard_id)
    if base_version is not None and base_version != spec.version:
        raise ValueError(f"the dashboard changed (now at edit {spec.version}, you edited {base_version}); reload first")
    if not ops:
        raise ValueError("no changes to save")
    edited = apply_ops(spec, ops, *workspace_context(user_id, spec.workspace_id))
    return _write_next(user_id, edited, spec.version, ", ".join(o.op for o in ops)[:200])


# Go back to an earlier edit (default: undo = the latest edit's parent). Saved as a new edit.
def revert(user_id: str, dashboard_id: str, version: int | None = None) -> DashboardSpec:
    latest = get(user_id, dashboard_id)
    target = version if version is not None else latest.parent_version
    if target is None:
        raise ValueError("nothing to undo")
    old = get(user_id, dashboard_id, target)
    return _write_next(user_id, old, old.parent_version, f"back to edit {target}")


# ── AI edit: instruction -> ops -> preview (the user applies it with save_ops) ─────────────

# What the AI must answer: the ops, plus one sentence for the preview.
class AiEdit(BaseModel):
    ops: list[Op]
    summary: str = ""


AI_EDITOR = """You edit a dashboard. Return ops that do what the user asks, and nothing else.
Ops: add_tile (value = a tile: title, kind, query {metrics, dimensions, time, filters} OR sql, layout {x,y,w,h},
fields, style), update_tile (value = the fields to change), move_tile (value {x, y}), resize_tile (value {w, h}),
delete_tile, duplicate_tile, set_style (value = style keys), rename_dashboard (value {title}), set_time (value {time}).
Rules:
- Use ONLY tile ids from the dashboard, metric names from "metrics" and columns from "tables". Dimensions may also be
  "month" or "year". Metrics in one tile must read the same table. A new tile's query.time is the dashboard's
  global_time unless the user asks for another period (e.g. last year = "last_fy").
- A text tile is {"kind": "text", "title": ..., "text": "..."} with no query and no sql.
- Grid: 12 columns, x + w <= 12. Put new tiles below the existing ones.
- fields name the chart encoding with the tile's own metric / dimension names, e.g. {"x": "month", "y": "<metric>"}.
- You never see the data and must never write numbers (amounts, percentages) into titles or text."""


# The visible difference between two edits of a spec: tiles added / removed / changed, dashboard fields changed.
def diff_specs(old: DashboardSpec, new: DashboardSpec) -> dict:
    changed = {tid: [k for k in ("title", "kind", "query", "sql", "text", "layout", "style", "fields")
                     if getattr(t, k) != getattr(old.tiles[tid], k)]
               for tid, t in new.tiles.items() if tid in old.tiles}
    return {"added": [t for t in new.tiles if t not in old.tiles], "removed": [t for t in old.tiles if t not in new.tiles],
            "changed": {k: v for k, v in changed.items() if v},
            "dashboard": [k for k in ("title", "global_time") if getattr(old, k) != getattr(new, k)]}


# Turn an instruction into checked ops and a preview. Nothing is saved: the screen shows the preview, and "Apply"
# sends the same ops with base_version to save_ops. A refused answer gets one retry with the reason.
async def ai_edit(user_id: str, dashboard_id: str, instruction: str) -> dict:
    import asyncio

    from app.ai.llm_client import call_json
    from app.ai.verify import check_numbers

    spec = get(user_id, dashboard_id)
    schema, metrics = await asyncio.to_thread(workspace_context, user_id, spec.workspace_id)
    context = json.dumps({
        "dashboard": spec.model_dump(include={"title", "global_time", "tiles"}),
        "metrics": {k: f"{v['name']} (table {v['shape']}, {v['unit']})" for k, v in metrics.items()},
        "tables": schema, "time_expressions": timeexpr.EXPRESSIONS, "style_keys": sorted(STYLE_KEYS)}, default=str)
    prompt, problem = f"Context: {context}\nInstruction: {instruction.strip()[:1000]}", ""
    for _ in (1, 2):
        answer = await call_json(AI_EDITOR, prompt + problem, AiEdit, max_tokens=1500, user_id=user_id)
        if answer is None:
            raise ValueError("the assistant is unavailable right now; edit the dashboard by hand")
        if not answer.ops:
            raise ValueError("that didn't ask for a change I can make to this dashboard")
        for op in answer.ops:
            if op.op == "add_tile" and not op.tile_id:
                op.tile_id = f"t{uuid.uuid4().hex[:8]}"  # fixed now, so the preview and the saved edit match
        texts = [answer.summary] + [str(op.value.get(k) or "") for op in answer.ops for k in ("title", "text")]
        if check_numbers(" ".join(texts), []):
            problem = "\n\nYour previous answer was refused: titles and text must not contain numbers."
            continue
        try:
            preview = apply_ops(spec, answer.ops, schema, metrics)
        except ValueError as e:
            problem = f"\n\nYour previous answer was refused: {str(e)[:400]}"
            continue
        return {"base_version": spec.version, "summary": answer.summary, "ops": [o.model_dump() for o in answer.ops],
                "preview": preview.model_dump(), "diff": diff_specs(spec, preview)}
    raise ValueError(f"couldn't turn that into a valid edit ({problem.split(': ', 1)[-1].strip()}); try rephrasing")


# ── Pin from chat ───────────────────────────────────────────────────────────────────────

# Add an assistant answer to a dashboard as a tile: its plan's metric query or SQL is stored, never its numbers, so
# the tile refreshes like any other. Chart kind: one number -> kpi, split by month/year -> line, other splits -> bar,
# SQL -> table. The tile goes below the existing ones. Returns the new edit.
def pin_answer(user_id: str, dashboard_id: str, plan: dict, title: str = "") -> DashboardSpec:
    spec = get(user_id, dashboard_id)
    bottom = max((t.layout.y + t.layout.h for t in spec.tiles.values()), default=0)
    tile: dict = {"title": title.strip()[:120] or "Pinned answer", "layout": {"x": 0, "y": bottom, "w": 6, "h": 4}}
    if plan.get("kind") == "metric" and plan.get("metric_query"):
        q = MetricQuery(**plan["metric_query"])
        dims = q.dimensions
        python = any("python" in engine.load_metrics(user_id, spec.workspace_id).get(m, {}) for m in q.metrics)
        tile |= {"query": q.model_dump(),
                 "kind": "kpi" if not dims else "line" if dims[0] in engine.TIME_GRAINS else "bar",
                 "fields": {} if python else {"x": dims[0], "y": q.metrics[0]} if dims else {"value": q.metrics[0]}}
    elif plan.get("kind") == "sql" and plan.get("sql"):
        tile |= {"sql": plan["sql"], "kind": "table"}
    else:
        raise ValueError("only answers computed from your data (a metric or a query) can be pinned")
    return save_ops(user_id, dashboard_id, [Op(op="add_tile", value=tile)])


# ── Refresh (no AI: every number comes from engine.py) ──────────────────────────────────

# Run one tile's query. Returns {rows, provenance, warnings} or {error}; one failing tile never stops the others.
def _run_tile(user_id: str, workspace_id: str, tile: Tile, follow: str, time: str | None) -> dict:
    try:
        if tile.sql:
            r = engine.run_sql(user_id, tile.sql, workspace_id)
        else:
            q = tile.query.model_copy(update={"time": time}) if time and tile.query.time == follow else tile.query
            r = engine.run_metric(user_id, q, None, workspace_id)
        return {"rows": r.rows, "provenance": r.provenance.model_dump(), "warnings": r.warnings}
    except Exception as e:  # ponytail: shown on that tile only; the dashboard still renders
        return {"error": str(e) or type(e).__name__}


# What changed in one tile since the last refresh: "new", "same", or "changed" (+ before/after of single numbers).
def _diff(before: dict | None, after: dict) -> dict:
    if not before or "rows" not in before or "rows" not in after:
        return {"status": "new"}
    if before["rows"] == after["rows"]:
        return {"status": "same"}
    out: dict = {"status": "changed"}
    if len(before["rows"]) == len(after["rows"]) == 1:
        out["values"] = {k: {"before": before["rows"][0].get(k), "after": v} for k, v in after["rows"][0].items()
                         if isinstance(v, (int, float)) and v != before["rows"][0].get(k)}
    return out


# Recompute every tile of the latest edit (in parallel), save the results as the dashboard's last snapshot, and
# say what changed since the previous one. `time` moves the tiles that follow the dashboard period (see top).
def refresh(user_id: str, dashboard_id: str, time: str | None = None) -> dict:
    spec = get(user_id, dashboard_id)
    if time:
        timeexpr.resolve(time)
    tiles = [t for t in spec.tiles.values() if t.kind != "text"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {t.id: pool.submit(_run_tile, user_id, spec.workspace_id, t, spec.global_time, time) for t in tiles}
        results = {}
        for tid, f in futures.items():
            try:
                results[tid] = f.result(timeout=engine.QUERY_TIMEOUT_S + 5)
            except FuturesTimeout:
                results[tid] = {"error": "this tile took too long"}
    path = _dir(user_id, dashboard_id) / "last_refresh.json"
    last = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    snapshot = {"dashboard_id": spec.id, "version": spec.version, "time": time or spec.global_time,
                "refreshed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tiles": results}
    path.write_text(json.dumps(snapshot, ensure_ascii=False, default=str), encoding="utf-8")
    return snapshot | {"changes": {tid: _diff(last.get("tiles", {}).get(tid), r) for tid, r in results.items()}}


# The results of the last refresh, or None if it was never refreshed.
def last_refresh(user_id: str, dashboard_id: str) -> dict | None:
    get(user_id, dashboard_id)  # LookupError if the dashboard doesn't exist
    path = _dir(user_id, dashboard_id) / "last_refresh.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# ── Export ──────────────────────────────────────────────────────────────────────────────

# The dashboard as an Excel file: one sheet per tile (the rows of the last refresh, refreshed first if there is none
# for this edit) and a Provenance sheet (SQL, period, files, data fingerprint). Returns the file's bytes.
def export_xlsx(user_id: str, dashboard_id: str) -> bytes:
    from io import BytesIO

    from openpyxl import Workbook

    spec = get(user_id, dashboard_id)
    snap = last_refresh(user_id, dashboard_id)
    if not snap or snap["version"] != spec.version:
        snap = refresh(user_id, dashboard_id)
    book = Workbook()
    prov = book.active
    prov.title = "Provenance"
    prov.append(["Dashboard", spec.title, "edit", spec.version, "refreshed at", snap["refreshed_at"]])
    prov.append(["Tile", "Sheet", "Badge", "Period", "SQL", "Parameters", "Source files", "Data fingerprint", "Warnings / error"])
    used: set[str] = {"provenance"}
    for tile in sorted(spec.tiles.values(), key=lambda t: (t.layout.y, t.layout.x)):
        result = snap["tiles"].get(tile.id)
        if result is None:  # text tiles
            continue
        name = re.sub(r"[\[\]:*?/\\]", " ", tile.title or tile.id).strip()[:28] or tile.id
        while name.lower() in used:
            name = f"{name[:25]} {len(used)}"
        used.add(name.lower())
        sheet = book.create_sheet(name)
        rows = result.get("rows") or []
        columns = list(rows[0]) if rows else []
        _append(sheet, columns or [result.get("error", "no rows")])
        for r in rows:
            _append(sheet, [r.get(c) for c in columns])
        p = result.get("provenance") or {}
        period = p.get("period") or {}
        _append(prov, [tile.title or tile.id, name, p.get("badge", ""),
                       f"{period.get('start', '')}..{period.get('end', '')}" if period else "", p.get("sql", ""),
                       json.dumps(p.get("params", []), default=str), ", ".join(d["file"] for d in p.get("datasets", [])),
                       p.get("data_version", ""), result.get("error") or "; ".join(result.get("warnings", []))])
    out = BytesIO()
    book.save(out)
    return out.getvalue()


# Add a row; text is always stored as text, so a value like "=HYPERLINK(...)" from the data can't run as a formula.
def _append(sheet, values: list) -> None:
    sheet.append([v if v is None or isinstance(v, (int, float)) else str(v) for v in values])
    for cell in sheet[sheet.max_row]:
        if isinstance(cell.value, str):
            cell.data_type = "s"


# Delete a dashboard with all its edits.
def delete(user_id: str, dashboard_id: str) -> None:
    if not _edit_numbers(user_id, dashboard_id):
        raise engine.NotFound("dashboard not found")
    shutil.rmtree(_dir(user_id, dashboard_id))
