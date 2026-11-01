"""
# excel.py - your own Excel template, filled with your data and recalculated by LibreOffice

**The template contract** (kept small on purpose):
- a sheet named `Data`: WealthOS clears it and writes one of the workspace's tables there (headers in row 1);
- the cells you want back are **named** in Excel (Formulas > Define Name, e.g. `total_rent` -> `Summary!B2`);
  those names are the outputs (a name may cover up to 100 cells);
- everything else (your formulas, SUMIFS summaries, formatting) stays as you built it.

**Checked at upload (`check`), refused with the reason:** not `.xlsx` (no `.xlsm` / macros), over 10 MB, macros
(`vbaProject.bin`), links to other workbooks, Excel Tables / structured references (`Tbl[col]`), OFFSET / INDIRECT,
dynamic-array functions, any function outside `ALLOWED`, no `Data` sheet, no named output cells. openpyxl reads the
XML with defusedxml (installed), so XML bombs are refused too.

**Running (`run`):** copy the template -> write the data with openpyxl -> LibreOffice (headless, its own profile set
to "always recalculate on load", 120 s timeout, one job at a time) saves a recalculated copy -> outputs read with
`data_only=True` -> any `#REF!`, `#NAME?`, `#VALUE!`, `#DIV/0!` ... in an output is reported, never hidden. The filled
workbook is kept for download. Results say "calculated by LibreOffice": Excel itself never ran.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import openpyxl
from dotenv import load_dotenv
from openpyxl.formula import Tokenizer

from app.data import engine

load_dotenv()  # LIBREOFFICE_PATH may be set in .env

MAX_BYTES = 10 * 1024 * 1024
TIMEOUT_S = 120  # a cold first start on Windows can take ~60 s
ALLOWED = set("""SUM SUMIF SUMIFS SUMPRODUCT COUNT COUNTA COUNTBLANK COUNTIF COUNTIFS AVERAGE AVERAGEIF AVERAGEIFS MIN MAX
MINIFS MAXIFS MEDIAN MODE STDEV STDEV.S STDEV.P VAR VAR.S PERCENTILE PERCENTILE.INC QUARTILE LARGE SMALL RANK RANK.EQ
ROUND ROUNDUP ROUNDDOWN INT MOD ABS SIGN SQRT POWER EXP LN LOG LOG10 CEILING FLOOR TRUNC PRODUCT
IF IFS IFERROR IFNA AND OR NOT XOR TRUE FALSE SWITCH CHOOSE
VLOOKUP HLOOKUP LOOKUP INDEX MATCH XLOOKUP XMATCH ROW ROWS COLUMN COLUMNS
DATE YEAR MONTH DAY EOMONTH EDATE TODAY DATEDIF DAYS WEEKDAY NETWORKDAYS DATEVALUE YEARFRAC
TEXT LEFT RIGHT MID LEN UPPER LOWER PROPER TRIM CONCAT CONCATENATE TEXTJOIN SUBSTITUTE FIND SEARCH VALUE EXACT REPT
ISBLANK ISNUMBER ISTEXT ISERROR ISNA N NA
PMT PV FV NPV IRR XNPV XIRR RATE NPER IPMT PPMT CUMIPMT CUMPRINC EFFECT NOMINAL""".split())
BLOCKED = {"OFFSET": "its range moves, so results can't be traced", "INDIRECT": "it builds references from text",
           "FILTER": "dynamic arrays", "SORT": "dynamic arrays", "SORTBY": "dynamic arrays", "UNIQUE": "dynamic arrays",
           "SEQUENCE": "dynamic arrays", "RANDARRAY": "dynamic arrays", "LET": "LET / LAMBDA", "LAMBDA": "LET / LAMBDA",
           "WEBSERVICE": "web calls", "HYPERLINK": "links", "CALL": "external calls", "INFO": "system info", "CELL": "system info"}
ERRORS = ("#REF!", "#NAME?", "#VALUE!", "#DIV/0!", "#N/A", "#NUM!", "#NULL!", "Err:")
_LOCK = threading.Lock()  # ponytail: one recalculation at a time per process; a job queue if many users run at once
_RECALC_PROFILE = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema">
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="OOXMLRecalcMode" oor:op="fuse"><value>0</value></prop></item>
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="ODFRecalcMode" oor:op="fuse"><value>0</value></prop></item>
</oor:items>"""


