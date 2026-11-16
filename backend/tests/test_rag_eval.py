# tests/test_rag_eval.py
"""The filing Q&A eval (evals/sets/run_rag.py) re-run in CI on the two real 10-Ks in storage/eval_filings (fetched by
evals/sets/fetch_filings.py; skipped when they are absent). Fails when any retrieval arm's recall@5 drops below the
committed evals/results_rag.json, or when a recorded answer (evals/llm_cache_rag.json, replayed with no network)
changes whether it was refused or passed the number check."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

from app.ai import llm_client as llm
from app.ai import research
from app.data import engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals" / "sets"))
import run_rag  # noqa: E402

pytestmark = pytest.mark.skipif(not all((run_rag.FILINGS / f).exists() for f in ("aapl_10k_2025.pdf", "nvda_10k_2026.pdf")),
                                reason="no eval filings: run `python evals/sets/fetch_filings.py` (needs SEC_USER_AGENT)")


# Both filings indexed once into a temp folder, with their chunk vectors.
@pytest.fixture(scope="module")
def indexed(tmp_path_factory):
    old = engine.DATA_ROOT
    engine.DATA_ROOT = tmp_path_factory.mktemp("rag") / "users"
    yield run_rag.load_set(), run_rag.index()[0]
    engine.DATA_ROOT = old


def test_retrieval_recall_does_not_drop(indexed):
    # shortcut: re-ranked arms are left out (8-15 s a question on a CPU, over half an hour for the set); their
    # recall comes from the local full run. Gate them too once CI has a GPU or the eval set is cached per arm.
    fast = [a for a in [*run_rag.ARMS, run_rag.BEFORE] if "+rerank" not in a]
    was = {a: v for a, v in json.loads(run_rag.OUT.read_text(encoding="utf-8"))["arms"].items() if a in fast}
    now, _ = asyncio.run(run_rag.retrieval(*indexed, only=fast))
    worse = [f"{arm}: {now[arm]['recall_at_5']} (committed {a['recall_at_5']})" for arm, a in was.items()
             if now[arm]["recall_at_5"] < a["recall_at_5"]]
    assert not worse, worse


def test_recorded_answers_replay_unchanged(indexed, monkeypatch):
    if not run_rag.OUT_ANSWERS.exists() or not run_rag.CACHE.exists():
        pytest.skip("no recorded answers yet: run `python evals/sets/run_rag.py --live` once")
    for module, attr in [(llm, "call_json"), (llm, "call_llm"), (research, "REFUSE_BM25"), (research, "REFUSE_RERANK")]:
        monkeypatch.setattr(module, attr, getattr(module, attr))  # the replay patches these; put them back afterwards
    monkeypatch.setenv("WEALTHOS_DB_URL", "")
    committed = json.loads(run_rag.OUT_ANSWERS.read_text(encoding="utf-8"))
    was = {(r["arm"], r["id"]): r for r in committed["rows"]}
    got, _ = asyncio.run(run_rag.answers(indexed[0], indexed[1], committed["arms"], committed["thresholds"],
                                         offline=True, only=set(was)))
    drift = [f"{r['arm']} {r['id']}: refused {r['refused']} verified {r['verified']}" for r in got
             if (r["refused"], r["verified"]) != (was[(r["arm"], r["id"])]["refused"], was[(r["arm"], r["id"])]["verified"])]
    assert len(got) == len(was) and not drift, drift
