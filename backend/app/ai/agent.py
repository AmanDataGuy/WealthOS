"""
# agent.py - the assistant: questions in plain English, answers with numbers you can check

One fixed pipeline (no "multi-agent debates"); the AI never produces a number:

1. **Plan** (one AI call, checked against a strict format): what kind of question is this, and exactly what to run
   - `metric`: a named calculation from `metrics.yaml` for a period ("spending this FY") -> **Trusted** answer
   - `sql`: a read-only query when no metric fits -> checked by `verify.guard_sql` -> **Exploratory** answer
   - `company` / `news`: SEC figures or recent news for a company (`tools.py`)
   - `filing`: what an uploaded filing / annual report *says* (risks, strategy, lawsuits ...): `research.py` finds the
     pages and answers with page citations, each number checked against those pages
   - `calc`: arithmetic on numbers the user gave ("12% of 4,50,000")
   - `clarify`: the question is ambiguous ("revenue" could be two things) -> ask once, remember the answer
   - `out_of_scope`: not something we can answer from data (we never give buy/sell advice)
2. **Run** it in code (`engine.py` / `tools.py`). If the data is missing, say so instead of guessing.
3. **Explain** (one AI call) using only the returned rows.
4. **Verify** every number in the explanation (`verify.check_numbers`). If one doesn't match the results, the
   explanation is replaced by a plain sentence and the table is shown instead. Numbers are never "fixed up".

The previous plan is sent back with a follow-up ("and last month?") so the planner can reuse it.
Definitions the user confirmed ("revenue means net of returns") are stored per user and always win.
The workspace's semantic model (`semantic.py`: column meanings, relationships, its own metrics) is part of the context.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel

from app.data import engine
from app.ai import research
from app.data import semantic
from app.data import timeexpr
from app.ai import tools
from app.data.engine import MetricQuery
from app.ai.verify import check_numbers, numbers_in

MAX_ROWS_TO_EXPLAIN = 40
DISCLAIMER = "Analytics on your own data, not investment advice (WealthOS is not SEBI-registered)."


# What the planner decided to do. Exactly one of the detail fields is used, depending on `kind`.
class Plan(BaseModel):
    kind: Literal["metric", "sql", "company", "filing", "news", "calc", "clarify", "out_of_scope", "smalltalk"]
    metric_query: MetricQuery | None = None
    sql: str | None = None
    ticker: str | None = None
    news_query: str | None = None
    expression: str | None = None
    clarify_question: str | None = None
    clarify_term: str | None = None
    clarify_options: list[str] = []
    reply: str | None = None  # only for smalltalk / out_of_scope: a short sentence with no numbers


# What the user gets back: the words, the numbers behind them, where they came from, and the plan (for follow-ups).
class Answer(BaseModel):
    text: str
    badge: Literal["trusted", "exploratory", "external", "none"]
    rows: list[dict] = []
    provenance: dict | None = None
    warnings: list[str] = []
    plan: Plan | None = None
    verified: bool = True
    clarify: dict | None = None


# ── Definitions memory ──────────────────────────────────────────────────────────────────

# The user's confirmed definitions, newest last: [{term, value, created_at}].
def definitions(user_id: str) -> list[dict]:
    path = engine.user_dir(user_id) / "definitions.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


# Remember what a term means for this user (a newer definition of the same term replaces the old one).
def save_definition(user_id: str, term: str, value: str) -> list[dict]:
    defs = [d for d in definitions(user_id) if d["term"].lower() != term.lower()]
    defs.append({"term": term, "value": value, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    path = engine.user_dir(user_id) / "definitions.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(defs, indent=1, ensure_ascii=False), encoding="utf-8")
    return defs


# Forget one definition.
def delete_definition(user_id: str, term: str) -> list[dict]:
    defs = [d for d in definitions(user_id) if d["term"].lower() != term.lower()]
    path = engine.user_dir(user_id) / "definitions.json"
    if path.exists():
        path.write_text(json.dumps(defs, indent=1, ensure_ascii=False), encoding="utf-8")
    return defs


# ── Chat history: one JSON file per chat, tied to one workspace (data/users/<id>/chats/<chat_id>.json) ──────────

# Where one chat's JSON file lives.
def _chat_path(user_id: str, chat_id: str):
    return engine.user_dir(user_id) / "chats" / f"{engine.safe_id(chat_id)}.json"


# The user's chats, newest first, without their turns: [{id, title, workspace_id, updated_at, turns: count}].
def list_chats(user_id: str, workspace_id: str | None = None) -> list[dict]:
    folder = engine.user_dir(user_id) / "chats"
    chats = [json.loads(p.read_text(encoding="utf-8")) for p in folder.glob("*.json")] if folder.exists() else []
    return sorted(({**{k: c[k] for k in ("id", "title", "workspace_id", "updated_at")}, "turns": len(c["turns"])}
                   for c in chats if workspace_id in (None, c["workspace_id"])), key=lambda c: c["updated_at"], reverse=True)


# One chat with all its turns ([{question, answer}]). LookupError if it doesn't exist.
def get_chat(user_id: str, chat_id: str) -> dict:
    path = _chat_path(user_id, chat_id)
    if not path.exists():
        raise engine.NotFound(f"no chat '{chat_id}'")
    return json.loads(path.read_text(encoding="utf-8"))


# Append a question and its answer; a new chat (chat_id None) is named after its first question. Returns the chat id.
def save_turn(user_id: str, chat_id: str | None, workspace_id: str, question: str, answer: dict) -> str:
    now = datetime.now(timezone.utc)
    chat = get_chat(user_id, chat_id) if chat_id else {
        "id": now.strftime("%Y%m%d%H%M%S%f"), "title": question[:80], "workspace_id": workspace_id,
        "created_at": now.isoformat(), "turns": []}
    chat["turns"].append({"question": question, "answer": answer})
    chat["updated_at"] = now.isoformat()
    path = _chat_path(user_id, chat["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(chat, ensure_ascii=False, default=str), encoding="utf-8")
    return chat["id"]


# Delete one chat (no error if it is already gone).
def delete_chat(user_id: str, chat_id: str) -> None:
    _chat_path(user_id, chat_id).unlink(missing_ok=True)


# ── The pipeline ────────────────────────────────────────────────────────────────────────

# Everything the planner may use: the user's tables and columns, the metric list, definitions, dates.
def _context(user_id: str, workspace_id: str | None) -> str:
    con = engine.open_readonly(user_id, workspace_id)
    metrics = {k: f"{v['name']} (table {v['shape']}, {v['unit']})" for k, v in engine.load_metrics(user_id, workspace_id).items()}
    model = semantic.load(user_id, workspace_id) if workspace_id else {}
    return json.dumps({
        "today": timeexpr.today_ist().isoformat(), "time_expressions": timeexpr.EXPRESSIONS + ["YYYY-MM-DD..YYYY-MM-DD"],
        "your_tables": engine.schema(con), "metrics": metrics,
        "column_meanings": model.get("columns", {}), "relationships": model.get("relationships", []),
        "confirmed_definitions": definitions(user_id),
        "documents": research.list_docs(user_id, workspace_id or "default")}, ensure_ascii=False, default=str)


PLANNER = """You plan how to answer a user's question (Indian user, amounts in INR, financial year April-March).
Questions are about their own money data, OR about a listed company's reported financials, OR recent news.
You never calculate or invent numbers; you choose what to run.
Rules:
- Prefer kind "metric" with a metric_query {metrics, dimensions, time, filters} using ONLY metric names, table
  columns and time expressions from the context. Metrics in one query must read the same table.
  Use dimensions (e.g. "symbol_or_scheme", "description", "symbol") to split results; "month" and "year" are also
  allowed dimensions (by month / by year). "This financial year" = time "fy"; "last year", "last financial year",
  "previous FY" and "FY <last year>" = "last_fy" (never "fy"); a single month
  = "YYYY-MM-01..YYYY-MM-<last day>". Filters compare a column with one plain value: {"column", "op", "value"};
  to find a word in a text column (e.g. rent, Swiggy in description) use op "contains".
  A period ALWAYS goes in "time", never in a filter on a date column.
  Spending, income, savings, portfolio value, gains and XIRR questions ALWAYS use their metric, never SQL.
