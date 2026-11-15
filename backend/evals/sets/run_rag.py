"""
# evals/sets/run_rag.py - how well filing Q&A (app/ai/research.py) finds, refuses and answers, on two real 10-Ks

The set is `rag_golden.yaml`: 81 questions on Apple's fiscal 2025 and NVIDIA's fiscal 2026 10-K (fetched by
`fetch_filings.py`), each with the pages that answer it, key facts, a kind, and 15 that the filings cannot answer.
Both filings are indexed into a temp workspace with `research.add` (keyword mode, no database). Chunk vectors are
computed here with `research.embed` and ranked by cosine over unit vectors (the order pgvector returns), then handed
to `research.search` as `dense=`, so every arm runs the app's own search code without Postgres.

1. **Retrieval** (keyless, every arm {bm25, vector, hybrid, hybrid+rerank} x {structure, fixed}, plus the page-level
   BM25 the app ran before chunking as `pages_bm25_before`): recall@5 and MRR at page level (a hit covers its pages
   page_start..page_end; a question counts as found if a labelled page is covered), on the answerable questions,
   recall@5 by kind, p50/p95 retrieval ms.
2. **Refusal threshold**: questions split into dev (1/3) and test (2/3) with a fixed seed, stratified by answerable.
   Each arm's threshold on its best top-5 score is picked on dev only (most correct refusals + answerable kept);
   refusal rates are reported on test only.
3. **Answers** (`--live`, keys from the repo's .env): `research.answer` for the structure arms and the best fixed arm,
   on all 81 questions, with each arm's threshold. Fact accuracy, refusals, number-check rejects (verified False),
   Ragas faithfulness and context precision (judge: the same gpt-oss-120b model, skipped for refusals), p50/p95
   seconds (rate-limit waits excluded) and USD. Replies are recorded in `evals/llm_cache_rag.json` (prompt-hash
   cache from evals/run.py), so `--offline` replays them with no network. Spend is capped at `MAX_USD`.

Run from backend/: `python evals/sets/run_rag.py` (writes evals/results_rag.json),
`python evals/sets/run_rag.py --live [--provider openrouter]` (also writes evals/results_rag_answers.json),
`python evals/sets/run_rag.py --offline` (replays the recorded answers, prints any changed outcome).
tests/test_rag_eval.py runs the keyless part and the offline replay in CI.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import statistics
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

import numpy as np
import yaml

SETS = Path(__file__).resolve().parent
ROOT = SETS.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SETS.parent))
from app.ai import research  # noqa: E402
from app.data import engine  # noqa: E402

OUT, OUT_ANSWERS = SETS.parent / "results_rag.json", SETS.parent / "results_rag_answers.json"
CACHE = SETS.parent / "llm_cache_rag.json"
FILINGS = ROOT / "storage" / "eval_filings"
ENV = ROOT.parent / ".env"  # GROQ_API_KEY / OPENROUTER_API_KEY live here; never printed or written anywhere
USER, WS, K, SEED = "eval", "rag", 5, 20261115
ARMS = {f"{mode}{'+rerank' if rr else ''}/{chunker}": {"chunker": chunker, "mode": mode, "rerank": rr}
        for chunker in research.CHUNKERS for mode, rr in (("bm25", False), ("vector", False), ("hybrid", False),
                                                          ("hybrid", True))}
BEFORE = "pages_bm25_before"
JUDGE = "openai/gpt-oss-120b"  # the app's model (llm_client.GROQ_MODEL default)
PROVIDERS = {"groq": "https://api.groq.com/openai/v1", "openrouter": "https://openrouter.ai/api/v1"}
MAX_USD = 1.00
QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


# The golden set, each question with its labelled (file, page) set.
def load_set() -> list[dict]:
    rows = yaml.safe_load((SETS / "rag_golden.yaml").read_text(encoding="utf-8"))
    for r in rows:
        r["relevant"] = ({(f, int(p)) for f, p in (x.rsplit(":", 1) for x in r["pages"])} if isinstance(r["file"], list)
                         else {(r["file"], int(p)) for p in r["pages"]})
    return rows


# Does a hit cover one of the labelled pages (same file, page inside page_start..page_end)?
def covers(hit: dict, relevant: set) -> bool:
    return any(f == hit["file"] and hit["page_start"] <= p <= hit["page_end"] for f, p in relevant)


# 1 if a hit in the first k covers a labelled page, else 0.
def recall_at(hits: list[dict], relevant: set, k: int = K) -> int:
    return int(any(covers(h, relevant) for h in hits[:k]))


# 1 / rank of the first hit covering a labelled page (0 when none does).
def reciprocal_rank(hits: list[dict], relevant: set) -> float:
    return next((1 / (i + 1) for i, h in enumerate(hits) if covers(h, relevant)), 0.0)


# Index both filings into the temp workspace and embed every chunk: {chunker: (keys, unit-vector matrix)}.
def index() -> tuple[dict, dict]:
    os.environ.pop("WEALTHOS_RESEARCH_SEARCH", None)  # keyword mode: no database at upload
    for pdf in sorted(FILINGS.glob("*.pdf")):
        research.add(USER, WS, pdf, pdf.stem)
    docs = research._docs(USER, WS)
    vecs, counts = {}, {}
    for chunker in research.CHUNKERS:
        chunks = [(d["doc_id"], c) for d in docs for c in d["chunks"][chunker]]
        vecs[chunker] = ([(doc, c["chunk"]) for doc, c in chunks], np.array(research.embed([c["text"] for _, c in chunks])))
        counts[chunker] = {d["file"]: len(d["chunks"][chunker]) for d in docs}
    return vecs, counts


# Every chunk of one chunker ranked by cosine to the question (vectors are unit length, so a dot product).
def dense_ranking(vecs: dict, chunker: str, question: str) -> list[tuple]:
    keys, matrix = vecs[chunker]
    return [keys[i] for i in np.argsort(-(matrix @ np.array(research.embed([question])[0])), kind="stable")]


# One arm's top CANDIDATES hits for a question and the milliseconds it took (question embedding included).
async def retrieve(vecs: dict, question: str, arm: str) -> tuple[list[dict], float]:
    start = time.perf_counter()
    if arm == BEFORE:  # the app before chunking: BM25 + section boost over whole non-contents pages, no filters
        pages = [{"file": d["file"], **p} for d in research._docs(USER, WS) for p in d["pages"]
                 if not p["toc"] and p["text"].strip()]
        scores = research.bm25_scores(pages, question)
        hits = [{"file": pages[i]["file"], "page_start": pages[i]["page"], "page_end": pages[i]["page"],
                 "score": scores[i], "text": pages[i]["text"]}
                for i in sorted(scores, key=scores.get, reverse=True)[:research.CANDIDATES]]
    else:
        how = ARMS[arm]
        dense = dense_ranking(vecs, how["chunker"], question) if how["mode"] != "bm25" else None
        hits = await research.search(USER, WS, question, **how, k=research.CANDIDATES, dense=dense)
    return hits, (time.perf_counter() - start) * 1000


# Percentile p (50, 95) of a list, rounded.
def pct(xs: list[float], p: int, nd: int = 1) -> float | None:
    return round(float(np.percentile(xs, p)), nd) if xs else None


# Score every arm on the set: per-arm metrics plus each question's (found, reciprocal rank, best top-5 score).
async def retrieval(rows: list[dict], vecs: dict, only: list[str] | None = None) -> tuple[dict, dict]:
    arms, per_q = {}, {r["id"]: {} for r in rows}
    for arm in only or [*ARMS, BEFORE]:
        found, rr, ms, kinds, words = [], [], [], {}, []
        for r in rows:
            hits, took = await retrieve(vecs, r["q"], arm)
            ms.append(took)
            best = max((h["score"] for h in hits[:K]), default=None)
            per_q[r["id"]][arm] = [recall_at(hits, r["relevant"]), round(reciprocal_rank(hits, r["relevant"]), 4),
                                   None if best is None else round(best, 4)]
            if r["answerable"]:
                found.append(per_q[r["id"]][arm][0])
                rr.append(per_q[r["id"]][arm][1])
                words.append(sum(len(h["text"].split()) for h in hits[:K]))  # how much text the model would read
                kinds.setdefault(r["kind"], []).append(found[-1])
        arms[arm] = {"recall_at_5": round(sum(found) / len(found), 3), "mrr": round(sum(rr) / len(rr), 3),
                     "questions": len(found), "p50_ms": pct(ms, 50), "p95_ms": pct(ms, 95),
                     "top5_words_mean": round(sum(words) / len(words)),
                     "recall_at_5_by_kind": {k: round(sum(v) / len(v), 3) for k, v in sorted(kinds.items())}}
        print(f"{arm:24s} recall@5 {arms[arm]['recall_at_5']:.3f}  MRR {arms[arm]['mrr']:.3f}  "
              f"p50 {arms[arm]['p50_ms']} ms  p95 {arms[arm]['p95_ms']} ms", flush=True)
    return arms, per_q


# Dev (1/3) and test (2/3) question ids, stratified by answerable, with a fixed seed.
def split(rows: list[dict]) -> tuple[list[str], list[str]]:
    rng, dev, test = random.Random(SEED), [], []
    for answerable in (True, False):
        ids = sorted(r["id"] for r in rows if r["answerable"] == answerable)
        rng.shuffle(ids)
        cut = round(len(ids) / 3)
        dev, test = dev + ids[:cut], test + ids[cut:]
    return sorted(dev), sorted(test)


# Refusal outcome counts at threshold t: a question is refused when its best score is missing or under t.
def refusals(scores: list, answerable: list[bool], t: float) -> dict:
    refused = [s is None or s < t for s in scores]
    return {"unanswerable_refused": sum(r for r, a in zip(refused, answerable) if not a),
            "unanswerable": answerable.count(False),
            "answerable_refused": sum(r for r, a in zip(refused, answerable) if a), "answerable": answerable.count(True)}


# The threshold that maximises correct refusals + answerable kept (midpoints between scores; lowest wins a tie).
def tune(scores: list, answerable: list[bool]) -> float:
    s = sorted({x for x in scores if x is not None})
    if not s:
        return 0.0
    options = [s[0] - 1, *[(a + b) / 2 for a, b in zip(s, s[1:])], s[-1] + 1]
    good = lambda t: (lambda c: c["unanswerable_refused"] + c["answerable"] - c["answerable_refused"])(  # noqa: E731
        refusals(scores, answerable, t))
    return round(max(options, key=lambda t: (good(t), -t)), 4)


# Pick each arm's refusal threshold on dev, report refusal rates on test.
def thresholds(rows: list[dict], per_q: dict) -> tuple[dict, dict]:
    dev, test = split(rows)
    by_id = {r["id"]: r for r in rows}
    chosen, report = {}, {}
    for arm, how in ARMS.items():
        col = lambda ids: ([per_q[i][arm][2] for i in ids], [by_id[i]["answerable"] for i in ids])  # noqa: E731
        t = tune(*col(dev))
        c = refusals(*col(test), t)
        chosen[arm] = {"threshold": t, "scored_by": "rerank" if how["rerank"] else "bm25"}
        report[arm] = c | {"unanswerable_refused_pct": round(100 * c["unanswerable_refused"] / c["unanswerable"], 1),
                           "answerable_refused_pct": round(100 * c["answerable_refused"] / c["answerable"], 1)}
    return chosen, {"seed": SEED, "dev": dev, "test_questions": len(test), "test": report}


# The arm the app should run: best recall@5, then MRR, then speed.
def best(arms: dict, names) -> str:
    return max(names, key=lambda a: (arms[a]["recall_at_5"], arms[a]["mrr"], -arms[a]["p50_ms"]))


# Lower-case, NFKC, plain quotes and dashes, single spaces.
def norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).translate(QUOTES).lower().split())


# Share of a question's key facts found in an answer (numbers also match with $ , and spaces stripped).
def fact_share(facts: list[str], answer: str) -> float:
    a = norm(answer)
    bare = re.sub(r"[$,\s]", "", a)
    return sum(norm(f) in a or re.sub(r"[$,\s]", "", norm(f)) in bare for f in facts) / len(facts) if facts else 0.0


class Stop(Exception):
    """The run must end now: spend cap or a provider's daily limit."""


