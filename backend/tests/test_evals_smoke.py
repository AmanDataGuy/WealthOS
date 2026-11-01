# tests/test_evals_smoke.py
"""The first 10 golden questions (evals/cases.yaml), replayed from the recorded LLM replies
(evals/llm_cache.json) so CI needs no network. Fails if the assistant's plans or numbers get worse."""
import sys
from pathlib import Path

import pytest

from app.data import engine
from app.ai import llm_client as llm
from app.data import timeexpr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import run as evals  # noqa: E402


def test_smoke_set_replayed(monkeypatch):
    if not evals.CACHE.exists():
        pytest.skip("no recorded LLM replies yet: run `python evals/run.py` once")
    for module, name in [(engine, "DATA_ROOT"), (timeexpr, "today_ist"), (llm, "call_json"), (llm, "call_llm")]:
        monkeypatch.setattr(module, name, getattr(module, name))  # evaluate() patches these; put them back afterwards
    monkeypatch.setenv("WEALTHOS_DB_URL", "")
    results, summary = evals.evaluate(offline=True, only=10)
    wrong = [r["q"] for r in results if not (r["plan_ok"] and r["values_ok"])]
    assert summary["value_accuracy_pct"] >= 90 and summary["plan_match_pct"] >= 90, wrong
