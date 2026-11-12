"""
# evals/run.py - the golden eval set and its CI smoke run

Measures the assistant end to end on synthetic data whose right answers are computed here, independently of the engine:

1. **Fixtures** (`make_fixtures`, rows from `app/data/samples.py`): 24 months of a bank statement (salary, rent, groceries, power, Swiggy, and one row
   whose narration is a prompt-injection attempt), a Zerodha tradebook (a long-term and a short-term sale, one open
   buy) and a holdings export. "Today" is pinned to 2026-10-26 so the answers never drift.
2. **Truths** (`truths`): every expected value, summed in plain Python from the fixture rows.
3. **Cases** (`cases.yaml`): question -> accepted plan kinds -> expected values (by truth name, or literal numbers).
4. **Score**: plan match %, value accuracy % (every expected value appears in the answer rows, within 0.5%), trusted
   badge % on value cases, number-check rejects, abstention on advice questions, LLM calls per question, and on a
   live run p50/p95 seconds per question plus tokens and list-price USD per question (`llm_client.CALL_LOG`).
   Seconds exclude the time spent waiting out a provider rate limit (`waited_seconds` in the summary).

LLM replies are cached in `evals/llm_cache.json` keyed by a hash of the prompt:
- `python evals/run.py` runs live and records every new reply (needs the Groq / OpenRouter keys); a cached prompt is
  replayed, so for fresh latency and cost numbers move `llm_cache.json` aside first;
- `python evals/run.py --offline` replays the cache only (no network; a miss is an error; writes no result file):
  this is what CI runs;
- `--only N` runs the first N cases;
- `--name X` keeps a separate run (another provider or model) in `results_X.json` with its replies in
  `llm_cache_X.json`, e.g. `GROQ_API_KEY= LLM_FALLBACK_MODEL=openai/gpt-oss-120b python evals/run.py --name openrouter`.
"""
from __future__ import annotations

import argparse
import asyncio
import calendar
import hashlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ai import agent  # noqa: E402
from app.data import engine  # noqa: E402
from app.data import timeexpr  # noqa: E402
from app.data.ingest import parse_file  # noqa: E402
from app.data.samples import HOLDINGS, TRADES, bank_rows, write_sample_files  # noqa: E402

TODAY = date(2026, 10, 26)
CACHE = Path(__file__).with_name("llm_cache.json")
USER = "eval"


# ── Fixtures and their right answers ───────────────────────────────────────────────────────────────────────

# Write the sample files and load them for the eval user (into whatever engine.DATA_ROOT points at).
def make_fixtures(folder: Path) -> None:
    for path in write_sample_files(folder):
        engine.save_tables(USER, parse_file(path))


# Every expected value, computed straight from the fixture rows (never through the engine).
def truths() -> dict[str, float]:
    rows = bank_rows()
    fy, last_fy = (date(2026, 4, 1), TODAY), (date(2025, 4, 1), date(2026, 3, 31))
    month = lambda y, m: (date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1]))
    within = lambda d, span: span[0] <= d <= span[1]

    # Sum of one column over a date span, optionally for rows whose text contains a word.
    def total(span, col, word=""):  # col 2 = debit (money out), 3 = credit (money in)
        return sum(r[col] for r in rows if within(r[0], span) and word.lower() in r[1].lower())

    t = {"spend_fy": total(fy, 2), "spend_last_fy": total(last_fy, 2), "income_fy": total(fy, 3),
         "income_last_fy": total(last_fy, 3), "rent_fy": total(fy, 2, "rent"), "swiggy_last_fy": total(last_fy, 2, "swiggy"),
         "spend_last_month": total(month(2026, 9), 2), "count_fy": sum(within(r[0], fy) for r in rows)}
    for m in range(4, 11):
        t[f"spend_2026_{m:02d}"] = total(month(2026, m), 2)
    t["savings_fy"] = t["income_fy"] - t["spend_fy"]
    t["rate_fy"] = round(100 * t["savings_fy"] / t["income_fy"], 1)
    t["trades_fy"] = sum(within(d, fy) for _, d, *_ in TRADES)
    t["buy_last_fy"] = sum(q * p for _, d, side, q, p in TRADES if side == "buy" and within(d, last_fy))
    t["sell_fy"] = sum(q * p for _, d, side, q, p in TRADES if side == "sell" and within(d, fy))
    t["gain_infy"], t["gain_tcs"] = 10 * (1700 - 1400), 5 * (3900 - 3600)  # one buy lot each, so FIFO is trivial
    t["portfolio_value"] = sum(v for *_, v in HOLDINGS)
    t["invested_value"] = sum(q * a for _, q, a, _ in HOLDINGS)
    t["unrealized_pnl"] = t["portfolio_value"] - t["invested_value"]
    t["holdings_count"] = len(HOLDINGS)
    return t


