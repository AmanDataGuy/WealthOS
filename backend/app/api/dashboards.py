"""
# dashboards.py - dashboards (checked edits, undo, AI edit, refresh, Excel export, report), ready-made dashboard
templates, and the user's own Excel templates recalculated by LibreOffice

- Routes under `/dashboards`, `/templates` and `/excel`.
"""
import asyncio
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app.api.sources import _save_upload
from app.main import USER, USER_WS, XLSX, require_workspace

router = APIRouter()


# ── Dashboards: built only from checked edits; every save is kept, so undo always works ─────────────────────

# A new dashboard's title and workspace.
class DashboardBody(BaseModel):
    title: str
    workspace_id: str = "default"


# Dashboard edits, plus the edit number the screen was showing.
class OpsBody(BaseModel):
    ops: list[dict]                     # [{"op": "add_tile", "value": {...}}, ...]
    base_version: Optional[int] = None  # the edit the screen showed; refused if the dashboard has moved on


# Which edit to go back to.
class RevertBody(BaseModel):
    version: Optional[int] = None  # an edit number; none = undo the last change


# A plain-English change to a dashboard.
class InstructionBody(BaseModel):
    instruction: str  # e.g. "make revenue a bar chart and add last year"


# A chat answer to add to a dashboard.
class PinBody(BaseModel):
    plan: dict       # the plan of a chat answer
    title: str = ""


# The period to recompute a dashboard for.
class PeriodBody(BaseModel):
    time: Optional[str] = None  # e.g. "mtd"; none = as saved


# Report options: period, AI summary on/off, subtitle.
class ReportBody(BaseModel):
    time: Optional[str] = None
    narrative: bool = True  # the AI-written summary (every number checked)
    subtitle: str = ""


# A dashboards function's answer as plain JSON.
def _plain(result):
    return result.model_dump() if hasattr(result, "model_dump") else result


@router.get("/dashboards/{user_id}", dependencies=USER)
def dashboards_list(user_id: str, workspace_id: Optional[str] = None):
    """The user's dashboards, newest first."""
    from app.features.dashboards import list_dashboards
    return list_dashboards(user_id, workspace_id)


@router.post("/dashboards/{user_id}", dependencies=USER)
def dashboards_create(user_id: str, body: DashboardBody):
    """Create an empty dashboard in a workspace."""
    from app.features.dashboards import create
    require_workspace(user_id, body.workspace_id)
    return _plain(create(user_id, body.title, body.workspace_id))


@router.get("/dashboards/{user_id}/{dashboard_id}", dependencies=USER)
def dashboards_get(user_id: str, dashboard_id: str, version: Optional[int] = None):
    """A dashboard at an edit (default: the latest)."""
    from app.features.dashboards import get
    return _plain(get(user_id, dashboard_id, version))


@router.delete("/dashboards/{user_id}/{dashboard_id}", dependencies=USER)
def dashboards_delete(user_id: str, dashboard_id: str):
    """Delete a dashboard and its whole edit history."""
    from app.features.dashboards import delete
    delete(user_id, dashboard_id)
    return {"deleted": dashboard_id}


@router.post("/dashboards/{user_id}/{dashboard_id}/ops", dependencies=USER)
def dashboards_ops(user_id: str, dashboard_id: str, body: OpsBody):
    """Apply edits (all checked first; one bad edit refuses the lot) and save them."""
    from app.features.dashboards import Op, save_ops
    return _plain(save_ops(user_id, dashboard_id, [Op(**o) for o in body.ops], body.base_version))


@router.post("/dashboards/{user_id}/{dashboard_id}/revert", dependencies=USER)
def dashboards_revert(user_id: str, dashboard_id: str, body: RevertBody):
    """Undo the last change, or go back to a chosen edit (saved as a new edit)."""
    from app.features.dashboards import revert
    return _plain(revert(user_id, dashboard_id, body.version))


@router.post("/dashboards/{user_id}/{dashboard_id}/ai-edit", dependencies=USER)
async def dashboards_ai_edit(user_id: str, dashboard_id: str, body: InstructionBody):
    """Turn an instruction into checked edits and a preview; nothing is saved until they are sent to /ops."""
    from app.features.dashboards import ai_edit
    return await ai_edit(user_id, dashboard_id, body.instruction)


@router.post("/dashboards/{user_id}/{dashboard_id}/pin", dependencies=USER)
def dashboards_pin(user_id: str, dashboard_id: str, body: PinBody):
    """Add a chat answer to the dashboard (its query, never its numbers)."""
    from app.features.dashboards import pin_answer
    return _plain(pin_answer(user_id, dashboard_id, body.plan, body.title))