# Answer every question with each live arm through research.answer; offline replays the cache (no judge, no file).
async def answers(rows: list[dict], vecs: dict, live_arms: list[str], chosen: dict, offline: bool,
                  provider: str = "groq", done: dict | None = None, spent: float = 0.0,
                  max_usd: float = MAX_USD, only: set | None = None) -> tuple[list[dict], str | None]:
    sys.path.insert(0, str(SETS.parent))
    import run as evals
    from app.ai import llm_client as llm

    stats, log, out, done, stopped = {"calls": 0}, [], [], done or {}, None
    evals.install_cache(offline, stats, CACHE)
    llm.CALL_LOG.set(log)
    if provider == "groq" and not offline:  # never let a rate-limited Groq call fall through to another model
        llm._PROVIDERS = [p for p in llm._PROVIDERS if p[0] != "openrouter"]
    track = llm._track_usage

    # Keep the provider's own billed cost next to the list-price estimate (OpenRouter reports it; Groq free tier: $0).
    async def tracked(usage, *a, **kw):
        await track(usage, *a, **kw)
        if (entries := llm.CALL_LOG.get()):
            entries[-1]["billed"] = float(usage.get("cost") or 0.0)
    llm._track_usage = tracked
    seen, cached_call = {}, llm.call_llm

    # Remember what the model wrote (research.answer hides it when the checks fail).
    async def recording(system, user, **kw):
        seen["raw"] = await cached_call(system, user, **kw)
        return seen["raw"]
    llm.call_llm = recording
    real_search = research.search

    # Remember which chunks the answerer was shown.
    async def shown(*a, **kw):
        seen["hits"] = await real_search(*a, **kw)
        return seen["hits"]
    research.search = shown
    try:
        for r in rows:
            for arm in live_arms:
                if only is not None and (arm, r["id"]) not in only:  # a replay covers the recorded rows only
                    continue
                if (arm, r["id"]) in done:
                    out.append(done[(arm, r["id"])])
                    continue
                if spent + sum(x.get("billed", 0) for x in log) > max_usd:
                    raise Stop(f"spend cap ${max_usd} reached")
                setattr(research, "REFUSE_RERANK" if ARMS[arm]["rerank"] else "REFUSE_BM25", chosen[arm]["threshold"])
                dense = dense_ranking(vecs, ARMS[arm]["chunker"], r["q"]) if ARMS[arm]["mode"] != "bm25" else None
                seen.clear()
                first, waited, calls = len(log), stats.get("waited", 0), stats["calls"]
                start = time.perf_counter()
                res = await research.answer(USER, r["q"], WS, **ARMS[arm], dense=dense)
                seconds = time.perf_counter() - start - (stats.get("waited", 0) - waited)
                if stats["calls"] > calls and not seen.get("raw"):
                    raise Stop(f"{provider} gave no answer after waiting (daily limit or outage) at {arm} {r['id']}")
                fresh = log[first:]
                out.append({"id": r["id"], "arm": arm, "kind": r["kind"], "answerable": r["answerable"],
                            "refused": res["text"] == research.NOT_FOUND, "verified": res["verified"],
                            "warnings": res["warnings"], "text": res["text"], "raw": seen.get("raw", ""),
                            "fact_share": round(fact_share(r["facts"], res["text"]), 3) if r["answerable"] else None,
                            "shown": [[h["doc_id"], h["chunk"]] for h in seen.get("hits", [])],
                            "seconds": round(seconds, 3), "llm_cached": stats["calls"] > calls and not fresh,
                            "tokens": sum(x["prompt_tokens"] + x["completion_tokens"] for x in fresh),
                            "prompt_tokens": sum(x["prompt_tokens"] for x in fresh),
                            "completion_tokens": sum(x["completion_tokens"] for x in fresh),
                            "usd_llm_client": sum(x["usd"] for x in fresh), "billed": sum(x.get("billed", 0) for x in fresh),
                            "served_by": sorted({f"{x['provider']}:{x['model']}" for x in fresh})})
                if not offline:
                    print(len(out), arm, r["id"], "refused" if out[-1]["refused"] else f"facts {out[-1]['fact_share']}",
                          "verified" if res["verified"] else "REJECTED", flush=True)
    except Stop as e:  # keep the rows already answered; a re-run replays them from the cache
        stopped = str(e)
    finally:
        research.search, llm.call_llm, llm._track_usage = real_search, cached_call, track
    return out, stopped


