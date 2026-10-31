"""
# sheets.py - read a Google Sheet shared by link (read-only, no sign-in)

The user pastes the sheet's link; we download one tab as CSV and from there it is exactly like an uploaded CSV
(same recognition, mapping and checks in `tabular.py`).

- Accepted: `https://docs.google.com/spreadsheets/d/<id>/...` (shared as "Anyone with the link can view") and
  `.../spreadsheets/d/e/<id>/pub...` (published to the web). The `gid` in the link picks the tab.
- We build the export URL ourselves from the sheet id, so a pasted link can never make the server fetch anything
  other than a Google Sheets CSV export.
- A private sheet answers with Google's sign-in page (HTML), which is refused with a plain message.
- Downloads stop at the same size limit as uploads. Private sheets are not supported.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

SHEET_PATH = re.compile(r"^/spreadsheets/d/(e/)?([A-Za-z0-9_-]{10,})")
SHARE_HINT = "couldn't open the sheet: share it as 'Anyone with the link can view' (or publish it to the web)"


# The CSV export URL for a Google Sheets link (raises ValueError for anything else).
def export_url(url: str) -> str:
    p = urlsplit(url.strip())
    m = SHEET_PATH.match(p.path)
    if p.scheme != "https" or p.hostname != "docs.google.com" or not m:
        raise ValueError("paste a Google Sheets link (https://docs.google.com/spreadsheets/d/...)")
    gid = (parse_qs(p.query).get("gid") or parse_qs(p.fragment).get("gid") or [""])[0]
    tab = f"&gid={gid}" if gid.isdigit() else ""
    if m.group(1):  # published to the web
        return f"https://docs.google.com/spreadsheets/d/e/{m.group(2)}/pub?output=csv{tab}"
    return f"https://docs.google.com/spreadsheets/d/{m.group(2)}/export?format=csv{tab}"


# Download the sheet's tab as CSV: (bytes, a file name for it). `client` is only passed in tests.
async def fetch_sheet_csv(url: str, max_bytes: int, client: httpx.AsyncClient | None = None) -> tuple[bytes, str]:
    target = export_url(url)
    owns = client is None
    client = client or httpx.AsyncClient(timeout=20, follow_redirects=True)
    try:
        async with client.stream("GET", target) as r:
            if r.status_code in (401, 403, 404) or "text/html" in r.headers.get("content-type", ""):
                raise ValueError(SHARE_HINT)  # a private sheet sends a sign-in page, not CSV
            if r.status_code != 200:
                raise ValueError(f"Google Sheets answered with status {r.status_code}; try again later")
            data = bytearray()
            async for chunk in r.aiter_bytes():
                data += chunk
                if len(data) > max_bytes:
                    raise ValueError(f"the sheet is larger than {max_bytes // (1024 * 1024)} MB")
            disposition = r.headers.get("content-disposition", "")
    except httpx.HTTPError as e:
        raise ValueError(f"couldn't reach Google Sheets: {type(e).__name__}") from None
    finally:
        if owns:
            await client.aclose()
    found = re.search(r"filename\*=UTF-8''([^;]+)|filename=\"?([^\";]+)", disposition)
    name = Path(unquote((found.group(1) or found.group(2)) if found else "")).name
    name = name or f"sheet_{SHEET_PATH.match(urlsplit(target).path).group(2)[:8]}"
    return bytes(data), name if name.lower().endswith(".csv") else f"{name}.csv"