@router.post("/dashboards/{user_id}/{dashboard_id}/refresh", dependencies=USER)
def dashboards_refresh(user_id: str, dashboard_id: str, body: PeriodBody):
    """Recompute every tile (no AI) and say per tile what changed since last time."""
    from app.features.dashboards import refresh
    return refresh(user_id, dashboard_id, body.time)


@router.get("/dashboards/{user_id}/{dashboard_id}/refresh", dependencies=USER)
def dashboards_last_refresh(user_id: str, dashboard_id: str):
    """The last refresh's results (null if never), so a dashboard opens instantly."""
    from app.features.dashboards import last_refresh
    return last_refresh(user_id, dashboard_id)


@router.get("/dashboards/{user_id}/{dashboard_id}/export.xlsx", dependencies=USER)
def dashboards_export(user_id: str, dashboard_id: str):
    """The dashboard as Excel: a sheet per tile and a sheet saying where every number came from."""
    from app.features.dashboards import export_xlsx
    return Response(export_xlsx(user_id, dashboard_id), media_type=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="dashboard-{Path(dashboard_id).name}.xlsx"'})


@router.post("/dashboards/{user_id}/{dashboard_id}/report", dependencies=USER)
async def dashboards_report(user_id: str, dashboard_id: str, body: ReportBody):
    """The dashboard as a printable report with a number-checked summary."""
    from app.features.reports import build_report
    return await build_report(user_id, dashboard_id, body.time, body.narrative, body.subtitle)


# The table and columns to build a template dashboard from.
class TemplateBody(BaseModel):
    table: Optional[str] = None   # business templates: the table to use (from /suggest)
    mapping: dict[str, str] = {}  # {slot: column}
    title: Optional[str] = None


@router.get("/templates")
def templates_catalog():
    """Every ready-made dashboard and the columns it needs."""
    from app.features.templates import catalog
    return catalog()


@router.get("/templates/{user_id}/{workspace_id}/{template_id}/suggest", dependencies=USER_WS)
def templates_suggest(user_id: str, workspace_id: str, template_id: str):
    """The best table and column for each of the template's slots, and what is missing."""
    from app.features.templates import suggest
    return suggest(user_id, workspace_id, template_id)


@router.post("/templates/{user_id}/{workspace_id}/{template_id}/apply", dependencies=USER_WS)
def templates_apply(user_id: str, workspace_id: str, template_id: str, body: TemplateBody):
    """Create a dashboard from the template with the confirmed columns."""
    from app.features.templates import apply
    return _plain(apply(user_id, workspace_id, template_id, body.table, body.mapping, body.title))


# ── Excel templates: your workbook filled with a table and recalculated by LibreOffice ───────────────────────

@router.post("/excel/{user_id}/{workspace_id}", dependencies=USER_WS)
async def excel_add(user_id: str, workspace_id: str, file: UploadFile = File(...), data_table: str = Form(...),
                    name: str = Form("")):
    """Upload a template (.xlsx with a 'Data' sheet and named result cells); it is checked before it is kept."""
    from app.features import excel
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / Path(file.filename or "template.xlsx").name
        await _save_upload(file, dest)
        return await asyncio.to_thread(excel.add, user_id, workspace_id, dest, name, data_table)


@router.get("/excel/{user_id}/{workspace_id}", dependencies=USER_WS)
def excel_list(user_id: str, workspace_id: str):
    """The workspace's templates with their last run."""
    from app.features.excel import list_templates
    return list_templates(user_id, workspace_id)


@router.post("/excel/{user_id}/{workspace_id}/{template_id}/run", dependencies=USER_WS)
def excel_run(user_id: str, workspace_id: str, template_id: str):
    """Fill the template with today's data, recalculate it, return the named results."""
    from app.features.excel import run
    return run(user_id, workspace_id, template_id)


@router.get("/excel/{user_id}/{workspace_id}/{template_id}/filled.xlsx", dependencies=USER_WS)
def excel_download(user_id: str, workspace_id: str, template_id: str):
    """The filled, recalculated workbook from the last run."""
    from app.features.excel import filled_path
    return FileResponse(filled_path(user_id, workspace_id, template_id), media_type=XLSX,
                        filename=f"filled-{Path(template_id).name}.xlsx")


@router.delete("/excel/{user_id}/{workspace_id}/{template_id}", dependencies=USER_WS)
def excel_delete(user_id: str, workspace_id: str, template_id: str):
    """Delete a template."""
    from app.features.excel import delete
    delete(user_id, workspace_id, template_id)
    return {"deleted": template_id}