# Ragas faithfulness and context precision for every row that was answered, judged by the same model.
async def judge(rows: list[dict], provider: str, spent: float, max_usd: float, done: dict) -> dict:
    from openai import AsyncOpenAI, RateLimitError
    from ragas.llms import llm_factory
    from ragas.metrics.collections import ContextPrecisionWithoutReference, Faithfulness
    from app.ai import llm_client as llm

    key = os.environ["GROQ_API_KEY" if provider == "groq" else "OPENROUTER_API_KEY"]
    client = AsyncOpenAI(api_key=key, base_url=PROVIDERS[provider], max_retries=0)
    create, used = client.chat.completions.create, {"prompt_tokens": 0, "completion_tokens": 0, "billed": 0.0, "waited": 0}

    # Count the judge's tokens and cost; wait out per-minute rate limits, stop on a daily one or at the spend cap.
    async def counted(*a, **kw):
        for _ in range(20):
            if spent + used["billed"] > max_usd:
                raise Stop(f"spend cap ${max_usd} reached")
            try:
                r = await create(*a, **kw)
            except RateLimitError as e:
                if "per day" in str(e):
                    raise Stop(f"{provider} daily limit: {str(e)[:160]}") from e
                used["waited"] += 20
                await asyncio.sleep(20)
                continue
            used["prompt_tokens"] += r.usage.prompt_tokens
            used["completion_tokens"] += r.usage.completion_tokens
            used["billed"] += float((r.usage.model_extra or {}).get("cost") or 0.0)
            return r
        raise Stop(f"{provider} kept rate-limiting the judge")
    client.chat.completions.create = counted
    model = llm_factory(JUDGE, client=client, max_tokens=4096, temperature=0, reasoning_effort="low")
    faith, precision = Faithfulness(llm=model), ContextPrecisionWithoutReference(llm=model)
    texts = {(d["doc_id"], c["chunk"], c["chunker"]): c["text"][:6000]
             for d in research._docs(USER, WS) for name in research.CHUNKERS for c in d["chunks"][name]}
    questions = {r["id"]: r["q"] for r in load_set()}
    gate, stopped = asyncio.Semaphore(6), None

    # Judge one row (both metrics); a judge error is recorded, never scored as 0.
    async def one(row):
        prev = done.get((row["arm"], row["id"]), {})
        if "faithfulness" in prev or "judge_error" in prev:
            row.update({k: prev[k] for k in ("faithfulness", "context_precision", "judge_error") if k in prev})
            return
        contexts = [texts[(doc, n, ARMS[row["arm"]]["chunker"])] for doc, n in row["shown"]]
        async with gate:
            try:
                f, p = await asyncio.gather(faith.ascore(user_input=questions[row["id"]], response=row["raw"],
                                                         retrieved_contexts=contexts),
                                            precision.ascore(user_input=questions[row["id"]], response=row["raw"],
                                                             retrieved_contexts=contexts))
                row["faithfulness"], row["context_precision"] = round(f.value, 4), round(p.value, 4)
            except Stop:
                raise
            except Exception as e:
                row["judge_error"] = f"{type(e).__name__}: {str(e)[:160]}"

    try:
        await asyncio.gather(*(one(r) for r in rows if not r["refused"] and r["raw"]))
    except Stop as e:
        stopped = str(e)
    llm_price = llm._PRICES["groq"]
    return {"model": f"{provider}:{JUDGE}", **used, "stopped": stopped,
            "usd_list": round((used["prompt_tokens"] * llm_price[0] + used["completion_tokens"] * llm_price[1]) / 1e6, 6)}