# The LibreOffice program (LIBREOFFICE_PATH, PATH, or the default Windows / Linux install). Raises if missing.
def soffice() -> str:
    for candidate in [os.getenv("LIBREOFFICE_PATH", ""), shutil.which("soffice") or "", shutil.which("libreoffice") or "",
                      r"C:\Program Files\LibreOffice\program\soffice.exe", "/usr/bin/soffice", "/usr/lib/libreoffice/program/soffice"]:
        if candidate and Path(candidate).exists():
            return candidate
    raise RuntimeError("LibreOffice is not installed (set LIBREOFFICE_PATH): Excel templates need it to recalculate")


# Why a formula is not allowed (blocked or unlisted function, structured / external reference); [] if fine.
def _formula_problems(formula: str) -> list[str]:
    out = []
    for tok in Tokenizer(formula).items:
        if tok.type == "FUNC" and tok.subtype == "OPEN":
            name = tok.value[:-1].upper().removeprefix("_XLFN.").removeprefix("_XLWS.")
            if name in BLOCKED:
                out.append(f"{name} is not allowed ({BLOCKED[name]})")
            elif name not in ALLOWED:
                out.append(f"function {name} is not on the allowed list")
        elif tok.type == "OPERAND" and tok.subtype == "RANGE" and "[" in tok.value:
            out.append("structured / external references ([...]) are not allowed")
    return out


# Check an uploaded template. Returns {ok, problems, outputs: {name: "Sheet!A1"}, sheets}.
def check(path: Path) -> dict:
    refuse = lambda why: {"ok": False, "problems": [why], "outputs": {}, "sheets": []}
    if path.suffix.lower() != ".xlsx":
        return refuse("only .xlsx templates (no .xlsm / .xls: macros are not allowed)")
    if path.stat().st_size > MAX_BYTES:
        return refuse("the template is larger than 10 MB")
    problems = []
    with zipfile.ZipFile(path) as z:
        parts = z.namelist()
    if any(p.endswith("vbaProject.bin") for p in parts):
        problems.append("the workbook contains macros")
    if any(p.startswith("xl/externalLinks/") for p in parts):
        problems.append("the workbook links to other workbooks")
    if any(p.startswith("xl/tables/") for p in parts):
        problems.append("the workbook uses Excel Tables; convert them to plain ranges (Table Design > Convert to Range)")
    wb = openpyxl.load_workbook(path)  # defusedxml is installed, so openpyxl parses safely
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    problems += [f"{ws.title}!{cell.coordinate}: {why}" for why in _formula_problems(cell.value)]
    if "Data" not in wb.sheetnames:
        problems.append("there is no sheet named 'Data' (WealthOS writes your table there)")
    outputs = {name: str(dn.attr_text) for name, dn in wb.defined_names.items() if not name.startswith("_xlnm")}
    if not outputs:
        problems.append("no named output cells: name the result cells in Excel (Formulas > Define Name)")
    return {"ok": not problems, "problems": problems[:20], "outputs": outputs, "sheets": wb.sheetnames}


# ── Templates per workspace: data/users/<id>/excel/<ws>/<template_id>/{template.xlsx, meta.json, filled.xlsx} ──

# Where a workspace's Excel templates live.
def _folder(user_id: str, workspace_id: str) -> Path:
    return engine.user_dir(user_id) / "excel" / engine.safe_id(workspace_id)


# One template's folder and saved details. Raises LookupError if it doesn't exist.
def _meta(user_id: str, workspace_id: str, tid: str) -> tuple[Path, dict]:
    folder = _folder(user_id, workspace_id) / engine.safe_id(tid)
    if not (folder / "meta.json").exists():
        raise engine.NotFound(f"no template '{tid}'")
    return folder, json.loads((folder / "meta.json").read_text(encoding="utf-8"))


