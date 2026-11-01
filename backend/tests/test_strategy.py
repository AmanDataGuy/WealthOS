# tests/test_strategy.py
"""The signal ops against hand-computed series, the look-ahead guard, Indian costs against a hand-written
contract note, the split check, run cards with the deflated Sharpe, and plain English -> spec with a fake LLM."""
import asyncio
import json

import httpx
import pandas as pd
import pytest
from pydantic import ValidationError

from app.data import engine
from app.features import strategy
from app.data.ingest import parse_file
from app.features.strategy import Signal, StrategySpec, evaluate


@pytest.fixture(autouse=True)
def data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DATA_ROOT", tmp_path / "users")


def S(op, *args):
    return Signal(op=op, args=list(args))


CLOSE = S("close")
PANEL = pd.DataFrame({"A": [1.0, 2, 3, 4, 5, 6], "B": [6.0, 5, 4, 3, 2, 1]})


# Every op on a tiny panel, checked against values worked out by hand.
def test_signal_ops():
    assert evaluate(S("sma", CLOSE, 3), PANEL)["A"].tolist()[2:] == [2, 3, 4, 5]
    assert evaluate(S("ema", CLOSE, 3), PANEL)["A"][2] == 2.25            # alpha 0.5: 1 -> 1.5 -> 2.25
    assert evaluate(S("delta", CLOSE, 2), PANEL)["A"][2] == 2
    assert evaluate(S("lag", CLOSE, 1), PANEL)["A"][1] == 1
    assert evaluate(S("ts_std", CLOSE, 2), PANEL)["A"][1] == pytest.approx(0.70710678)
    rsi = evaluate(S("rsi", CLOSE, 2), pd.DataFrame({"A": [10.0, 11, 10, 11]}))["A"]
    assert rsi[2] == pytest.approx(50) and rsi[3] == pytest.approx(75)    # Wilder: gains .5/.75, losses .5/.25
    assert evaluate(S("rsi", CLOSE, 2), PANEL)["A"][3] == 100             # only gains
    assert evaluate(S("rank", CLOSE), PANEL).iloc[0].tolist() == [0.5, 1.0]
    assert evaluate(S("gt", CLOSE, 3.5), PANEL)["A"].tolist() == [False] * 3 + [True] * 3
    assert evaluate(S("crossover", CLOSE, 3.5), PANEL)["A"].tolist() == [False] * 3 + [True, False, False]
    both = evaluate(S("and", S("gt", CLOSE, 2), S("lt", CLOSE, 5)), PANEL)["A"].tolist()
    assert both == [False, False, True, True, False, False]
    assert evaluate(S("or", S("lt", CLOSE, 2), S("gt", CLOSE, 5)), PANEL)["A"].tolist() == [True, False, False, False, False, True]
    assert evaluate(S("const", 7), PANEL) == 7.0
    assert evaluate(S("lt", CLOSE, S("mul", S("lag", CLOSE, 1), 1.4)), PANEL)["A"].tolist()[1:] == [False, False, True, True, True]
    assert strategy.describe_signal(S("mul", S("sma", CLOSE, 20), 0.95)) == "0.95 × the 20-day average of the price"


# Bad rules are refused before anything runs.
def test_signal_limits():
    deep = CLOSE
    for _ in range(9):
        deep = S("lag", deep, 1)
    for bad in (deep, S("lag", CLOSE, 0), S("lag", CLOSE, -1), S("sma", CLOSE, 2.5), S("sma", CLOSE, 500),
                S("sma", CLOSE), S("gt", CLOSE), S("sma", S("const", 3), 2)):
        with pytest.raises(ValueError):
            evaluate(bad, PANEL)
    with pytest.raises(ValidationError):
        Signal(op="close", lookahead=True)
    with pytest.raises(ValidationError):
        StrategySpec(universe=["A"], entry=CLOSE, leverage=10)  # unknown keys are an error


# Save a price table for user "u" the way an upload does.
def _prices(tmp_path, rows: list[tuple], name="prices.csv", header="date,symbol,open,close"):
    p = tmp_path / name
    p.write_text(header + "\n" + "\n".join(",".join(map(str, r)) for r in rows) + "\n", encoding="utf-8")
    tables = parse_file(p)
    assert tables[0].shape == "prices"
    engine.save_tables("u", tables)


# A rule that buys after an up day must not earn the up day itself: on a zig-zag it buys before each down day.
def test_no_look_ahead(tmp_path):
    days = pd.bdate_range("2026-01-01", periods=40)
    closes = [100 if i % 2 == 0 else 110 for i in range(40)]
    _prices(tmp_path, [(d.date(), "ZIG", closes[i - 1] if i else 100, c) for i, (d, c) in enumerate(zip(days, closes))])
    r = strategy.run("u", None, {"universe": ["zig"], "entry": S("gt", S("delta", CLOSE, 1), 0).model_dump(), "costs": "none"})
    assert r["metrics"]["total_return_pct"] < -50  # it is always holding on the way down
    assert r["metrics"]["hit_rate_pct"] == 0
    assert r["label"].startswith("Historical simulation, not advice")


