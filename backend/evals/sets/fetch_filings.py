"""
# evals/sets/fetch_filings.py - downloads the two real 10-K filings the filing Q&A set is labelled on, as PDFs

Real filings are not committed (only synthetic fixtures go in git), so this fetches them from SEC EDGAR, checks each
download against its SHA-256, and renders the HTML to a Letter-size PDF with PyMuPDF. The page numbers in
`rag_golden.yaml` are pages of these PDFs, so the rendered page count is checked too.

Needs `SEC_USER_AGENT` (in .env) and pymupdf (requirements-dev.txt). Output: backend/storage/eval_filings/*.pdf
(gitignored). Run: `python evals/sets/fetch_filings.py`
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import time
from pathlib import Path

import fitz
import httpx
from dotenv import load_dotenv

OUT = Path(__file__).resolve().parents[2] / "storage" / "eval_filings"
FILINGS = {  # pdf name: (EDGAR url, sha256 of the HTML, pages after rendering)
    "aapl_10k_2025.pdf": ("https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
                          "7f44c98b2b8d4f0836aac3f151c415149df4a560c4208860a7d2ccfedf9134be", 97),
    "nvda_10k_2026.pdf": ("https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm",
                          "9eacf925ad55b2ef1d0dec1fb4ec168e2a13646c80a0c27229be2b01cfd4e5b2", 129),
}


# Render filing HTML to a PDF, dropping the hidden inline-XBRL header block first. Bold text is set in capitals:
# the rendered fonts carry no weight finx can read, and capitals are how finx tells an "ITEM 1A." heading from a
# sentence that mentions Item 1A (many printed 10-Ks look like this anyway).
def render(html: str, dest: Path) -> int:
    html = re.sub(r"(?s)<ix:header>.*?</ix:header>", "", html)
    upper = lambda t: re.sub(r"&#?\w+;|[^&]+", lambda x: x[0] if x[0][0] == "&" else x[0].upper(), t)  # noqa: E731
    html = re.sub(r'(<span style="[^"]*font-weight:(?:700|bold)[^"]*">)([^<]*)', lambda m: m[1] + upper(m[2]), html)
    story, writer = fitz.Story(html), fitz.DocumentWriter(str(dest))
    rect = fitz.paper_rect("letter")
    more = True
    while more:
        dev = writer.begin_page(rect)
        more, _ = story.place(rect + (36, 36, -36, -36))
        story.draw(dev)
        writer.end_page()
    writer.close()
    return fitz.open(dest).page_count


# Fetch, check and render every filing that is not on disk yet.
def main() -> None:
    load_dotenv()  # the nearest .env above this file
    agent = os.getenv("SEC_USER_AGENT")
    if not agent:
        sys.exit("set SEC_USER_AGENT in .env (SEC asks for a name and email)")
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (url, sha, pages) in FILINGS.items():
        dest = OUT / name
        if not dest.exists():
            reply = httpx.get(url, headers={"User-Agent": agent}, timeout=60)
            body = reply.content
            got = hashlib.sha256(body).hexdigest()
            if got != sha:  # say what came back: SEC answers some networks with a short notice page instead
                start = " ".join(body[:300].decode("utf-8", errors="replace").split())
                sys.exit(f"{name}: download hash {got} does not match {sha} "
                         f"(HTTP {reply.status_code}, {len(body)} bytes, starts: {start!r})")
            n = render(body.decode("utf-8", errors="replace"), dest)
            time.sleep(0.2)  # SEC asks for at most 10 requests a second
        n = fitz.open(dest).page_count
        if pages is not None and n != pages:
            sys.exit(f"{name}: rendered {n} pages, the labels expect {pages}; check the pymupdf version")
        print(name, n, "pages")


if __name__ == "__main__":
    main()
