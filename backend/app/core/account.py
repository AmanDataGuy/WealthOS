"""
# account.py - take your data with you, or delete it

Everything a user owns lives in one folder (`data/users/<user_id>/`, see engine.py), so:
- `export_zip(user_id, extra)`: a zip of that folder plus `extra` files (the API adds the user's AI usage as CSV).
  Saved database connections (`connection.json`) are left out: they hold the database password.
- `delete_files(user_id)`: removes the folder, after checking it really is inside the data root.
The users / llm_usage rows are handled by the API, which owns the database.

ponytail: the zip is built in memory; stream it from a temp file if accounts grow past a few hundred MB.
"""
import io
import shutil
import zipfile

from app.data import engine

SKIP = {"connection.json"}


# The user's folder, resolved, refusing anything that is not strictly inside the data root.
def _folder(user_id: str):
    root = engine.DATA_ROOT.resolve()
    folder = engine.user_dir(user_id).resolve()
    if folder == root or not folder.is_relative_to(root):
        raise ValueError("not a valid user id")
    return folder


# A zip of everything the user stored (minus connection secrets) plus `extra` {name: text}.
def export_zip(user_id: str, extra: dict[str, str]) -> bytes:
    folder = _folder(user_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if folder.exists():
            for path in sorted(folder.rglob("*")):
                if path.is_file() and path.name not in SKIP:
                    z.write(path, path.relative_to(folder).as_posix())
        for name, text in extra.items():
            z.writestr(name, text)
    return buf.getvalue()


# Delete everything the user stored. Returns how many files were removed.
def delete_files(user_id: str) -> int:
    folder = _folder(user_id)
    if not folder.exists():
        return 0
    count = sum(1 for p in folder.rglob("*") if p.is_file())
    shutil.rmtree(folder)
    return count