# Save a checked template that will be filled from `data_table`. Returns its meta; ValueError lists the problems.
def add(user_id: str, workspace_id: str, path: Path, name: str, data_table: str) -> dict:
    result = check(path)
    if not result["ok"]:
        raise ValueError("this template can't be used: " + "; ".join(result["problems"]))
    tid = uuid.uuid4().hex[:12]
    folder = _folder(user_id, workspace_id) / tid
    folder.mkdir(parents=True)
    shutil.copy(path, folder / "template.xlsx")
    meta = {"id": tid, "name": name or path.stem, "file": path.name, "data_table": data_table, "outputs": result["outputs"],
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "last_run": None}
    (folder / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta


# The workspace's templates (newest first), each with its last run.
def list_templates(user_id: str, workspace_id: str) -> list[dict]:
    folder = _folder(user_id, workspace_id)
    metas = [json.loads(p.read_text(encoding="utf-8")) for p in folder.glob("*/meta.json")] if folder.exists() else []
    return sorted(metas, key=lambda m: m["created_at"], reverse=True)


# Delete one template and its filled copy.
def delete(user_id: str, workspace_id: str, tid: str) -> None:
    folder, _ = _meta(user_id, workspace_id, tid)
    shutil.rmtree(folder)


# The filled, recalculated workbook of the last run, for download.
def filled_path(user_id: str, workspace_id: str, tid: str) -> Path:
    folder, _ = _meta(user_id, workspace_id, tid)
    if not (folder / "filled.xlsx").exists():
        raise engine.NotFound("run the template first")
    return folder / "filled.xlsx"


# Recalculate a workbook with LibreOffice (headless, its own profile that always recalculates). Returns the new file.
# ponytail: one reused profile (data/cache/lo_profile): building a fresh one costs ~15 s on Windows; runs are serialised
# by _LOCK, so they never share it at the same time.
def recalc(src: Path, out_dir: Path) -> Path:
    profile = engine.DATA_ROOT.parent / "cache" / "lo_profile"
    (profile / "user").mkdir(parents=True, exist_ok=True)
    (profile / "user" / "registrymodifications.xcu").write_text(_RECALC_PROFILE, encoding="utf-8")
    result_dir = out_dir / "out"
    cmd = [soffice(), f"-env:UserInstallation={profile.resolve().as_uri()}", "--headless", "--norestore", "--nolockcheck",
           "--convert-to", "xlsx:Calc MS Excel 2007 XML", "--outdir", str(result_dir), str(src)]
    with _LOCK:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
    out = result_dir / src.name
    if not out.exists():
        raise RuntimeError(f"LibreOffice could not recalculate the workbook: {(proc.stderr or proc.stdout).strip()[:300]}")
    return out


# A cell, or the nested tuples of cells for a range, as one flat stream of cells.
def _cells(x):
    if isinstance(x, tuple):
        for item in x:
            yield from _cells(item)
    else:
        yield x


# Fill a template with the workspace's table, recalculate, read the named outputs. Returns the run summary.
def run(user_id: str, workspace_id: str, tid: str) -> dict:
    folder, meta = _meta(user_id, workspace_id, tid)
    con = engine.open_readonly(user_id, workspace_id)
    try:
        cols = engine.schema(con).get(meta["data_table"])
        if not cols:
            raise engine.NotFound(f"no '{meta['data_table']}' table in this workspace")
        keep = [c for c in cols if c not in ("source_file", "dataset_id", "source_row")]
        select = ", ".join('"' + c + '"' for c in keep)
        rows = engine._execute(con, f'SELECT {select} FROM "{meta["data_table"]}" LIMIT 100000')
    finally:
        con.close()

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "workbook.xlsx"
        wb = openpyxl.load_workbook(folder / "template.xlsx")
        ws = wb["Data"]
        ws.delete_rows(1, ws.max_row)
        ws.append(keep)
        for r in rows:
            ws.append([r[c] for c in keep])
        wb.save(work)
        shutil.copy(recalc(work, Path(tmp)), folder / "filled.xlsx")

    book = openpyxl.load_workbook(folder / "filled.xlsx", data_only=True)
    outputs, errors = {}, []
    for name in meta["outputs"]:
        dn = book.defined_names.get(name)
        values = [c.value for sheet, ref in (dn.destinations if dn else []) for c in _cells(book[sheet][ref])][:100]
        outputs[name] = values[0] if len(values) == 1 else values
        errors += [f"{name}: {v}" for v in values if isinstance(v, str) and v.startswith(ERRORS)]
        if dn is None:
            errors.append(f"{name}: the name disappeared after recalculation")
    run_info = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "rows_written": len(rows),
                "outputs": outputs, "errors": errors, "engine": "LibreOffice"}
    meta["last_run"] = run_info
    (folder / "meta.json").write_text(json.dumps(meta, indent=1, default=str), encoding="utf-8")
    return {"template": meta["name"], **run_info,
            "note": "Calculated by LibreOffice from your template, not by Excel; check results that matter in Excel."}
