# WealthOS

Hard rules: the LLM never produces a number the user sees (numbers come from SQL/Python and every LLM sentence with
numbers passes `verify.check_numbers`); add explicit paths only, never `git add -A` / `git add .`; only synthetic
fixtures in git; redact before every LLM call; tenant and user come from the verified token; model-written SQL passes
`verify.guard_sql` and runs on `engine.open_readonly`; outbound messages are decided by code, never the model; every
eval number comes from a committed live-run result file.
