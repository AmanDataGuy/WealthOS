"""
# workspaces.py - group a user's data into separate "databases"

A workspace is what the user picks before chatting or building a dashboard ("GCC e-commerce data",
"My portfolio", "Company MIS"). It is a named group of datasets (uploaded files) with its own semantic model (`semantic.py`). Chats and dashboards only
ever see their own workspace's tables.

- Every user always has **My files** (id `default`); uploads go there unless another workspace is chosen.
- Which workspace a dataset belongs to is written in the dataset's own description (`engine.save_tables`), so there is
  no separate list to keep in sync.
- Stored as `data/users/<user_id>/workspaces/<workspace_id>.json`: `{id, name, created_at}`.
"""
from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone

from app.features import dashboards
from app.data import engine

DEFAULT = {"id": "default", "name": "My files"}


# Folder holding this user's workspace files.
def _dir(user_id: str):
    return engine.user_dir(user_id) / "workspaces"


# All workspaces of a user (My files first), each with how many datasets it holds and its database connection (or None).
def list_workspaces(user_id: str) -> list[dict]:
    found = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(_dir(user_id).glob("*.json"))]
    out = [DEFAULT | {"created_at": None}] + sorted(found, key=lambda w: w["created_at"])
    counts: dict[str, int] = {}
    for m in engine.list_datasets(user_id):
        counts[m.get("workspace_id", "default")] = counts.get(m.get("workspace_id", "default"), 0) + 1
    return [w | {"datasets": counts.get(w["id"], 0)} for w in out]


# One workspace, or None if it doesn't exist.
def get_workspace(user_id: str, workspace_id: str) -> dict | None:
    return next((w for w in list_workspaces(user_id) if w["id"] == engine.safe_id(workspace_id)), None)


# Create a workspace with a display name; returns it.
def create_workspace(user_id: str, name: str) -> dict:
    name = name.strip()[:80]
    if not name:
        raise ValueError("a workspace needs a name")
    ws = {"id": uuid.uuid4().hex[:12], "name": name, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    _dir(user_id).mkdir(parents=True, exist_ok=True)
    (_dir(user_id) / f"{ws['id']}.json").write_text(json.dumps(ws, ensure_ascii=False), encoding="utf-8")
    return ws | {"datasets": 0}


# Rename a workspace (My files keeps its name).
def rename_workspace(user_id: str, workspace_id: str, name: str) -> dict:
    path = _dir(user_id) / f"{engine.safe_id(workspace_id)}.json"
    if not path.exists() or not name.strip():
        raise ValueError("workspace not found, or empty name (My files cannot be renamed)")
    ws = json.loads(path.read_text(encoding="utf-8")) | {"name": name.strip()[:80]}
    path.write_text(json.dumps(ws, ensure_ascii=False), encoding="utf-8")
    return ws


# Delete a workspace AND its datasets and dashboards (uploaded raw files stay under raw/). My files cannot be deleted.
def delete_workspace(user_id: str, workspace_id: str) -> int:
    ws_id = engine.safe_id(workspace_id)
    path = _dir(user_id) / f"{ws_id}.json"
    if ws_id == "default" or not path.exists():
        raise ValueError("workspace not found (My files cannot be deleted)")
    removed = 0
    for m in engine.list_datasets(user_id, ws_id):
        engine.remove_dataset(user_id, m)
        removed += 1
    for d in dashboards.list_dashboards(user_id, ws_id):  # its dashboards would point at tables that are gone
        dashboards.delete(user_id, d["id"])
    path.unlink()
    shutil.rmtree(_dir(user_id) / ws_id, ignore_errors=True)  # its connection, uploaded SQLite file, semantic model
    shutil.rmtree(engine.user_dir(user_id) / "research" / ws_id, ignore_errors=True)  # its indexed filings
    return removed
