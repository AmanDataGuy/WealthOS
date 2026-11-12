# tests/test_agent.py
"""The assistant pipeline with a fake language model (no network): plans run in code, explanations are
number-checked, advice is refused, clarifications are remembered."""
import asyncio

import pytest

from app.ai import agent
from app.data import engine
from app.data.ingest import parse_file

BANK = "Date,Narration,Debit,Credit\n05/04/2026,Rent,25000,\n06/04/2026,Salary,,90000\n10/05/2026,Groceries,4000,\n"


# Fresh data folder with one bank statement for user "u".
@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DATA_ROOT", tmp_path / "users")
    p = tmp_path / "bank.csv"
    p.write_text(BANK, encoding="utf-8")
    engine.save_tables("u", parse_file(p))


# Replace the language model: the planner returns `plan`, the explainer returns `text`.
def _fake_llm(monkeypatch, plan: dict, text: str = ""):
    from app.ai import llm_client as llm

    async def fake_json(system, user, model_cls, **kw):
        return model_cls.model_validate(plan)

    async def fake_text(system, user, **kw):
        return text

    monkeypatch.setattr(llm, "call_json", fake_json)
    monkeypatch.setattr(llm, "call_llm", fake_text)


# Run one question through the assistant.
def _ask(question, **kw):
    return asyncio.run(agent.ask("u", question, **kw))


# A metric plan is run by the engine; an honest explanation passes the number check.
def test_metric_answer_verified(monkeypatch):
    _fake_llm(monkeypatch, {"kind": "metric", "metric_query": {"metrics": ["total_spend"], "time": "2026-04-01..2026-06-30"}},
              "You spent ₹29,000 between April and June 2026.")
    a = _ask("How much did I spend?")
    assert a.badge == "trusted" and a.verified and a.rows == [{"total_spend": 29000.0}]
    assert a.text.startswith("You spent ₹29,000")


# An explanation with an invented number is thrown away and replaced by a plain sentence + the table.
def test_invented_number_replaced(monkeypatch):
    _fake_llm(monkeypatch, {"kind": "metric", "metric_query": {"metrics": ["total_spend"], "time": "2026-04-01..2026-06-30"}},
              "You spent ₹35,000.")
    a = _ask("How much did I spend?")
    assert not a.verified and "35,000" not in a.text and a.rows == [{"total_spend": 29000.0}]


# Advice questions get the planner's polite refusal, with no data and no numbers.
def test_advice_refused(monkeypatch):
    _fake_llm(monkeypatch, {"kind": "out_of_scope", "reply": "I can't give buy or sell advice."})
    a = _ask("Should I buy INFY?")
    assert a.badge == "none" and a.rows == [] and "advice" in a.text


# A clarify answer is remembered as a definition for this user.
def test_clarify_then_define(monkeypatch):
    _fake_llm(monkeypatch, {"kind": "clarify", "clarify_question": "Is rent the 'Rent' description?", "clarify_term": "rent",
                            "clarify_options": ["Rent", "Housing"]})
    a = _ask("How much rent did I pay?")
    assert a.clarify == {"term": "rent", "options": ["Rent", "Housing"]}
    _fake_llm(monkeypatch, {"kind": "smalltalk", "reply": "Noted."})
    _ask("How much rent did I pay?", define={"term": "rent", "value": "description = 'Rent'"})
    assert agent.definitions("u")[0]["term"] == "rent"


# A bad SQL plan is refused with a reason, not run.
def test_bad_sql_refused(monkeypatch):
    _fake_llm(monkeypatch, {"kind": "sql", "sql": "SELECT * FROM read_csv('C:/x.csv')"})
    a = _ask("show me everything")
    assert a.badge == "none" and "couldn't answer" in a.text


