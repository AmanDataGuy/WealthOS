"""
# evals/sets/run_sets.py - the labelled sets: measured rates for the safety and mapping pieces

Five small sets, each scored against labels written by hand (all data synthetic):

- **pii** (`pii.yaml`): `redact_pii` recall per type, precision per placeholder label, and how many amounts / dates /
  references survive.
- **sql** (`sql_attacks.yaml`): each query through `guard_sql`, then (if it passes) on `engine.open_readonly`;
  block rate on attacks, escapes, and the false-block rate on legitimate queries.
- **numbers**: a perturbation grid for `check_numbers` over ten answer tables of the golden eval (values from
  `evals/run.py` `truths()`): true sentences in several formats, and wrong ones (off by 0.6% to x100, lakh written as
  crore, wrong currency, made-up numbers, sums of two other cells). False-accept and false-reject rates.
- **deflated_sharpe**: `strategy.dsr` against the published worked example, plus a Monte-Carlo check of its
  expected-maximum-Sharpe term.
- **mapping** (`mapping.yaml`): `mapping.suggest` on 31 header rows; shape and column accuracy and how often each
  confidence label was right. Keyless arm = rules only (the AI step stubbed out); `--live` adds the rules+AI arm
  (one LLM call per row that rules can't finish; replies cached in `evals/llm_cache_mapping.json`).

`python evals/sets/run_sets.py` writes `evals/results_sets.json` (keeps a previously recorded live arm);
`--live` also runs the AI arm. `tests/test_eval_sets.py` re-runs the keyless sets in CI against that file.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import re
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import yaml

SETS = Path(__file__).resolve().parent
sys.path.insert(0, str(SETS.parent))
import run as golden  # noqa: E402  (evals/run.py: fixtures, truths, the LLM cache)

from app.ai.pii_redaction import redact_pii  # noqa: E402
from app.ai.verify import SqlRejected, check_numbers, guard_sql  # noqa: E402
from app.data import engine, mapping  # noqa: E402
from app.data.ingest import ExtractedTable, SourceRef  # noqa: E402
from app.data.ingest.tabular import _profile  # noqa: E402
from app.features.strategy import dsr  # noqa: E402

OUT = SETS.parent / "results_sets.json"
MAPPING_CACHE = SETS.parent / "llm_cache_mapping.json"
_LABEL = re.compile(r"\[REDACTED_([A-Z]+)\]")
pct = lambda a, b: round(100 * a / b, 1) if b else None  # noqa: E731


def _load(name: str) -> list[dict]:
    return yaml.safe_load((SETS / name).read_text(encoding="utf-8"))


# ── PII redaction ───────────────────────────────────────────────────────────────────────────────────────────

# A labelled value is hidden when none of its 4+ character pieces survive in the redacted text.
def _hidden(value: str, out: str) -> bool:
    return not any(p in out for p in re.findall(r"[A-Za-z0-9@.+]{4,}", value))


# Score redact_pii on pii.yaml: recall per type, precision per placeholder, survival of must-keep strings.
def pii() -> dict:
    per = defaultdict(lambda: {"labelled": 0, "hidden": 0, "emitted": 0, "right": 0})
    misses, wrongly_redacted, kept, keep_total, gap_total, gap_hidden = [], [], 0, 0, 0, 0
    for row in _load("pii.yaml"):
        out = redact_pii(row["text"])
        expected = Counter(t for t, _ in row.get("hide", []))
        for typ, value in row.get("hide", []):
            per[typ]["labelled"] += 1
            ok = _hidden(value, out)
            per[typ]["hidden"] += ok
            gap_total, gap_hidden = gap_total + bool(row.get("gap")), gap_hidden + (ok and bool(row.get("gap")))
            if not ok:
                misses.append({"type": typ, "text": row["text"], "out": out, "known_gap": bool(row.get("gap"))})
        for label, n in Counter(_LABEL.findall(out)).items():
            per[label]["emitted"] += n
            per[label]["right"] += min(n, expected[label])
        for k in row.get("keep", []):
            keep_total += 1
            kept += k in out
            if k not in out:
                wrongly_redacted.append({"keep": k, "text": row["text"], "out": out})
    labelled, hidden = sum(p["labelled"] for p in per.values()), sum(p["hidden"] for p in per.values())
    emitted, right = sum(p["emitted"] for p in per.values()), sum(p["right"] for p in per.values())
    return {"rows": len(_load("pii.yaml")), "recall_pct": pct(hidden, labelled),
            "recall_without_known_gaps_pct": pct(hidden - gap_hidden, labelled - gap_total),
            "precision_pct": pct(right, emitted), "keep_survival_pct": pct(kept, keep_total),
            "by_type": {t: {"recall_pct": pct(p["hidden"], p["labelled"]), "precision_pct": pct(p["right"], p["emitted"]),
                            **p} for t, p in sorted(per.items())},
            "misses": misses, "wrongly_redacted": wrongly_redacted}


# ── guard_sql attacks ───────────────────────────────────────────────────────────────────────────────────────

# Run every query in sql_attacks.yaml through guard_sql and then the locked engine, on the golden fixtures.
def sql_attacks() -> dict:
    rows, out = _load("sql_attacks.yaml"), []
    with tempfile.TemporaryDirectory() as tmp:
        engine.DATA_ROOT = Path(tmp) / "users"
        golden.make_fixtures(Path(tmp))
        schema = engine.schema(engine.open_readonly(golden.USER))
        for r in rows:
            start, outcome, why = time.perf_counter(), "ran", ""
            try:
                safe = guard_sql(r["sql"], schema)
            except SqlRejected as e:
                outcome, why = "guard", str(e)[:120]
            else:
                try:
                    engine._execute(engine.open_readonly(golden.USER), safe)
                except (ValueError, TimeoutError) as e:
                    outcome, why = "engine", str(e)[:120]
            out.append({"sql": r["sql"], "family": r.get("family", "legit"), "legit": bool(r.get("ok")),
                        "outcome": outcome, "why": why, "seconds": round(time.perf_counter() - start, 2)})
    attacks, legit = [r for r in out if not r["legit"]], [r for r in out if r["legit"]]
    escaped = [r for r in attacks if r["outcome"] == "ran" and r["family"] != "resource"]
    blocked = [r for r in attacks if r["outcome"] != "ran"]
    return {"attacks": len(attacks), "blocked_by_guard": sum(r["outcome"] == "guard" for r in attacks),
            "blocked_by_engine": sum(r["outcome"] == "engine" for r in attacks),
            "resource_ran_within_limits": sum(r["outcome"] == "ran" and r["family"] == "resource" for r in attacks),
            "escaped": len(escaped),  # block rate leaves out "resource" rows: running them inside the limits is allowed
            "block_rate_pct": pct(sum(r["family"] != "resource" for r in blocked), sum(r["family"] != "resource" for r in attacks)),
            "legit": len(legit), "false_block_rate_pct": pct(sum(r["outcome"] != "ran" for r in legit), len(legit)),
            "max_seconds": max(r["seconds"] for r in out), "rows": out}


# ── check_numbers perturbation grid ─────────────────────────────────────────────────────────────────────────

# 1234567.0 -> "12,34,567" (Indian grouping), keeping up to 2 decimals when the value has them.
def _indian(v: float) -> str:
    whole, frac = f"{abs(v):.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    groups = [head[max(0, i - 2):i] for i in range(len(head), 0, -2)][::-1]
    s = ",".join(groups + [tail]) if head else tail
    return ("-" if v < 0 else "") + s + ("" if frac == "00" else "." + frac)


# The ten answer tables (rows as the engine returns them) and the value each sentence states.
def _tables() -> list[tuple[str, list[dict], float, bool]]:
    t = golden.truths()
    months = [{"month": f"2026-{m:02d}", "total": t[f"spend_2026_{m:02d}"]} for m in range(4, 11)]
    return [  # (sentence with {x}, rows, headline value, is a percentage)
        ("You spent {x} this financial year.", [{"total": t["spend_fy"]}], t["spend_fy"], False),
        ("Last financial year you spent {x}.", [{"total": t["spend_last_fy"]}], t["spend_last_fy"], False),
        ("Money in this financial year: {x}.", [{"total": t["income_fy"]}], t["income_fy"], False),
        ("You saved {x} this FY.", [{"income": t["income_fy"], "spend": t["spend_fy"], "savings": t["savings_fy"]}],
         t["savings_fy"], False),
        ("Your savings rate this FY is {x}.", [{"savings_rate_pct": t["rate_fy"]}], t["rate_fy"], True),
        ("In July 2026 you spent {x}.", months, t["spend_2026_07"], False),
        ("Your realized gain on INFY was {x}.", [{"symbol": "INFY", "gain": t["gain_infy"]},
                                                 {"symbol": "TCS", "gain": t["gain_tcs"]}], t["gain_infy"], False),
        ("Your portfolio is worth {x}.", [{"value": t["portfolio_value"], "invested": t["invested_value"],
                                          "pnl": t["unrealized_pnl"]}], t["portfolio_value"], False),
        ("You paid {x} in rent this financial year.", [{"total": t["rent_fy"]}], t["rent_fy"], False),
        ("You spent {x} on Swiggy last financial year.", [{"total": t["swiggy_last_fy"]}], t["swiggy_last_fy"], False),
    ]


# Every (variant, written number, should it pass) for one headline value.
def _variants(v: float, is_pct: bool, rows: list[dict], rng: random.Random) -> list[tuple[str, str, bool]]:
    money = (lambda n: f"{round(n, 1)}%") if is_pct else (lambda n: "₹" + _indian(round(n)))
    out = [("exact", money(v), True), ("within_0.4pct", money(v * 1.004), True)]
    if not is_pct:
        out += [("western_commas", f"Rs {round(v):,}", True), ("rupees_word", f"{round(v):,} rupees", True)]
        if v >= 1e5:
            out += [("lakh_rounded", f"₹{v / 1e5:.2f} lakh", True), ("lakh_as_crore", f"₹{v / 1e5:.2f} crore", False)]
        out += [("dollar_sign", f"${round(v):,}", False)]
    out += [(f"off_{name}", money(v * f), False) for name, f in
            [("+0.6pct", 1.006), ("-1pct", 0.99), ("+5pct", 1.05), ("-5pct", 0.95), ("x10", 10), ("div10", 0.1), ("x100", 100)]]
    out += [("invented", money(v * rng.uniform(0.2, 3.0)), False) for _ in range(5)]
    others = [float(x) for r in rows for x in r.values() if isinstance(x, (int, float)) and x != v]
    if len(others) >= 2 and abs(others[0] + others[1] - v) > 0.005 * v:  # a real derived number (two other cells added) stated as if it were the headline
        out.append(("sum_of_two_other_cells", money(others[0] + others[1]), False))
    return out


# Run the grid through check_numbers and count wrong accepts and wrong rejects.
def number_check() -> dict:
    rng, cells = random.Random(7), []
    for sentence, rows, v, is_pct in _tables():
        for variant, written, should_pass in _variants(v, is_pct, rows, rng):
            text = sentence.format(x=written)
            passed = not check_numbers(text, [], unit="INR", rows=rows)
            cells.append({"text": text, "variant": variant, "should_pass": should_pass, "passed": passed})
    good, bad = [c for c in cells if c["should_pass"]], [c for c in cells if not c["should_pass"]]
    derived = [c for c in bad if c["variant"] == "sum_of_two_other_cells"]
    plain_bad = [c for c in bad if c not in derived]
    by_variant = defaultdict(lambda: [0, 0])
    for c in cells:
        by_variant[c["variant"]][0] += c["passed"]
        by_variant[c["variant"]][1] += 1
    return {"cells": len(cells), "false_reject_pct": pct(sum(not c["passed"] for c in good), len(good)),
            "false_accept_pct": pct(sum(c["passed"] for c in plain_bad), len(plain_bad)),
            "derived_sum_accept_pct": pct(sum(c["passed"] for c in derived), len(derived)),
            "accepted_by_variant": {k: f"{a}/{n}" for k, (a, n) in sorted(by_variant.items())},
            "wrong_calls": [c for c in cells if c["passed"] != c["should_pass"]]}


# ── Deflated Sharpe ─────────────────────────────────────────────────────────────────────────────────────────

# The published worked example, and a Monte-Carlo check of the expected best Sharpe out of N zero-skill trials.
def deflated_sharpe() -> dict:
    sr, var, t = 2.5 / math.sqrt(250), 0.5 / 250, 1250
    published = {100: 0.9004, 46: 0.9505}  # López de Prado, Nomura QIS slides 2015 (Bailey & López de Prado 2014)
    got = {n: round(dsr(sr, t, -3, 10, n, var), 4) for n in published}
    euler, z = 0.5772156649, __import__("statistics").NormalDist().inv_cdf
    sr0 = math.sqrt(var) * ((1 - euler) * z(1 - 1 / 100) + euler * z(1 - 1 / (100 * math.e)))
    sims = np.random.default_rng(7).normal(0, math.sqrt(var), size=(20000, 100)).max(axis=1)
    return {"inputs": {"annual_sr": 2.5, "t": t, "skew": -3, "kurtosis": 10, "annual_var_trial_srs": 0.5},
            "published_dsr": published, "computed_dsr": got,
            "max_abs_error": round(max(abs(got[n] - published[n]) for n in published), 6),
            "sr0_formula": round(sr0, 4), "sr0_published": 0.1132, "sr0_monte_carlo_20000x100": round(float(sims.mean()), 4)}


# ── Column mapping ──────────────────────────────────────────────────────────────────────────────────────────

# Suggest a mapping for every header row in mapping.yaml and score shape, columns and confidence labels.
async def mapping_set(with_ai: bool) -> dict:
    cases, rows = _load("mapping.yaml"), []
    if not with_ai:
        real_ai, mapping._ask_ai = mapping._ask_ai, _no_ai
    try:
        with tempfile.TemporaryDirectory() as tmp:
            engine.DATA_ROOT = Path(tmp) / "users"
            for i, c in enumerate(cases):
                rec = dict(zip(c["headers"], c["example"])) | {"source_row": 1}
                table = ExtractedTable(shape="unknown", columns=c["headers"], rows=[rec], status="needs_mapping",
                                       source=SourceRef(file="eval.csv", content_hash="0"),
                                       profile=_profile(c["headers"], [rec]))
                rows.append(_score_mapping(c, await mapping.suggest(f"eval-map-{i}", table)))
    finally:
        if not with_ai:
            mapping._ask_ai = real_ai
    return _mapping_summary(rows)


async def _no_ai(*_a, **_k):
    return None, {}


# One header row: did the suggestion get the shape and every column right, and was each confidence label right?
def _score_mapping(case: dict, s: dict) -> dict:
    want = {k: v if isinstance(v, list) else [v] for k, v in case["map"].items()}
    if case["shape"] == "none":
        return {"name": case["name"], "shape": "none", "ok": bool(s["missing"]), "got_shape": s["shape"],
                "missing": s["missing"], "pairs": [], "via": s["via"]}
    pairs = [{"std": k, "col": v, "right": s["shape"] == case["shape"] and v in want.get(k, []),
              "confidence": s["confidence"].get(k)} for k, v in s["column_map"].items()]
    found = sum(p["right"] for p in pairs)
    ok = s["shape"] == case["shape"] and found == len(pairs) == len(want)
    return {"name": case["name"], "shape": case["shape"], "ok": ok, "got_shape": s["shape"], "missing": s["missing"],
            "pairs": pairs, "expected_pairs": len(want), "via": s["via"]}


# Accuracy over all rows, by shape, column precision / recall, and precision of each confidence label.
def _mapping_summary(rows: list[dict]) -> dict:
    by_shape, labels = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
    for r in rows:
        by_shape[r["shape"]][0] += r["ok"]
        by_shape[r["shape"]][1] += 1
        for p in r["pairs"]:
            labels[p["confidence"]][0] += p["right"]
            labels[p["confidence"]][1] += 1
    real = [r for r in rows if r["shape"] != "none"]
    pairs = [p for r in real for p in r["pairs"]]
    return {"rows": len(rows), "accuracy_pct": pct(sum(r["ok"] for r in rows), len(rows)),
            "shape_accuracy_pct": pct(sum((r["got_shape"] == r["shape"]) if r["shape"] != "none" else r["ok"]
                                          for r in rows), len(rows)),
            "column_precision_pct": pct(sum(p["right"] for p in pairs), len(pairs)),
            "column_recall_pct": pct(sum(p["right"] for p in pairs), sum(r["expected_pairs"] for r in real)),
            "by_shape": {k: f"{a}/{n}" for k, (a, n) in sorted(by_shape.items())},
            "confidence_precision": {k: {"right": a, "labelled": n, "pct": pct(a, n)} for k, (a, n) in sorted(labels.items())},
            "rules_incomplete": sum(r["via"] == "rules+ai" for r in rows),  # rows where the AI step runs (live arm)
            "wrong": [r for r in rows if not r["ok"]]}


# The keyless sets, in the order the result file lists them.
def keyless() -> dict:
    return {"pii": pii(), "sql": sql_attacks(), "numbers": number_check(), "deflated_sharpe": deflated_sharpe(),
            "mapping_rules": asyncio.run(mapping_set(with_ai=False))}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="also run the rules+AI mapping arm (needs a model key)")
    args = ap.parse_args()
    out = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    out |= keyless()
    if args.live:
        stats = {"calls": 0}
        golden.install_cache(False, stats, MAPPING_CACHE)
        out["mapping_rules_ai"] = asyncio.run(mapping_set(with_ai=True)) | {"llm_calls": stats["calls"]}
    out["run_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    for name, res in out.items():
        if isinstance(res, dict):
            print(name, {k: v for k, v in res.items() if not isinstance(v, (list, dict))})
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