# Buying ₹1,00,000 of a flat stock costs exactly the charges on the contract note.
def test_costs_match_contract_note(tmp_path):
    days = pd.bdate_range("2026-01-01", periods=10)
    _prices(tmp_path, [(d.date(), "FLAT", 100, 100) for d in days])
    r = strategy.run("u", None, {"universe": ["FLAT"], "entry": S("gt", CLOSE, 50).model_dump(), "capital": 100000})
    # STT 100 + stamp duty 15 + exchange fee 2.97 + SEBI fee 0.10 + GST 18% on (2.97 + 0.10) = 118.62
    assert r["metrics"]["final_value"] == pytest.approx(100000 - 118.6226, abs=0.01)
    assert r["buy_and_hold"]["final_value"] == pytest.approx(100000 - 118.6226, abs=0.01)


# Unadjusted prices with a split-sized drop stop the run; a run card counts the tries and deflates the Sharpe.
def test_split_check_and_run_cards(tmp_path):
    days = pd.bdate_range("2026-01-01", periods=60)
    _prices(tmp_path, [(d.date(), "SPLT", 0, 200 if i < 30 else 100) for i, d in enumerate(days)], "split.csv", "date,symbol,volume,close")
    with pytest.raises(ValueError, match="split"):
        strategy.run("u", None, {"universe": ["SPLT"], "entry": S("gt", CLOSE, 0).model_dump()})

    walk = [100.0]
    for i in range(1, 60):
        walk.append(walk[-1] * (1.01 if i % 3 else 0.985))
    _prices(tmp_path, [(d.date(), "WALK", round(w, 2), round(w, 2)) for d, w in zip(days, walk)], "walk.csv")
    spec = {"universe": ["WALK"], "entry": S("gt", CLOSE, S("sma", CLOSE, 5)).model_dump()}
    first = strategy.run("u", None, spec)
    second = strategy.run("u", None, spec | {"exit": S("lt", CLOSE, S("sma", CLOSE, 10)).model_dump()}, generated_by="test-model")
    assert first["run_card"]["n_trials"] == 1 and second["run_card"]["n_trials"] == 2
    assert 0 <= second["run_card"]["deflated_sharpe"] <= 1
    assert any("tested 2 rules" in w for w in second["warnings"])
    assert not any("learned from data" in w for w in second["warnings"])  # 2026 is after the model cutoff
    assert [c["id"] for c in strategy.list_runs("u")] == [second["run_card"]["id"], first["run_card"]["id"]]
    assert strategy.get_run("u", first["run_card"]["id"])["description"].startswith("Buy WALK when the price is above")
    assert len(first["equity_curve"]) > 50 and first["metrics"]["time_invested_pct"] > 0


# Plain English -> spec: the AI only translates; symbols and windows are checked, and code writes the description.
def test_parse_with_fake_llm(tmp_path, monkeypatch):
    from app.ai import llm_client as llm
    days = pd.bdate_range("2026-01-01", periods=5)
    _prices(tmp_path, [(d.date(), "NIFTYBEES", 100, 100) for d in days])
    answer = {"universe": ["niftybees"], "entry": S("crossover", S("sma", CLOSE, 50), S("sma", CLOSE, 200)).model_dump()}

    async def fake_json(system, user, model_cls, **kw):
        return model_cls(**answer)
    monkeypatch.setattr(llm, "call_json", fake_json)
    out = asyncio.run(strategy.parse("u", None, "buy niftybees on a golden cross"))
    assert out["description"].startswith("Buy NIFTYBEES when the 50-day average of the price crosses above the 200-day average")
    answer["universe"] = ["NIFTYBEES", "TCS"]  # the model adds a stock nobody named: the text decides
    _prices(tmp_path, [(d.date(), "TCS", 100, 100) for d in days], "tcs.csv")
    assert asyncio.run(strategy.parse("u", None, "golden cross on niftybees"))["spec"]["universe"] == ["NIFTYBEES"]
    answer = {"universe": ["TSLA"], "entry": CLOSE.model_dump()}
    with pytest.raises(ValueError, match="no prices"):
        asyncio.run(strategy.parse("u", None, "buy tesla"))


# When Groq fails and the fallback answers, the spec names the fallback model, not Groq's.
def test_generated_by_names_the_model_that_answered(tmp_path, monkeypatch):
    from app.ai import llm_client as llm

    class Resp:
        def __init__(self, ok):
            self.ok = ok

        def raise_for_status(self):
            if not self.ok:
                raise httpx.HTTPStatusError("429", request=None, response=None)

        def json(self):
            return {"choices": [{"message": {"content": '{"universe": ["NIFTYBEES"], "entry": ' + json.dumps(CLOSE.model_dump()) + "}"}}]}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            return Resp(ok="openrouter" in url)
    monkeypatch.setattr(llm.httpx, "AsyncClient", Client)
    monkeypatch.setattr(llm, "_PROVIDERS", [("groq", "https://groq/x", "k"), ("openrouter", "https://openrouter/x", "k")])
    monkeypatch.setattr(llm, "_db_url", lambda: "")
    days = pd.bdate_range("2026-01-01", periods=5)
    _prices(tmp_path, [(d.date(), "NIFTYBEES", 100, 100) for d in days])
    out = asyncio.run(strategy.parse("u", None, "buy niftybees when the close is up"))
    assert out["generated_by"] == llm.OPENROUTER_MODEL != llm.GROQ_MODEL