# A chat keeps its turns in order and stays tied to its workspace; deleting it removes it.
def test_chat_history():
    first = agent.save_turn("u", None, "default", "How much did I spend?", {"text": "₹29,000"})
    assert agent.save_turn("u", first, "default", "and last month?", {"text": "₹4,000"}) == first
    other = agent.save_turn("u", None, "biz", "Revenue by month", {"text": "..."})
    chat = agent.get_chat("u", first)
    assert chat["title"] == "How much did I spend?" and [t["question"] for t in chat["turns"]] == ["How much did I spend?", "and last month?"]
    assert [c["id"] for c in agent.list_chats("u")] == [other, first]  # newest first
    assert [c["turns"] for c in agent.list_chats("u", "default")] == [2]
    agent.delete_chat("u", first)
    with pytest.raises(LookupError):
        agent.get_chat("u", first)
    with pytest.raises(LookupError):
        agent.get_chat("u", "../../secrets")  # ids can't walk out of the chats folder


# A "filing" plan is answered from the workspace's indexed documents, with page citations as rows.
def test_filing_question_goes_to_research(monkeypatch):
    from app.ai import research
    _fake_llm(monkeypatch, {"kind": "filing"}, "unused")

    async def fake_answer(user_id, question, workspace_id):
        return {"text": "Tariffs are a risk [p. 3].", "citations": [{"file": "acme.pdf", "page": 3, "section": "Item 1A",
                "excerpt": "Tariffs on imported parts"}], "verified": True, "warnings": []}
    monkeypatch.setattr(research, "answer", fake_answer)
    a = _ask("What risks does Acme report?")
    assert a.badge == "external" and a.rows[0]["page"] == 3 and a.text.startswith("Tariffs")


# After the user picks what a word means, the planner is told that meaning outright and is not handed the clarify
# question back as a "previous plan" (otherwise it may ask the same question again).
def test_chosen_meaning_reaches_the_planner(monkeypatch):
    from app.ai import llm_client as llm
    seen = []

    async def fake_json(system, user, model_cls, **kw):
        seen.append(user)
        return model_cls.model_validate({"kind": "smalltalk", "reply": "Noted."})
    monkeypatch.setattr(llm, "call_json", fake_json)
    _ask("Show my profit", define={"term": "profit", "value": "realized gains"},
         previous_plan={"kind": "clarify", "clarify_term": "profit"})
    assert '(here "profit" means: realized gains)' in seen[0] and "Previous plan: none" in seen[0]


# A multi plan runs each step in turn (the graph loops over them), tags rows by step and keeps each step's source.
def test_multi_runs_each_step(monkeypatch):
    spend = {"kind": "metric", "metric_query": {"metrics": ["total_spend"], "time": "2026-04-01..2026-06-30"}}
    _fake_llm(monkeypatch, {"kind": "multi", "steps": [spend, {"kind": "calc", "expression": "2 + 3"}]}, "")
    a = _ask("spending and 2 + 3")
    assert [r["step"] for r in a.rows] == [1, 2] and a.rows[1]["result"] == 5
    assert a.provenance["tool"] == "multi" and len(a.provenance["steps"]) == 2 and a.badge == "trusted"
    assert set(a.timings) == {"plan", "run", "explain", "verify"}


# Without Langfuse keys no tracing callback is attached, so nothing leaves the machine.
def test_no_tracing_without_keys(monkeypatch):
    from app.ai import graph
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert graph._callbacks() == []


# With a chat id, each question's graph state is checkpointed under that chat, in the user's own folder.
def test_state_checkpointed_per_chat(monkeypatch):
    from langgraph.checkpoint.sqlite import SqliteSaver
    _fake_llm(monkeypatch, {"kind": "metric", "metric_query": {"metrics": ["total_spend"], "time": "2026-04-01..2026-06-30"}},
              "You spent ₹29,000.")
    _ask("How much did I spend?", chat_id="c1")
    _ask("and in May?", chat_id="c1")
    with SqliteSaver.from_conn_string(str(engine.user_dir("u") / "agent_state.sqlite")) as saver:
        state = saver.get_tuple({"configurable": {"thread_id": "c1"}}).checkpoint["channel_values"]
    assert state["question"] == "and in May?" and state["answer"]["rows"] == [{"total_spend": 29000.0}]
