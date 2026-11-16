"""
# evals/sets/run_rag_pgvector.py - filing search through the real pgvector store, next to the in-memory ranking

run_rag.py ranks chunk vectors in memory (cosine over unit vectors) and hands them to `research.search` as `dense=`.
This script runs the app's real path instead: both filings are indexed with `research.add` while
`WEALTHOS_RESEARCH_SEARCH` asks for vectors, so every chunk (both chunkers) is embedded and stored in
`research_chunks` (HNSW cosine index), and `research.search` is called without `dense=`, so it asks Postgres.

For each question and chunker it compares the pgvector ranking with the in-memory one (top-5 / top-20 overlap and
exact order, vector and hybrid), scores recall@5 and MRR exactly as run_rag.py does (page level, answerable only),
and times the SQL alone (asyncpg `fetch`), the store call with its connection, and the whole `search` call.
A throwaway users row owns the chunks and is deleted at the end (its chunks go by ON DELETE CASCADE).

Needs a local pgvector Postgres (pgvector/pgvector:pg16, user and database wealthos) on 127.0.0.1, port
`WEALTHOS_EVAL_PG_PORT` (default 5432), and POSTGRES_PASSWORD in the repo .env (never printed).
Run from backend/: `python evals/sets/run_rag_pgvector.py [--set evals/sets/rag_hard.yaml]`
(writes evals/results_rag_pgvector.json, or results_rag_pgvector_<name>.json for another set).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import numpy as np
import yaml
from dotenv import load_dotenv

SETS = Path(__file__).resolve().parent
# The repo .env sits in the main checkout, also when this runs from a git worktree.
load_dotenv(Path(subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=SETS,
                                capture_output=True, text=True).stdout.strip()).parent / ".env")
os.environ["WEALTHOS_DB_URL"] = (f"postgresql://wealthos:{quote(os.environ['POSTGRES_PASSWORD'], safe='')}"
                                 f"@127.0.0.1:{os.getenv('WEALTHOS_EVAL_PG_PORT', '5432')}/wealthos")  # app.main reads it at import: set first
sys.path.insert(0, str(SETS))
import asyncpg  # noqa: E402
import run_rag as rr  # noqa: E402  (also puts backend/ on sys.path)
from app import main  # noqa: E402
from app.ai import research  # noqa: E402
from app.core.migrate import migrate  # noqa: E402
from app.data import engine  # noqa: E402

WS, N = "rag", research.CANDIDATES
LOG: list[tuple[str, float]] = []  # (first SQL word, ms) of every asyncpg fetch, the SQL round trip alone
CALLS: list[float] = []  # ms of every main._vectors call: connect + SQL + close


# Run fn and log how long it took under `bucket`.
def _timed(fn, bucket: list):
    async def wrapped(self, query, *args, **kw):
        start = time.perf_counter()
        try:
            return await fn(self, query, *args, **kw)
        finally:
            bucket.append((query.split()[0].upper(), (time.perf_counter() - start) * 1000))
    return wrapped


_fetch, _vectors = asyncpg.Connection.fetch, main._vectors
asyncpg.Connection.fetch = _timed(_fetch, LOG)


# main._vectors with its total time recorded (the app's store call, new connection each time).
async def _vectors_timed(query: str, *args):
    start = time.perf_counter()
    try:
        return await _vectors(query, *args)
    finally:
        CALLS.append((time.perf_counter() - start) * 1000)


main._vectors = _vectors_timed


# The question set at `path`, each question with its labelled (file, page) set (as run_rag.load_set).
def load_set(path: Path) -> list[dict]:
    rows = yaml.safe_load(path.read_text(encoding="utf-8"))
    for r in rows:
        r["relevant"] = ({(f, int(p)) for f, p in (x.rsplit(":", 1) for x in r["pages"])} if isinstance(r["file"], list)
                         else {(r["file"], int(p)) for p in r["pages"]})
    return rows


# Run one statement on a fresh connection (setup and cleanup, outside the timed app path).
async def db(query: str, *args):
    conn = await asyncpg.connect(os.environ["WEALTHOS_DB_URL"])
    try:
        return await conn.fetch(query, *args)
    finally:
        await conn.close()


# Apply the migrations on the local database.
async def prepare() -> list[str]:
    conn = await asyncpg.connect(os.environ["WEALTHOS_DB_URL"])
    try:
        return await migrate(conn)
    finally:
        await conn.close()


# Index both filings with vectors on (stored in Postgres) and the same chunks as in-memory vectors for comparison.
def index(user: str) -> tuple[dict, dict]:
    os.environ["WEALTHOS_RESEARCH_SEARCH"] = "hybrid"  # add() embeds and stores both chunk sets
    embed_ms: list[float] = []
    embed = research.embed

    # research.embed with its time recorded.
    def embed_timed(texts):
        start = time.perf_counter()
        out = embed(texts)
        embed_ms.append((time.perf_counter() - start) * 1000)
        return out

    research.embed, LOG[:] = embed_timed, []
    start = time.perf_counter()
    for pdf in sorted(rr.FILINGS.glob("*.pdf")):
        research.add(user, WS, pdf, pdf.stem)
    total = time.perf_counter() - start
    research.embed = embed
    timing = {"add_total_seconds": round(total, 1), "embed_seconds": round(sum(embed_ms) / 1000, 1),
              "insert_sql_seconds": round(sum(ms for w, ms in LOG if w == "INSERT") / 1000, 2),
              "pdf_extract_and_chunk_seconds": round(total - (sum(embed_ms) + sum(ms for _, ms in LOG)) / 1000, 1)}
    docs = research._docs(user, WS)
    vecs = {}
    for chunker in research.CHUNKERS:
        chunks = [(d["doc_id"], c) for d in docs for c in d["chunks"][chunker]]
        vecs[chunker] = ([(doc, c["chunk"]) for doc, c in chunks], np.array(research.embed([c["text"] for _, c in chunks])))
    return vecs, timing


# How alike two rankings are: shared share of the first k (0..1) and whether the first k match in order.
def agree(a: list, b: list, k: int) -> tuple[float, bool]:
    a, b = a[:k], b[:k]
    return (len(set(a) & set(b)) / max(len(a), len(b))) if a or b else 1.0, a == b  # two empty lists agree


assert agree([1, 2, 3], [1, 3, 2], 2) == (0.5, False) and agree([1, 2], [1, 2], 5) == (1.0, True) and agree([], [], 5) == (1.0, True)


# One search through Postgres (no dense=): its hits, full ms, SQL-only ms and store-call ms. Fails loudly if the
# store was not reached, because search() silently falls back to BM25 then.
async def real(user: str, q: str, chunker: str, mode: str) -> tuple[list[dict], float, float, float]:
    LOG[:], CALLS[:] = [], []
    start = time.perf_counter()
    hits = await research.search(user, WS, q, chunker=chunker, mode=mode, rerank=False, k=N)
    took = (time.perf_counter() - start) * 1000
    selects = [ms for w, ms in LOG if w == "SELECT"]
    assert len(selects) == 1 and len(CALLS) == 1, f"pgvector not queried for {q!r}"
    return hits, took, selects[0], CALLS[0]


# Every question x chunker x {vector, hybrid}: pgvector vs in-memory agreement, recall@5 / MRR, latencies.
async def compare(user: str, rows: list[dict], vecs: dict) -> dict:
    out = {}
    for chunker in research.CHUNKERS:
        for mode in ("vector", "hybrid"):
            s = {"found": [], "rr": [], "found_mem": [], "rr_mem": [], "o5": [], "o20": [], "x5": [], "x20": [],
                 "full": [], "sql": [], "empty": [], "call": [], "differ5": [], "differ20": []}
            for r in rows:
                hits, took, sql, call = await real(user, r["q"], chunker, mode)
                mem = await research.search(user, WS, r["q"], chunker=chunker, mode=mode, rerank=False, k=N,
                                            dense=rr.dense_ranking(vecs, chunker, r["q"]))
                a, b = [(h["doc_id"], h["chunk"]) for h in hits], [(h["doc_id"], h["chunk"]) for h in mem]
                for k, o, x, d in ((5, "o5", "x5", "differ5"), (N, "o20", "x20", "differ20")):
                    share, same = agree(a, b, k)
                    s[o].append(share)
                    s[x].append(same)
                    if not same:
                        s[d].append(r["id"])
                s["full"].append(took)
                s["empty"].append(not (a or b))
                s["sql"].append(sql)
                s["call"].append(call)
                if r["answerable"]:
                    s["found"].append(rr.recall_at(hits, r["relevant"]))
                    s["rr"].append(rr.reciprocal_rank(hits, r["relevant"]))
                    s["found_mem"].append(rr.recall_at(mem, r["relevant"]))
                    s["rr_mem"].append(rr.reciprocal_rank(mem, r["relevant"]))
            n, avg = len(rows), (lambda xs: round(sum(xs) / len(xs), 3))  # noqa: E731
            out[f"{mode}/{chunker}"] = {
                "pgvector": {"recall_at_5": avg(s["found"]), "mrr": avg(s["rr"]), "questions": len(s["found"])},
                "in_memory_same_run": {"recall_at_5": avg(s["found_mem"]), "mrr": avg(s["rr_mem"])},
                "agreement": {"top5_overlap_mean": avg(s["o5"]), "top20_overlap_mean": avg(s["o20"]),
                              "both_empty": sum(s["empty"]), "top5_exact_order": f"{sum(s['x5'])}/{n}", "top20_exact_order": f"{sum(s['x20'])}/{n}",
                              "top5_differs_on": s["differ5"], "top20_differs_on": s["differ20"]},
                "latency_ms": {"sql_only_p50": rr.pct(s["sql"], 50, 2), "sql_only_p95": rr.pct(s["sql"], 95, 2),
                               "store_call_with_connect_p50": rr.pct(s["call"], 50), "store_call_with_connect_p95": rr.pct(s["call"], 95),
                               "full_search_p50": rr.pct(s["full"], 50), "full_search_p95": rr.pct(s["full"], 95)}}
            a = out[f"{mode}/{chunker}"]  # for the progress line
            print(f"{mode}/{chunker:9s} pg recall@5 {a['pgvector']['recall_at_5']} MRR {a['pgvector']['mrr']} | mem "
                  f"{a['in_memory_same_run']['recall_at_5']} {a['in_memory_same_run']['mrr']} | top5 same "
                  f"{a['agreement']['top5_exact_order']} top20 same {a['agreement']['top20_exact_order']}", flush=True)
    return out


# Index, compare, write the result file; the throwaway user (and its chunks) is deleted even on error.
def run(set_path: Path, out: Path) -> dict:
    rows = load_set(set_path)
    applied = asyncio.run(prepare())
    user = str(uuid.uuid4())
    asyncio.run(db("INSERT INTO users (id, username, password_hash) VALUES ($1::uuid, $2, '!')", user, f"eval-pgvector-{user[:8]}"))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            engine.DATA_ROOT = Path(tmp) / "users"
            vecs, timing = index(user)
            stored = asyncio.run(db("SELECT chunker, doc_id, count(*) AS n FROM research_chunks WHERE user_id = $1::uuid "
                                    "GROUP BY 1, 2 ORDER BY 1, 2", user))
            counts = {c: {f"{x['doc_id']}.pdf": x["n"] for x in stored if x["chunker"] == c} for c in research.CHUNKERS}
            assert all(sum(counts[c].values()) == len(vecs[c][0]) for c in research.CHUNKERS), f"not all chunks stored: {counts}"
            idx = asyncio.run(db("SELECT indexdef FROM pg_indexes WHERE indexname = 'research_chunks_cosine'"))
            ext = asyncio.run(db("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
            arms = asyncio.run(compare(user, rows, vecs))
    finally:
        asyncio.run(db("DELETE FROM users WHERE id = $1::uuid", user))
    left = asyncio.run(db("SELECT count(*) AS n FROM research_chunks WHERE user_id = $1::uuid", user))[0]["n"]
    result = {"set": {"file": str(set_path), "questions": len(rows), "answerable": sum(r["answerable"] for r in rows)},
              "migrations_applied_now": applied, "embed_model": research.EMBED_MODEL,
              "embed_dims": int(vecs["structure"][1].shape[1]), "pgvector_version": ext[0]["extversion"],
              "index": idx[0]["indexdef"], "session_settings": {"hnsw.iterative_scan": "strict_order"},
              "candidates": N, "top_k": rr.K, "chunk_counts_in_postgres": counts, "index_time": timing, "arms": arms,
              "cleanup_chunks_left": left, "run_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "git_sha": rr.git_sha()}
    out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="evals/sets/rag_golden.yaml")
    args = ap.parse_args()
    path = Path(args.set)
    name = "" if path.stem == "rag_golden" else "_" + path.stem.removeprefix("rag_")
    run(path, SETS.parent / f"results_rag_pgvector{name}.json")