# Per-arm answer metrics over the recorded rows.
def summarize(rows: list[dict], live_arms: list[str]) -> dict:
    from app.ai import llm_client as llm
    price = llm._PRICES["groq"]
    out = {}
    for arm in live_arms:
        rs = [r for r in rows if r["arm"] == arm]
        if not rs:
            continue
        ans, una = [r for r in rs if r["answerable"]], [r for r in rs if not r["answerable"]]
        mean = lambda k: round(statistics.mean(v), 3) if (v := [r[k] for r in rs if r.get(k) is not None]) else None  # noqa: E731
        live = [r["seconds"] for r in rs if not r["llm_cached"]]
        out[arm] = {"questions": len(rs), "answerable": len(ans), "unanswerable": len(una),
                    "answer_accuracy": round(statistics.mean(r["fact_share"] for r in ans), 3) if ans else None,
                    "refused_unanswerable": sum(r["refused"] for r in una),
                    "refused_unanswerable_pct": round(100 * sum(r["refused"] for r in una) / len(una), 1) if una else None,
                    "wrongly_refused_answerable": sum(r["refused"] for r in ans),
                    "number_check_rejects": sum(not r["verified"] for r in rs),
                    "ragas_faithfulness": mean("faithfulness"), "ragas_context_precision": mean("context_precision"),
                    "judged": sum("faithfulness" in r for r in rs), "judge_errors": sum("judge_error" in r for r in rs),
                    "p50_seconds": pct(live, 50, 2), "p95_seconds": pct(live, 95, 2),
                    "usd_per_question_list": round(sum((r["prompt_tokens"] * price[0] + r["completion_tokens"] * price[1])
                                                       / 1e6 for r in rs) / len(rs), 6),
                    "usd_per_question_llm_client": round(sum(r["usd_llm_client"] for r in rs) / len(rs), 6),
                    "usd_per_question_billed": round(sum(r["billed"] for r in rs) / len(rs), 6)}
    return out


