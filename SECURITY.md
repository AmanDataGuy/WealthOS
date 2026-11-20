# Security

## Reporting

Please report vulnerabilities privately through a GitHub security advisory on this repository, not a public issue.

## Known ceilings (deliberate, with the planned fix)

| Ceiling | Why it is acceptable for now | Planned fix |
|---|---|---|
| Session token in `localStorage` | No third-party scripts; links from data are protocol-checked | httpOnly cookie session + CSRF |
| One API process, one worker | Locks and JSON state are per process | Shared state in Postgres |
| PII redaction is regex-based | Every LLM prompt passes one redactor: PAN, GSTIN, Aadhaar, phones, cards, accounts, IFSC, emails, UPI ids; not names in free text or UPI narrations | Name recognition (NER) |
| Rate limits are per API process | 10/min per IP on `/auth/*`, 20/min per user on AI routes, 300/min otherwise | Shared buckets in Postgres for more than one replica |
| Python strategy uploads run in a checked, time-limited child process, not a container | Code is checked before it runs (pandas / numpy / math only, no files, network or internals) and the feature is off in production by default | Run the child in a no-network container (gVisor / Modal / E2B) before enabling for strangers |

## Fixed issues

| Date | Issue | Fix |
|---|---|---|
| 2026-10-31 | **DB connector SSRF**: a workspace connection string could point at loopback, the private network, the cloud metadata endpoint or the app's own Postgres (exposing password hashes), and driver errors worked as a port-scan oracle. | `wealthos/netguard.py` resolves the host and refuses any non-public address, the app's own database host, ports other than 5432/3306 (plus `WEALTHOS_DB_ALLOWED_PORTS`) and libpq query options that redirect the connection (`host`, `hostaddr`, `service`, `port`, ...). The connection is pinned to the checked address (`hostaddr` for Postgres, `host=<ip>` for MySQL), so DNS rebinding after the check is not used. Connect errors are generic; the driver message is logged server side only. Private hosts are allowed only with `WEALTHOS_ENV=dev` and `WEALTHOS_ALLOW_PRIVATE_DB=1`. Later the user database connectors were removed from the app altogether (the November rewrite), so this code and its tests are gone with them. |
| 2026-10-31 | **Unguarded auto-deploy**: every push to `main` ran `git reset --hard` + rebuild on EC2 with no tests. | The workflow was disabled, then removed from the repo. There is no deploy workflow now; the app is not deployed. |
| 2026-11-01 | **Auth failed open**: with no `WEALTHOS_JWT_SECRET` every route trusted the user id in the path; `.env.example` shipped the secret `...`; demo accounts with passwords published in the README were seeded. | `backend/app/core/settings.py`: the API refuses to start without a 32+ byte non-placeholder secret, `WEALTHOS_ENV` defaults to production, the only opt-out is `WEALTHOS_ENV=dev` + `WEALTHOS_DEV_INSECURE_AUTH=1`. Old default accounts are locked at startup; `WEALTHOS_DEMO_SEED=1` creates one random account. Tokens last 7 days. Tests: `backend/tests/test_settings.py`. |
| 2026-11-01 | **Error messages leaked internals** (DuckDB file paths, LibreOffice output, `KeyError` text) and any `KeyError` became a 404. | Only `engine.NotFound` maps to 404; server paths are scrubbed; unmapped errors are a generic 500 with a request id (`X-Request-ID`, also on log lines). Tests: `backend/tests/test_stress.py::test_errors_do_not_leak`. |
| 2026-11-01 | **Unlimited password guessing and a username timing oracle** at `/auth/login`. | 10 failures lock the account for 15 minutes; unknown, disabled and locked accounts run the same bcrypt check and get the same 401. Tests: `backend/tests/test_settings.py`. |
| 2026-11-20 | **Strategy sandbox escapes**: the name check missed `__builtins__` and only looked at names and attributes, so a subscript such as `x["__import__"]` or `from numpy import __builtins__` got past it. | `backend/app/features/sandbox.py` also refuses `__builtins__` / `builtins`, any dunder name, any dunder string constant, and banned names imported with `from ... import`; allowed imports are unchanged. Tests: `backend/tests/test_strategy.py::test_python_sandbox_refuses_escapes`, `test_python_sandbox_allows_plain_strategies`. |
| 2026-11-20 | **Excel formula injection**: workspace strings starting with `=` became formulas in the Data sheet, and a template's defined names were not checked. | `backend/app/features/excel.py` writes strings starting with `= + - @` as text cells (shown as typed), and runs every defined name through the same formula check as cells (WEBSERVICE / HYPERLINK, other workbooks, DDE links refused). Tests: `backend/tests/test_excel.py::test_data_strings_written_as_text`, `test_defined_names_checked`. |
| 2026-11-20 | **Cost cap skipped without a database**: with no `WEALTHOS_DB_URL` the daily LLM budget was never checked, even in production. | `backend/app/ai/llm_client.py`: in production the call is refused with `CostCapReached`; dev and test behave as before. Test: `backend/tests/test_settings.py::test_cost_cap_fails_closed_without_db_in_production`. |
| 2026-11-20 | **Price worker ran with dev defaults** under Docker (no `WEALTHOS_ENV`). | `docker-compose.yml` sets `WEALTHOS_ENV: production` on the worker too; NSE prices then need `WEALTHOS_ENABLE_NSE=1`, as for the API. Checked with `docker compose config`. |
| 2026-11-20 | **One shared IP behind nginx**: `WEALTHOS_TRUSTED_PROXIES` was empty and took exact IPs only, so every user shared the proxy's rate limit. | `backend/app/main.py` accepts exact IPs and CIDR ranges; `docker-compose.yml` gives the network a fixed subnet and the web container a fixed address, which the API trusts. Test: `backend/tests/test_settings.py::test_trusted_proxies_exact_ip_and_cidr` (also a spoofed header from an untrusted client). |
| 2026-11-20 | **Account delete removed files first**, so a failed transaction left an account with no data. | `backend/app/api/account.py` deletes the database rows in their transaction first, then the files. Test: `backend/tests/test_settings.py::test_account_delete_keeps_files_when_the_transaction_fails`. |
| 2026-11-20 | **Public API docs in production**: `/docs`, `/redoc` and `/openapi.json` listed every route. | `backend/app/main.py` turns them off in production; on in dev and test. Test: `backend/tests/test_settings.py::test_docs_only_outside_production`. |
