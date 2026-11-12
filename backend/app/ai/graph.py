"""
# graph.py - the assistant's pipeline as a LangGraph state graph

The same four steps `agent.py` describes, as nodes with explicit edges:

```
START -> plan -> run (loops for each `multi` step, at most 3) -> explain -> verify -> END
           +-> reply (clarify / out_of_scope / smalltalk) -> END
plan or run may also finish early with an answer (model unavailable, `remember`, `filing`, a refused plan).
```

- Prompts, tools and the number check are the ones in `agent.py`, `tools.py` and `verify.py`; this file only wires them.
- Each node records its wall time in `timings` (returned on the answer).
- With a `chat_id`, the state is checkpointed per chat (thread id = chat id) in `agent_state.sqlite` in the user's folder.
- With `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` set, each question is sent to Langfuse as one trace.

Used by `agent.ask`.
"""
from __future__ import annotations

import json
import os
import time
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.ai import agent, research
from app.ai.verify import check_numbers, numbers_in
from app.data import engine

MULTI_KINDS = ("metric", "sql", "company", "calc", "tax", "rebalance")


# Everything one question carries through the graph. Plain data only, so it can be checkpointed.
class State(TypedDict):
    user_id: str
    workspace_id: str | None
    question: str
    previous_plan: dict | None
    define: dict | None
    plan: dict | None      # agent.Plan as a dict
    step: int              # the next `multi` step to run
    rows: list
    provenance: dict | None
    warnings: list
    badges: list           # one per `multi` step
    steps: list            # provenance of each `multi` step
    text: str | None       # the explainer's draft, before the number check
    answer: dict | None    # agent.Answer as a dict, once decided
    timings: dict


# Wrap a node so it adds its wall time (seconds, summed over loops) to `timings`.
def _timed(name, fn):
    async def node(state: State) -> dict:
        start = time.perf_counter()
        out = await fn(state)
        timings = dict(state["timings"])
        timings[name] = round(timings.get(name, 0) + time.perf_counter() - start, 4)
        return {**out, "timings": timings}
    return node


# Plan: one model call that decides what to run. `remember` is stored here, since it is part of understanding.
async def plan_node(s: State) -> dict:
    from app.ai import llm_client as llm
    planned, previous_plan, define = s["question"], s["previous_plan"], s["define"]
    if define and define.get("term") and define.get("value"):  # the user just picked what a word means
        agent.save_definition(s["user_id"], define["term"], define["value"])
        planned = f'{planned} (here "{define["term"]}" means: {define["value"]})'  # said outright, so it isn't asked again
    if previous_plan and previous_plan.get("kind") == "clarify":
        previous_plan = None  # the last answer was the question itself, not a plan to build on
    prompt = (f"Context: {agent._context(s['user_id'], s['workspace_id'])}\n"
              f"Previous plan: {json.dumps(previous_plan) if previous_plan else 'none'}\nQuestion: {planned}")
    plan = await llm.call_json(agent.PLANNER, prompt, agent.Plan, user_id=s["user_id"])
    if plan is None:
        return {"answer": agent.Answer(text="The assistant is unavailable right now (the language model did not answer). "
                                            "Your dashboards still work.", badge="none", verified=True).model_dump()}
    if plan.kind == "remember" and plan.memory and plan.memory.get("term") and plan.memory.get("value"):
        term, value = str(plan.memory["term"])[:80], str(plan.memory["value"])[:300]
        agent.save_definition(s["user_id"], term, value)
        text = f'Noted: "{term}" means {value}. You can remove it under Remembered meanings.'
        return {"plan": plan.model_dump(), "answer": agent.Answer(
            text=text if not check_numbers(text, numbers_in(s["question"])) else "Noted.", badge="none", plan=plan).model_dump()}
    return {"plan": plan.model_dump()}


# Reply: clarify / out_of_scope / smalltalk need no data, only the planner's sentence (which may hold no numbers).
async def reply_node(s: State) -> dict:
    plan = agent.Plan.model_validate(s["plan"])
    text = plan.clarify_question if plan.kind == "clarify" else (plan.reply or "I can only answer questions about your data.")
    bad = check_numbers(text or "", [])
    return {"answer": agent.Answer(
        text=text if text and not bad else "I can only answer questions about your data.", badge="none", plan=plan,
        clarify={"term": plan.clarify_term, "options": plan.clarify_options} if plan.kind == "clarify" else None).model_dump()}


# Run: the plan in code (or the next `multi` step). A plan that can't run ends with the reason.
async def run_node(s: State) -> dict:
    plan = agent.Plan.model_validate(s["plan"])
    if plan.kind == "filing":  # the text of the user's documents: research.py answers and checks with page citations
        res = await research.answer(s["user_id"], s["question"], s["workspace_id"] or "default")
        return {"answer": agent.Answer(
            text=res["text"], badge="external", rows=res["citations"], warnings=res["warnings"], plan=plan,
            verified=res["verified"], provenance={"tool": "research", "pages": [c["page"] for c in res["citations"]]}).model_dump()}
    try:
        if plan.kind != "multi" or not plan.steps:
            rows, provenance, warnings, badge = await agent._run(s["user_id"], plan, s["workspace_id"])
            return {"rows": rows, "provenance": provenance, "warnings": warnings, "badges": [badge]}
        n = s["step"] + 1
        sub = agent.Plan.model_validate(plan.steps[s["step"]])
        if sub.kind not in MULTI_KINDS:
            raise ValueError(f"step {n} ({sub.kind}) can't be part of a multi-step answer")
        r, p, w, b = await agent._run(s["user_id"], sub, s["workspace_id"])
    except (ValueError, LookupError, TimeoutError) as e:  # SqlRejected is a ValueError
        return {"answer": agent.Answer(text=f"I couldn't answer that from your data: {e}", badge="none", plan=plan,
                                       warnings=["the question was not answered"], verified=True).model_dump()}
    steps = s["steps"] + [p]
    return {"step": n, "rows": s["rows"] + [{"step": n, **row} for row in r or []], "steps": steps,
            "provenance": {"tool": "multi", "steps": steps}, "warnings": s["warnings"] + w, "badges": s["badges"] + [b]}