# ── LLM cache: record live replies, or replay them offline ─────────────────────────────────────────────────

# Wrap the two LLM entry points: a cached prompt is replayed; a new one is sent live and recorded (unless offline).
def install_cache(offline: bool, stats: dict, path: Path = CACHE) -> None:
    from app.ai import llm_client as llm

    cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    live = {"json": llm.call_json, "text": llm.call_llm}

    # Return the stored reply for this prompt, or call the model and store it.
    async def cached(k: str, call):
        if stats.get("inside"):  # call_json's own inner call_llm: run it live, it is cached as the JSON reply
            return await call()
        stats["calls"] += 1
        stats.setdefault("used", set()).add(k)
        if k not in cache:
            if offline:
                raise LookupError(f"no cached LLM reply for prompt {k}; run evals/run.py live once to record it")
            for wait in (0, 15, 30, 60, 60):  # no answer is usually a rate limit: wait it out (not counted as latency)
                await asyncio.sleep(wait)
                stats["waited"] = stats.get("waited", 0) + wait
                out = await call()
                if out:
                    break
            if not out:  # still no answer (outage): report it, never record it as the answer
                return out
            cache[k] = out
            path.write_text(json.dumps(cache, indent=0, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return cache[k]

    # Cached stand-in for the JSON model call.
    async def call_json(system, user, model_cls, **kw):
        k = hashlib.sha256(f"{system}\x1f{user}\x1f{model_cls.__name__}".encode()).hexdigest()[:24]
        out = await cached(k, lambda: _dump(live["json"](system, user, model_cls, **kw), stats))
        return model_cls.model_validate(out) if out is not None else None

    # Cached stand-in for the text model call.
    async def call_llm(system, user, **kw):
        k = hashlib.sha256(f"{system}\x1f{user}".encode()).hexdigest()[:24]
        return await cached(k, lambda: live["text"](system, user, **kw))

    llm.call_json, llm.call_llm = call_json, call_llm
    stats["cache"] = cache


# Run a model call and turn its result into plain data.
async def _dump(coro, stats: dict):
    stats["inside"] = True
    try:
        out = await coro
    finally:
        stats["inside"] = False
    return out.model_dump() if out is not None else None


# ── Scoring ────────────────────────────────────────────────────────────────────────────────────────────────

# True when every expected number appears among the answer's row values (within 0.5%, and at least 1).
def values_match(expected: list[float], rows: list[dict]) -> bool:
    got = [v for r in rows for v in r.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return all(any(abs(g - e) <= max(1.0, abs(e) * 0.005) for g in got) for e in expected)


# Ask every case; returns (per-case results, summary).
async def run(cases: list[dict], t: dict, stats: dict) -> tuple[list[dict], dict]:
    from app.ai import llm_client as llm

    results, log = [], []
    llm.CALL_LOG.set(log)  # live provider replies land here; cache hits add nothing
    for c in cases:
        start, waited, first = time.perf_counter(), stats.get("waited", 0), len(log)
        a = await agent.ask(USER, c["q"])
        seconds = time.perf_counter() - start - (stats.get("waited", 0) - waited)
        calls = log[first:]
        expected = [t[v] if isinstance(v, str) else v for v in c.get("values", [])]
        results.append({"q": c["q"], "kind": a.plan.kind if a.plan else None,
                        "plan_ok": bool(a.plan) and a.plan.kind in c["kinds"],
                        "values_ok": values_match(expected, a.rows) if expected else None, "badge": a.badge,
                        "verified": a.verified, "seconds": round(seconds, 2),
                        "llm_seconds": [x["seconds"] for x in calls],
                        "tokens": sum(x["prompt_tokens"] + x["completion_tokens"] for x in calls),
                        "usd": round(sum(x["usd"] for x in calls), 6),
                        "models": sorted({f"{x['provider']}:{x['model']}" for x in calls}),
                        "expected": expected, "text": a.text.split("\n\n_")[0][:200]})
    valued = [r for r in results if r["values_ok"] is not None]
    advice = [r for r, c in zip(results, cases) if c["kinds"] == ["out_of_scope"]]
    pct = lambda xs: round(100 * sum(xs) / len(xs), 1) if xs else None
    return results, {
        "cases": len(results), "plan_match_pct": pct([r["plan_ok"] for r in results]),
        "value_accuracy_pct": pct([r["values_ok"] for r in valued]),
        "trusted_pct_on_value_cases": pct([r["badge"] == "trusted" for r in valued]),
        "number_check_rejects": sum(not r["verified"] for r in results),
        "advice_abstention_pct": pct([r["kind"] == "out_of_scope" for r in advice]),
        **latency_cost(results)}


# Latency percentiles and cost per question over a run's rows (the usd is list price: Groq's free tier bills $0).
def latency_cost(results: list[dict]) -> dict:
    secs, llm_secs = [r["seconds"] for r in results], [s for r in results for s in r["llm_seconds"]]
    p95 = lambda xs: round(statistics.quantiles(xs, n=20)[18], 2) if len(xs) > 1 else None
    return {"avg_seconds": round(statistics.mean(secs), 2), "p50_seconds": round(statistics.median(secs), 2),
            "p95_seconds": p95(secs), "llm_call_p50_seconds": round(statistics.median(llm_secs), 2) if llm_secs else None,
            "llm_call_p95_seconds": p95(llm_secs),
            "tokens_per_question": round(sum(r["tokens"] for r in results) / len(results)),
            "usd_per_question": round(sum(r["usd"] for r in results) / len(results), 6),
            "usd_total": round(sum(r["usd"] for r in results), 6),
            "models": sorted({m for r in results for m in r["models"]})}


# Load the cases, build the fixtures in a temp folder, run, print and save the scores.
def evaluate(offline: bool = False, only: int = 0, name: str = "") -> tuple[list[dict], dict]:
    cases = yaml.safe_load(Path(__file__).with_name("cases.yaml").read_text(encoding="utf-8"))
    cases = cases[:only] if only else cases
    stats = {"calls": 0}
    os.environ["WEALTHOS_DB_URL"] = ""  # no usage logging / cost-cap lookups: an unreachable DB costs 5 s per call
    timeexpr.today_ist = lambda: TODAY
    cache = CACHE.with_name(f"llm_cache_{name}.json") if name else CACHE
    install_cache(offline, stats, cache)
    with tempfile.TemporaryDirectory() as tmp:
        engine.DATA_ROOT = Path(tmp) / "users"
        make_fixtures(Path(tmp))
        results, summary = asyncio.run(run(cases, truths(), stats))
    summary["llm_calls_per_question"] = round(stats["calls"] / len(cases), 2)
    summary["waited_seconds"] = stats.get("waited", 0)
    summary["run_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    summary["git_sha"] = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                        cwd=ROOT).stdout.strip()
    if not offline and not only:  # a full live run: keep only the replies the current prompts use
        kept = {k: v for k, v in stats["cache"].items() if k in stats.get("used", ())}
        cache.write_text(json.dumps(kept, indent=0, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return results, summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="replay cached LLM replies only (CI)")
    ap.add_argument("--only", type=int, default=0, help="run only the first N cases")
    ap.add_argument("--name", default="", help="write results_NAME.json and use llm_cache_NAME.json")
    args = ap.parse_args()
    results, summary = evaluate(args.offline, args.only, args.name)
    for r in results:
        ok = r["plan_ok"] and r["values_ok"] is not False
        values = "" if r["values_ok"] is None else "values ok" if r["values_ok"] else f"values WRONG, expected {r['expected']}"
        print(f"{'ok ' if ok else 'BAD'} {r['kind'] or '-':12} {r['badge']:11} {r['q'][:60]:60} {values}")
    print(json.dumps(summary, indent=1))
    if not args.only and not args.offline:  # an offline replay has no real timings: keep the live file
        Path(__file__).with_name(f"results_{args.name}.json" if args.name else "results.json").write_text(
            json.dumps({"summary": summary, "results": results}, indent=1, ensure_ascii=False), encoding="utf-8")
