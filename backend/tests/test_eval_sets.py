# tests/test_eval_sets.py
"""The keyless labelled sets (evals/sets/run_sets.py: PII, guard_sql attacks, number check, deflated Sharpe, rules-only
column mapping) re-run in CI. Fails when any rate is worse than the committed evals/results_sets.json."""
import json
import sys
from pathlib import Path

from app.data import engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals" / "sets"))
import run_sets  # noqa: E402


def test_labelled_sets_do_not_regress(monkeypatch):
    monkeypatch.setattr(engine, "DATA_ROOT", engine.DATA_ROOT)  # the sets point it at temp folders; put it back
    was = json.loads(run_sets.OUT.read_text(encoding="utf-8"))
    now = run_sets.keyless()
    worse = []
    for set_name, key, higher_is_better in [
            ("pii", "recall_pct", True), ("pii", "precision_pct", True), ("pii", "keep_survival_pct", True),
            ("sql", "block_rate_pct", True), ("sql", "escaped", False), ("sql", "false_block_rate_pct", False),
            ("numbers", "false_accept_pct", False), ("numbers", "false_reject_pct", False),
            ("deflated_sharpe", "max_abs_error", False),
            ("mapping_rules", "accuracy_pct", True), ("mapping_rules", "column_precision_pct", True)]:
        a, b = now[set_name][key], was[set_name][key]
        if (a < b) if higher_is_better else (a > b):
            worse.append(f"{set_name}.{key}: {a} (committed {b})")
    assert not worse, worse
    assert now["sql"]["escaped"] == 0 and now["deflated_sharpe"]["max_abs_error"] < 5e-5
