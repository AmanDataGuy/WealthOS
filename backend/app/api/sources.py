"""
# sources.py - data in: workspaces, file uploads, Google Sheets, sample data, column mapping, column meanings

- Routes under `/workspaces`, `/sources` and `/semantic`. Each checks the caller's token (or token + workspace).
- `_save_upload` is also used by the Excel template upload in `dashboards.py`.
"""
import asyncio
import json
import logging
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel

from app.data import engine
from app.main import MAX_UPLOAD_BYTES, USER, USER_WS, require_workspace, verify_user_workspace

router = APIRouter()
logger = logging.getLogger("app.main")  # same logger name as before the split, so log lines read the same

# ── Workspaces: named groups of data that chats and dashboards are tied to ─────────────────────────────────

@router.get("/workspaces/{user_id}", dependencies=USER)
def workspaces_list(user_id: str):
    """The user's workspaces (My files first) with their table counts."""
    from app.core.workspaces import list_workspaces
    return list_workspaces(user_id)


# ── Data in: file uploads, Google Sheets, column mapping ─────────────────────────────────────────────────────

# Stream an upload to disk in 1 MB pieces and stop at 20 MB (an endless upload must not fill the disk).
async def _save_upload(file: UploadFile, dest: Path) -> None:
    written = 0
    with dest.open("wb") as f:
        while chunk := await file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "file too large: 20 MB at most")
            f.write(chunk)


# The user's folder of original uploads.
def _raw_dir(user_id: str) -> Path:
    path = engine.user_dir(user_id) / "raw"
    path.mkdir(parents=True, exist_ok=True)
    return path


# A saved file becomes tables: kept under raw/<sha256>/, read, auto-mapped if its layout is known, saved to the
# workspace; a filing PDF is also indexed for questions in chat. Returns what was read.
def _ingest_file(user_id: str, tmp: Path, filename: str, password: Optional[str], workspace_id: str) -> dict:
    from app.data.ingest import file_hash, parse_file
    from app.data.mapping import auto_apply
    digest = file_hash(tmp)
    dest = _raw_dir(user_id) / digest / filename
    dest.parent.mkdir(exist_ok=True)
    tmp.replace(dest)  # the same file uploaded twice lands in the same folder
    from app.ai import research
    if dest.suffix.lower() in research.TEXT_FILES:  # notes / markdown: text the assistant can answer from, no tables
        doc = research.add(user_id, workspace_id, dest, digest[:16])
        return {"file": filename, "workspace_id": workspace_id, "content_hash": digest, "research": doc,
                "datasets": [], "tables": []}
    try:
        tables = parse_file(dest, password, filename)
    except ValueError:
        raise
    except Exception as e:  # a broken or unreadable file
        raise ValueError(f"could not read the file: {type(e).__name__}: {e}") from e
    (dest.parent / "tables.json").write_text(json.dumps([t.model_dump(mode="json") for t in tables], ensure_ascii=False),
                                             encoding="utf-8")  # kept so unknown tables can be mapped later
    tables = [auto_apply(user_id, t) for t in tables]
    saved = engine.save_tables(user_id, tables, workspace_id)
    doc = None
    if dest.suffix.lower() == ".pdf" and all(t.shape in ("statements", "unknown") for t in tables):  # a filing, not a CAS
        try:
            doc = research.add(user_id, workspace_id, dest, digest[:16])
        except Exception as e:  # indexing is a bonus: it never fails the upload
            logger.warning("research index failed for %s: %s", filename, e)
    return {"file": filename, "workspace_id": workspace_id, "content_hash": digest, "research": doc,
            "datasets": [m["dataset_id"] for m in saved],
            "tables": [{"index": i, "shape": t.shape, "status": t.status, "rows": len(t.rows), "columns": t.columns,
                        "sheet": t.source.sheet, "sample": t.rows[:5], "warnings": t.warnings} for i, t in enumerate(tables)]}


@router.post("/sources/upload")
async def upload_source(user_id: str = Form(...), file: UploadFile = File(...), password: Optional[str] = Form(None),
                        workspace_id: str = Form("default"), authorization: Optional[str] = Header(None)):
    """Upload a CSV / Excel / PDF (statement, tradebook, holdings, prices, CAS with its password, filing) or notes (.md / .txt)."""
    await verify_user_workspace(user_id, workspace_id, authorization)
    name = Path(file.filename or "upload").name  # never a path from the browser
    if Path(name).suffix.lower() not in (".csv", ".xlsx", ".pdf", ".md", ".txt"):
        raise HTTPException(400, "upload a .csv, .xlsx, .pdf, .md or .txt file")
    tmp = _raw_dir(user_id) / f"upload_{uuid.uuid4().hex}{Path(name).suffix.lower()}"
    await _save_upload(file, tmp)
    return await asyncio.to_thread(_ingest_file, user_id, tmp, name, password, workspace_id)


