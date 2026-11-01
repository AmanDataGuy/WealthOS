"""
# mapping.py - help the user say what the columns of an unrecognised file mean

When `ingest` can't recognise a table by its column names, it comes back as shape "unknown". This module suggests
a mapping ("column 'Amt (Rs)' is the amount, 'Txn Dt' is the date ...") and, once the user confirms it, turns the
table into clean rows and remembers the mapping so the next file with the same columns maps itself.

How a suggestion is made, cheapest first:
1. **Saved**: the user already confirmed a mapping for these exact column names -> use it.
2. **Rules**: known column names (the same lists `ingest/tabular.py` uses).
3. **AI**: if must-have columns are still missing, ONE call to the language model with the column names and a few
   example values (personal details masked first). It may only answer with our standard column names.
4. **The user confirms.** Confidence per column: `high` = rules and AI agree, `medium` = only one of them found it,
   and anything missing is listed for the user to pick.

The AI only labels columns; it never reads or produces amounts. The numbers are read by code after confirmation.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from wealthos.engine import save_tables, user_dir
from wealthos.ingest import ExtractedTable
from wealthos.ingest.tabular import SHAPES, clean, match_partial, missing_required


# A fingerprint of a table's column names: the same export layout always gets the same fingerprint.
def header_hash(columns: list[str]) -> str:
    names = sorted(re.sub(r"[^a-z0-9]+", " ", c.lower()).strip() for c in columns if c != "source_row")
    return hashlib.sha256("|".join(names).encode()).hexdigest()[:16]


# All mappings this user has confirmed: {header_hash: {shape, column_map, confirmed_at}}.
def _saved(user_id: str) -> dict:
    path = user_dir(user_id) / "mappings.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


# Suggest a shape and column mapping for an "unknown" table (saved -> rules -> one AI call).
async def suggest(user_id: str, table: ExtractedTable) -> dict:
    headers = [c for c in table.columns if c != "source_row"]
    saved = _saved(user_id).get(header_hash(headers))
    if saved:
        return {**saved, "confidence": {k: "saved" for k in saved["column_map"]}, "missing": [], "via": "saved"}

    # rules: the shape whose must-have columns are best covered by known names
    shape = max(SHAPES, key=lambda s: len(match_partial(headers, SHAPES[s])) - 2 * len(missing_required(match_partial(headers, SHAPES[s]), SHAPES[s])))
    rules = match_partial(headers, SHAPES[shape])
    ai, via = {}, "rules"
    if missing_required(rules, SHAPES[shape]):
        ai_shape, ai = await _ask_ai(headers, table.profile, user_id)
        via = "rules+ai"
        if ai_shape and ai_shape != shape and not missing_required(ai, SHAPES[ai_shape]):
            shape, rules = ai_shape, match_partial(headers, SHAPES[ai_shape])  # the AI found a better-fitting shape
    column_map, confidence = {}, {}
    for std in SHAPES[shape]["names"]:
        r, a = rules.get(std), ai.get(std)
        if r or a:
            column_map[std] = r or a
            confidence[std] = "high" if r and r == a else "medium"
    return {"shape": shape, "column_map": column_map, "confidence": confidence,
            "missing": missing_required(column_map, SHAPES[shape]), "via": via}


# Ask the language model which standard column each header is. Returns (shape, {standard: header}); (None, {}) on any failure.
async def _ask_ai(headers: list[str], profile: dict, user_id: str) -> tuple[str | None, dict]:
    from wealthos.services.llm_client import call_llm
    from wealthos.services.pii_redaction import redact_pii

    allowed = {s: list(spec["names"]) for s, spec in SHAPES.items()}
    examples = {h: [redact_pii(str(v)) for v in (profile.get(h) or {}).get("examples", [])] for h in headers}
    system = ("You label spreadsheet columns for a personal-finance app. Choose ONE shape and map its standard columns "
              f"to the file's column names. Shapes and their standard columns: {json.dumps(allowed)}. "
              'Answer ONLY with JSON: {"shape": "<shape>", "columns": {"<standard column>": "<file column name>"}}. '
              "Use only file column names from the list given. Leave out anything you are not sure about.")
    user = f"File columns with example values (personal details masked): {json.dumps(examples, ensure_ascii=False)}"
    try:
        raw = await call_llm(system=system, user=user, max_tokens=400, temperature=0, user_id=user_id)  # usage per user
        data = json.loads(re.search(r"\{.*\}", raw or "", re.S).group(0))
    except Exception:
        return None, {}
    shape = data.get("shape") if data.get("shape") in SHAPES else None
    if not shape:
        return None, {}
    cols = {std: col for std, col in (data.get("columns") or {}).items()
            if std in SHAPES[shape]["names"] and col in headers}  # nothing outside the allowed names gets through
    return shape, cols


# The user confirmed a mapping: check it, remember it, clean the rows and save them as a dataset.
def confirm(user_id: str, table: ExtractedTable, shape: str, column_map: dict[str, str],
            workspace_id: str = "default") -> tuple[ExtractedTable, list[dict]]:
    if shape not in SHAPES:
        raise ValueError(f"shape must be one of {sorted(SHAPES)}")
    headers = [c for c in table.columns if c != "source_row"]
    wrong = {k: v for k, v in column_map.items() if k not in SHAPES[shape]["names"] or v not in headers}
    if wrong:
        raise ValueError(f"unknown standard or file column(s): {wrong}")
    missing = missing_required(column_map, SHAPES[shape])
    if missing:
        raise ValueError(f"still missing must-have column(s): {missing}")

    saved = _saved(user_id)
    saved[header_hash(headers)] = {"shape": shape, "column_map": column_map,
                                   "confirmed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    path = user_dir(user_id) / "mappings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(saved, indent=1, ensure_ascii=False), encoding="utf-8")
    return apply(user_id, table, shape, column_map, workspace_id)


# Turn an unknown table into clean rows with a given mapping, and save it as a dataset.
def apply(user_id: str, table: ExtractedTable, shape: str, column_map: dict[str, str],
          workspace_id: str = "default") -> tuple[ExtractedTable, list[dict]]:
    parsed = _mapped(table, shape, column_map)
    return parsed, save_tables(user_id, [parsed], workspace_id)


# The same table, cleaned with a mapping (not saved yet).
def _mapped(table: ExtractedTable, shape: str, column_map: dict[str, str]) -> ExtractedTable:
    rows, bad = clean(shape, column_map, table.rows)
    return table.model_copy(update={
        "shape": shape, "status": "parsed", "rows": rows, "columns": list(column_map) + ["source_row"], "profile": {},
        "warnings": [w for w in table.warnings if "not recognised" not in w]
                    + ([f"{bad} row(s) skipped because a required value could not be read"] if bad else [])})


# On upload: if the user already confirmed a mapping for this exact layout, apply it (the caller saves it).
def auto_apply(user_id: str, table: ExtractedTable) -> ExtractedTable:
    if table.shape != "unknown":
        return table
    saved = _saved(user_id).get(header_hash([c for c in table.columns if c != "source_row"]))
    return _mapped(table, saved["shape"], saved["column_map"]) if saved else table