# The short git sha of the code that ran (with "-dirty" if tracked files had local edits).
def git_sha() -> str:
    run = lambda *a: subprocess.run(["git", *a], capture_output=True, text=True, cwd=ROOT).stdout.strip()  # noqa: E731
    return run("rev-parse", "--short", "HEAD") + ("-dirty" if run("status", "--porcelain", "--untracked-files=no") else "")


# Keyless part: index, every retrieval arm, thresholds. Returns (result dict, vectors) for the live part and the test.
def keyless(rows: list[dict]) -> tuple[dict, dict]:
    start = time.perf_counter()
    vecs, counts = index()
    index_s = round(time.perf_counter() - start, 1)
    arms, per_q = asyncio.run(retrieval(rows, vecs))
    chosen, refusal = thresholds(rows, per_q)
    return {"set": {"file": "evals/sets/rag_golden.yaml", "questions": len(rows),
                    "answerable": sum(r["answerable"] for r in rows), "filings": sorted(p.name for p in FILINGS.glob("*.pdf"))},
            "embed_model": research.EMBED_MODEL, "embed_dims": int(next(iter(vecs.values()))[1].shape[1]),
            "rerank_model": research.RERANK_MODEL, "top_k": K, "candidates": research.CANDIDATES,
            "chunk_counts": counts, "index_and_embed_seconds": index_s, "arms": arms,
            "best_arm": best(arms, ARMS), "best_fixed_arm": best(arms, [a for a in ARMS if a.endswith("/fixed")]),
            "thresholds": chosen, "refusal": refusal, "questions": per_q}, vecs


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="also answer and judge (keys from the repo .env)")
    ap.add_argument("--offline", action="store_true", help="replay the recorded answers from llm_cache_rag.json")
    ap.add_argument("--provider", choices=list(PROVIDERS), default="groq", help="where gpt-oss-120b runs for --live")
    ap.add_argument("--max-usd", type=float, default=MAX_USD)
    args = ap.parse_args()
    if not FILINGS.exists():
        sys.exit("run evals/sets/fetch_filings.py first")
    if args.live:
        from dotenv import load_dotenv
        load_dotenv(ENV)
        os.environ["WEALTHOS_DB_URL"] = ""  # no usage rows or cost-cap lookups, as in evals/run.py
        if args.provider == "openrouter":  # same model through llm_client's second slot (as evals/run.py --name openrouter)
            os.environ.update(GROQ_API_KEY="", LLM_FALLBACK_MODEL=JUDGE)
    rows = load_set()
    with tempfile.TemporaryDirectory() as tmp:
        engine.DATA_ROOT = Path(tmp) / "users"
        result, vecs = keyless(rows)
        sha, run_at = git_sha(), time.strftime("%Y-%m-%dT%H:%M:%S%z")
        if not (args.live or args.offline):
            OUT.write_text(json.dumps(result | {"run_at": run_at, "git_sha": sha}, indent=1), encoding="utf-8")
            print("best arm", result["best_arm"], "| thresholds", {a: t["threshold"] for a, t in result["thresholds"].items()})
            sys.exit()
        committed = json.loads(OUT.read_text(encoding="utf-8"))
        live_arms = [a for a in ARMS if a.endswith("/structure")] + [committed["best_fixed_arm"]]
        prev = json.loads(OUT_ANSWERS.read_text(encoding="utf-8")) if OUT_ANSWERS.exists() else {"rows": []}
        if args.offline:
            was = {(r["arm"], r["id"]): r for r in prev["rows"]}
            got, _ = asyncio.run(answers(rows, vecs, prev["arms"], prev["thresholds"], offline=True, only=set(was)))
            drift = [(r["arm"], r["id"]) for r in got if (r["arm"], r["id"]) in was
                     and (r["refused"], r["verified"]) != (was[(r["arm"], r["id"])]["refused"], was[(r["arm"], r["id"])]["verified"])]
            print(f"replayed {len(got)} answers, {len(drift)} changed outcome(s): {drift}")
            sys.exit(1 if drift else 0)
        done = {(r["arm"], r["id"]): r for r in prev["rows"]}  # an earlier, cut-short run: keep its rows
        spent_before = prev.get("spend", {}).get("billed_total", 0.0)
        rows_out, stopped = asyncio.run(answers(rows, vecs, live_arms, committed["thresholds"], False, args.provider,
                                                done, spent_before, args.max_usd))
        print("stopped:", stopped)
        billed = spent_before + sum(r["billed"] for r in rows_out if (r["arm"], r["id"]) not in done)
        judged = asyncio.run(judge(rows_out, args.provider, billed, args.max_usd, done)) if not stopped else None
        OUT_ANSWERS.write_text(json.dumps({
            "provider": args.provider, "answer_model": sorted({m for r in rows_out for m in r["served_by"]}),
            "arms": live_arms, "thresholds": {a: committed["thresholds"][a] for a in live_arms},
            "summary": summarize(rows_out, live_arms), "judge": judged, "stopped": stopped or (judged or {}).get("stopped"),
            "spend": {"billed_total": round(billed + (judged or {}).get("billed", 0.0), 6), "cap": args.max_usd,
                      "how": "billed: the provider's reported cost (OpenRouter usage.cost; Groq free tier bills $0); "
                             "usd_per_question_list: tokens x Groq list price for gpt-oss-120b (llm_client._PRICES['groq'])"},
            "retrieval_results_git_sha": committed["git_sha"], "run_at": run_at, "git_sha": sha, "rows": rows_out},
            indent=1, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(summarize(rows_out, live_arms), indent=1))