- In "transactions", amount is always positive; direction is 'debit' (money out) or 'credit' (money in).
  SQL about spending / expenses / payments MUST filter direction = 'debit'; about income / money in, 'credit'.
- Metrics in one query must come from ONE table; never mix e.g. realized_gains (trades) with unrealized_pnl (holdings).
- "make", "earn", "return", "profit" and "gain" without a clear object are ambiguous (income? realized gains?
  unrealized P&L? XIRR?): use kind "clarify" with those options, unless a confirmed definition or the question
  itself settles it (e.g. "realized gains", "salary", "XIRR", "unrealized profit"). Advice or opinion questions
  ("is X a good investment?", "should I ...") are always "out_of_scope", never "clarify".
- Use kind "sql" only if no metric fits: ONE read-only DuckDB SELECT over the listed tables/columns.
  Join tables only on the listed relationships; column_meanings say what each column is.
- kind "company" with its US stock ticker (e.g. "NVDA", "AAPL") for ANY question about a US-listed company's
  reported revenue, profit, assets, debt, cash flow (from SEC filings). This IS answerable: do not mark it out of scope.
- kind "filing" for what one of the listed "documents" (a filing / annual report the user uploaded) SAYS: risks,
  strategy, segments, lawsuits, management commentary. Needs at least one listed document.
