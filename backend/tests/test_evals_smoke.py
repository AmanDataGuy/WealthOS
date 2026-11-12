# tests/test_evals_smoke.py
"""All 34 golden questions (evals/cases.yaml), replayed from the recorded LLM replies (evals/llm_cache*.json) so CI
needs no network, for every recorded run: the agent (results.json; results_openrouter.json, same model on another
provider) and the text-to-SQL baseline (results_baseline.json). Fails naming every question whose plan kind, values
or badge differ from the committed live result file."""
import json
import sys
from pathlib import Path

import pytest

from app.data import engine
from app.ai import llm_client as llm
from app.data import timeexpr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import run as evals  # noqa: E402


@pytest.mark.parametrize("arm,name", [("agent", ""), ("agent", "openrouter"), ("baseline", "baseline")])
def test_golden_set_replayed(monkeypatch, arm, name):
    cache = evals.CACHE.with_name(f"llm_cache_{name}.json") if name else evals.CACHE
    committed = evals.CACHE.with_name(f"results_{name}.json" if name else "results.json")
    if not cache.exists() or not committed.exists():
        pytest.skip(f"no recorded run yet: run `python evals/run.py --arm {arm} --name {name}` once")
    for module, attr in [(engine, "DATA_ROOT"), (timeexpr, "today_ist"), (llm, "call_json"), (llm, "call_llm")]:
        monkeypatch.setattr(module, attr, getattr(module, attr))  # evaluate() patches these; put them back afterwards
    monkeypatch.setenv("WEALTHOS_DB_URL", "")
    results, _ = evals.evaluate(offline=True, name=name, arm=arm)
    was = {r["q"]: r for r in json.loads(committed.read_text(encoding="utf-8"))["results"]}
    keys = ("kind", "plan_ok", "values_ok", "badge")
    drift = [f"{r['q']}: {[r[k] for k in keys]} (committed {[was[r['q']][k] for k in keys]})"
             for r in results if [r[k] for k in keys] != [was[r["q"]][k] for k in keys]]
    assert not drift, drift
