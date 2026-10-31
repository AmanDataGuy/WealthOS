# WealthOS

Read `PORTFOLIO_PLAN.md` §0 before anything else, then §19 (status tracker) and the last rows of §21 (decisions) and §22 (results).

Hard rules (full list: §0.4 W1–W12): the LLM never produces a number the user sees (numbers come from SQL/Python and every
LLM sentence with numbers passes `verify.check_numbers`); stage explicit paths only, never `git add -A` / `git add .`;
nothing reaches `main` before the Phase 0 exit gate (P0.10); only synthetic fixtures in git; redact before every LLM call;
tenant and user come from the verified token; model-written SQL passes `verify.guard_sql` and runs on `engine.open_readonly`;
outbound messages are decided by code, never the model; every eval number comes from a committed live-run result file.