# Explain: one model call over at most 40 rows.
async def explain_node(s: State) -> dict:
    from app.ai import llm_client as llm
    rows, warnings = s["rows"], s["warnings"]
    shown = rows[:agent.MAX_ROWS_TO_EXPLAIN]
    period = (s["provenance"] or {}).get("period")
    raw = await llm.call_llm(agent.EXPLAINER, f"Question: {s['question']}\nPeriod: {period}\nResults: {json.dumps(shown, default=str)}"
                                              f"\nWarnings: {warnings}", max_tokens=300, temperature=0, user_id=s["user_id"])
    return {"text": raw}


# Verify: every number in the explanation must come from the rows (or the question); otherwise show the table instead.
async def verify_node(s: State) -> dict:
    plan, rows, raw, warnings = agent.Plan.model_validate(s["plan"]), s["rows"], s["text"], s["warnings"]
    period = (s["provenance"] or {}).get("period")
    values = numbers_in(s["question"])  # numbers the user typed may be repeated
    if plan.kind == "company":  # SEC values are in USD millions, so "215,938 million" must also match 215938
        values += [v * 1e6 for r in rows for v in r.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    unit = {"company": "USD", "calc": "mixed"}.get(plan.kind, "INR")  # the user's own data is in rupees
    bad = check_numbers(raw or "", values, unit, rows=rows)
    verified = bool(raw) and not bad
    text = raw if verified else agent._plain_summary(rows, period)
    if bad:
        warnings = warnings + [f"the written explanation had numbers not in the results ({', '.join(bad[:3])}), "
                               "so it was replaced by the table"]
    badge = next(b for b in ("exploratory", "external", "trusted") if b in set(s["badges"]) | {"trusted"}) \
        if plan.kind == "multi" else s["badges"][0]
    return {"answer": agent.Answer(text=f"{text}\n\n_{agent.DISCLAIMER}_", badge=badge, rows=rows, provenance=s["provenance"],
                                   warnings=warnings, plan=plan, verified=verified).model_dump()}


# After plan: stop if answered, reply if no data is needed, else run.
def _after_plan(s: State) -> str:
    if s["answer"]:
        return END
    return "reply" if s["plan"]["kind"] in ("clarify", "out_of_scope", "smalltalk") else "run"


# After run: stop if answered, run the next `multi` step if there is one (3 at most), else explain.
def _after_run(s: State) -> str:
    if s["answer"]:
        return END
    plan = s["plan"]
    return "run" if plan["kind"] == "multi" and 0 < s["step"] < min(len(plan["steps"]), 3) else "explain"


# The graph, before a checkpointer is attached.
def _build() -> StateGraph:
    g = StateGraph(State)
    for name, fn in [("plan", plan_node), ("reply", reply_node), ("run", run_node), ("explain", explain_node),
                     ("verify", verify_node)]:
        g.add_node(name, _timed(name, fn))
    g.add_edge(START, "plan")
    g.add_conditional_edges("plan", _after_plan, ["reply", "run", END])
    g.add_conditional_edges("run", _after_run, ["run", "explain", END])
    g.add_edge("explain", "verify")
    g.add_edge("reply", END)
    g.add_edge("verify", END)
    return g


BUILDER = _build()
GRAPH = BUILDER.compile()


# The Langfuse callback when its keys are set (none otherwise, so nothing is sent anywhere).
def _callbacks() -> list:
    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        return []
    from langfuse.langchain import CallbackHandler
    return [CallbackHandler()]


# Run one question through the graph; returns the final state. With a chat id, the state is checkpointed per chat.
async def invoke(user_id: str, question: str, previous_plan: dict | None, define: dict | None,
                 workspace_id: str | None, chat_id: str | None = None) -> State:
    state = State(user_id=user_id, workspace_id=workspace_id, question=question, previous_plan=previous_plan,
                  define=define, plan=None, step=0, rows=[], provenance=None, warnings=[], badges=[], steps=[],
                  text=None, answer=None, timings={})  # every key, so a checkpointed thread never leaks the last turn
    config = {"callbacks": _callbacks(), "run_name": "wealthos-ask"}
    if not chat_id:
        return await GRAPH.ainvoke(state, config)
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    path = engine.user_dir(user_id) / "agent_state.sqlite"  # one file per user, so threads can't cross users
    path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        config["configurable"] = {"thread_id": engine.safe_id(chat_id)}
        return await BUILDER.compile(checkpointer=saver).ainvoke(state, config)