# A Google Sheet link to read into a workspace.
class SheetBody(BaseModel):
    user_id: str
    url: str                       # a Google Sheets link shared as "Anyone with the link can view"
    workspace_id: str = "default"


@router.post("/sources/sheet")
async def upload_sheet(body: SheetBody, authorization: Optional[str] = Header(None)):
    """Read one tab of a Google Sheet shared by link, exactly like an uploaded CSV."""
    await verify_user_workspace(body.user_id, body.workspace_id, authorization)
    from app.data.ingest.sheets import fetch_sheet_csv
    data, name = await fetch_sheet_csv(body.url, MAX_UPLOAD_BYTES)
    tmp = _raw_dir(body.user_id) / f"upload_{uuid.uuid4().hex}.csv"
    tmp.write_bytes(data)
    return await asyncio.to_thread(_ingest_file, body.user_id, tmp, name, None, body.workspace_id)


@router.post("/sources/{user_id}/sample", dependencies=USER)
def load_sample(user_id: str, workspace_id: str = "default"):
    """Load the synthetic sample bank statement, tradebook and holdings (the eval's data) so a new user can try it."""
    from app.data.samples import write_sample_files
    require_workspace(user_id, workspace_id)
    with tempfile.TemporaryDirectory(dir=_raw_dir(user_id)) as folder:  # same disk, so files move into raw/ cheaply
        return [_ingest_file(user_id, p, p.name, None, workspace_id) for p in write_sample_files(Path(folder))]


@router.get("/sources/{user_id}/datasets", dependencies=USER)
def list_user_datasets(user_id: str, workspace_id: Optional[str] = None):
    """The user's saved tables: kind, source file, rows, warnings."""
    return engine.list_datasets(user_id, workspace_id)


@router.delete("/sources/{user_id}/datasets/{dataset_id}", dependencies=USER)
def delete_user_dataset(user_id: str, dataset_id: str):
    """Remove one saved table (the assistant and dashboards stop seeing it)."""
    meta = next((m for m in engine.list_datasets(user_id) if m["dataset_id"] == dataset_id), None)
    if meta is None:
        raise engine.NotFound("no such table")
    engine.remove_dataset(user_id, meta)
    return {"deleted": dataset_id}


# One table as it was read from an upload (kept in tables.json), for mapping.
def _uploaded_table(user_id: str, content_hash: str, index: int):
    from app.data.ingest import ExtractedTable
    path = _raw_dir(user_id) / Path(content_hash).name / "tables.json"
    tables = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    if not 0 <= index < len(tables):
        raise engine.NotFound("that upload or table was not found")
    return ExtractedTable(**tables[index])


@router.get("/sources/{user_id}/{content_hash}/tables/{index}/mapping", dependencies=USER)
async def suggest_mapping(user_id: str, content_hash: str, index: int):
    """Suggest what each column of an unrecognised table means (saved layout, name rules, then one AI call)."""
    from app.data.ingest.tabular import SHAPES
    from app.data.mapping import suggest
    table = _uploaded_table(user_id, content_hash, index)
    return {"columns": [c for c in table.columns if c != "source_row"], "profile": table.profile,
            "suggestion": await suggest(user_id, table),
            "shapes": {s: {"required": spec["required"], "columns": list(spec["names"])} for s, spec in SHAPES.items()}}


# The user's choice of what each column of an unknown table means.
class MappingBody(BaseModel):
    shape: str
    column_map: dict[str, str]  # {standard column: file column}
    workspace_id: str = "default"


@router.post("/sources/{user_id}/{content_hash}/tables/{index}/mapping", dependencies=USER)
def confirm_mapping(user_id: str, content_hash: str, index: int, body: MappingBody):
    """Save a confirmed mapping (remembered for this layout) and the cleaned table."""
    from app.data.mapping import confirm
    require_workspace(user_id, body.workspace_id)
    parsed, saved = confirm(user_id, _uploaded_table(user_id, content_hash, index), body.shape, body.column_map,
                            body.workspace_id)
    return {"datasets": [m["dataset_id"] for m in saved], "shape": parsed.shape, "rows": len(parsed.rows),
            "sample": parsed.rows[:5], "warnings": parsed.warnings}


# ── Understanding the data: column meanings, how tables join, the workspace's own metrics ───────────────────

@router.get("/semantic/{user_id}/{workspace_id}", dependencies=USER_WS)
def semantic_get(user_id: str, workspace_id: str):
    """Columns (saved meaning or a suggestion), relationships, metrics and remembered meanings."""
    from app.data import semantic
    from app.ai.agent import definitions
    model = semantic.load(user_id, workspace_id)
    return {"columns": semantic.suggest_columns(user_id, workspace_id), "relationships": model["relationships"],
            "workspace_metrics": model["metrics"], "metrics": engine.load_metrics(user_id, workspace_id),
            "definitions": definitions(user_id)}