- kind "news" (news_query) for recent news about a company or topic.
- kind "calc" (expression) for arithmetic on numbers the user typed. Separate function arguments with ", ".
- kind "clarify" if a word could mean two different columns/metrics and no confirmed definition settles it:
  give clarify_question, clarify_term and 2-4 clarify_options.
- kind "out_of_scope" for buy/sell/hold advice, price targets, predictions, or anything the data can't answer:
  put a short polite reply WITHOUT numbers in "reply". Never recommend buying or selling.
- kind "smalltalk" for greetings: a one-line reply, no numbers.
- Confirmed definitions always win. If the question is a follow-up, adapt the previous plan."""

EXPLAINER = """Explain the results to a non-technical Indian investor in 1-3 short sentences.
Use ONLY numbers that appear in the results (you may round them as shown, or give a simple difference / % change
of two of them). Copy each number's digits exactly as given. Rupee amounts: ₹ with Indian grouping (₹1,23,456) or
lakh/crore. US company figures (values in USD millions): write "$215,938 million" with Western grouping, never ₹.
Mention the period if there is one.
Never give buy/sell/hold advice or predictions. If the results are empty, say no matching data was found."""


# Answer one question for one user (on one workspace's data when given). `previous_plan` makes follow-ups work;
# `define` stores a clarification first.
async def ask(user_id: str, question: str, previous_plan: dict | None = None,
              define: dict | None = None, workspace_id: str | None = None) -> Answer:
    from app.ai.llm_client import call_json, call_llm

    planned = question
    if define and define.get("term") and define.get("value"):  # the user just picked what a word means
        save_definition(user_id, define["term"], define["value"])
        planned = f'{question} (here "{define["term"]}" means: {define["value"]})'  # said outright, so it isn't asked again
    if previous_plan and previous_plan.get("kind") == "clarify":
        previous_plan = None  # the last answer was the question itself, not a plan to build on
    prompt = f"Context: {_context(user_id, workspace_id)}\nPrevious plan: {json.dumps(previous_plan) if previous_plan else 'none'}\nQuestion: {planned}"
    plan = await call_json(PLANNER, prompt, Plan, user_id=user_id)
    if plan is None:
        return Answer(text="The assistant is unavailable right now (the language model did not answer). "
                           "Your dashboards still work.", badge="none", verified=True)

    if plan.kind == "filing":  # the text of the user's documents: research.py answers and checks with page citations
        res = await research.answer(user_id, question, workspace_id or "default")
        return Answer(text=res["text"], badge="external", rows=res["citations"], warnings=res["warnings"], plan=plan,
                      verified=res["verified"], provenance={"tool": "research", "pages": [c["page"] for c in res["citations"]]})

    try:
        rows, provenance, warnings, badge = await _run(user_id, plan, workspace_id)
    except (ValueError, LookupError, TimeoutError) as e:  # SqlRejected is a ValueError
        return Answer(text=f"I couldn't answer that from your data: {e}", badge="none", plan=plan,
                      warnings=["the question was not answered"], verified=True)
    if rows is None:  # clarify / out_of_scope / smalltalk: a sentence with no numbers of its own
        text = plan.clarify_question if plan.kind == "clarify" else (plan.reply or "I can only answer questions about your data.")
        bad = check_numbers(text or "", [])
        return Answer(text=text if text and not bad else "I can only answer questions about your data.", badge="none",
                      plan=plan, clarify={"term": plan.clarify_term, "options": plan.clarify_options} if plan.kind == "clarify" else None)

    if plan.kind == "news":  # headlines speak for themselves: list them, no AI summary to misread them
        text = (f"Recent headlines for '{plan.news_query}' (from news sources, not checked by us):" if rows
                else f"No recent news found for '{plan.news_query}'.")
        return Answer(text=text, badge=badge, rows=rows, provenance=provenance, warnings=warnings, plan=plan)

    shown = rows[:MAX_ROWS_TO_EXPLAIN]
    period = (provenance or {}).get("period")
    raw = await call_llm(EXPLAINER, f"Question: {question}\nPeriod: {period}\nResults: {json.dumps(shown, default=str)}"
                                    f"\nWarnings: {warnings}", max_tokens=300, temperature=0, user_id=user_id)
    values = numbers_in(question)  # numbers the user typed may be repeated
    if plan.kind == "company":  # SEC values are in USD millions, so "215,938 million" must also match 215938
        values += [v * 1e6 for r in rows for v in r.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    unit = {"company": "USD", "calc": "mixed"}.get(plan.kind, "INR")  # the user's own data is in rupees
    bad = check_numbers(raw or "", values, unit, rows=rows)
    verified = bool(raw) and not bad
    text = raw if verified else _plain_summary(rows, period)
    if bad:
        warnings = warnings + [f"the written explanation had numbers not in the results ({', '.join(bad[:3])}), "
                               "so it was replaced by the table"]
    return Answer(text=f"{text}\n\n_{DISCLAIMER}_", badge=badge, rows=rows, provenance=provenance,
                  warnings=warnings, plan=plan, verified=verified)


# Run a plan in code. Returns (rows, provenance, warnings, badge); rows is None for plans that need no data.
async def _run(user_id: str, plan: Plan, workspace_id: str | None = None):
    if plan.kind == "metric" and plan.metric_query:
        r = await asyncio.to_thread(engine.run_metric, user_id, plan.metric_query, None, workspace_id)
        return r.rows, r.provenance.model_dump(), r.warnings, "trusted"
    if plan.kind == "sql" and plan.sql:
        r = await asyncio.to_thread(engine.run_sql, user_id, plan.sql, workspace_id)
        return r.rows, r.provenance.model_dump(), r.warnings, "exploratory"
    if plan.kind == "calc" and plan.expression:
        value = tools.calc(plan.expression)
        return [{"expression": plan.expression, "result": round(value, 4)}], {"tool": "calc"}, [], "trusted"
    if plan.kind == "company" and plan.ticker:
        facts = await asyncio.to_thread(tools.company_facts, plan.ticker)
        if facts.get("error"):
            raise engine.NotFound(facts["error"])
        rows = [{"metric": name, **row} for name, series in facts.get("facts", {}).items() for row in series[:3]]
        return rows, {"tool": "sec_edgar_xbrl", "company": facts.get("company_name"), "cik": facts.get("cik")}, \
            ["values in USD millions, from SEC XBRL filings"], "external"
    if plan.kind == "news" and plan.news_query:
        res = await asyncio.to_thread(tools.news, plan.news_query)
        rows = [{k: a.get(k) for k in ("title", "source", "published", "url")} for a in (res.get("articles") or [])]
        return rows, {"tool": "news", "query": plan.news_query}, ["news text is quoted as-is; it may be wrong"], "external"
    if plan.kind in ("clarify", "out_of_scope", "smalltalk"):
        return None, None, [], "none"
    raise ValueError(f"the plan '{plan.kind}' was missing its details")


# A plain, number-safe summary used when the written explanation fails the number check.
def _plain_summary(rows: list[dict], period: dict | None) -> str:
    when = f" for {period['start']} to {period['end']}" if period else ""
    return f"Here are the results{when} ({len(rows)} row{'s' if len(rows) != 1 else ''}), straight from your data:"
