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

## Open issues (found in review, not fixed yet)

| Issue | Where | Why it is not urgent yet |
|---|---|---|
| The strategy sandbox's name check misses `__builtins__` and only checks names and attributes, so subscript access gets past it | `backend/app/features/sandbox.py` | The feature is off in production; it must not be enabled for strangers before this and the container above |
| Workspace strings are written into Excel cells unescaped, and a template's defined names are not checked, so a cell can become a formula | `backend/app/features/excel.py` | Only the user's own data reaches their own workbook today |
| The daily LLM cost cap is skipped when no database URL is set, before the production check | `backend/app/ai/llm_client.py` | Docker Compose always sets the database URL |
| The price worker gets no `WEALTHOS_ENV`, so it runs with dev defaults under Docker | `docker-compose.yml` | It only fetches public prices |
| `WEALTHOS_TRUSTED_PROXIES` is empty by default, so behind nginx every user shares one IP for rate limits | `backend/app/main.py`, `.env.example` | Single-user local runs; set it before any public deploy |
| Account delete removes the user's files before the database transaction, so a failed transaction leaves an account with no data | `backend/app/api/account.py` | The user asked for deletion; data is not exposed |
| The API's `/docs` page and route list are public in production | `backend/app/main.py` | Route names only, no data; turn it off or put it behind auth before a public deploy |

## Fixed issues

| Date | Issue | Fix |
|---|---|---|
| 2026-10-31 | **DB connector SSRF**: a workspace connection string could point at loopback, the private network, the cloud metadata endpoint or the app's own Postgres (exposing password hashes), and driver errors worked as a port-scan oracle. | `wealthos/netguard.py` resolves the host and refuses any non-public address, the app's own database host, ports other than 5432/3306 (plus `WEALTHOS_DB_ALLOWED_PORTS`) and libpq query options that redirect the connection (`host`, `hostaddr`, `service`, `port`, ...). The connection is pinned to the checked address (`hostaddr` for Postgres, `host=<ip>` for MySQL), so DNS rebinding after the check is not used. Connect errors are generic; the driver message is logged server side only. Private hosts are allowed only with `WEALTHOS_ENV=dev` and `WEALTHOS_ALLOW_PRIVATE_DB=1`. Later the user database connectors were removed from the app altogether (the November rewrite), so this code and its tests are gone with them. |
| 2026-10-31 | **Unguarded auto-deploy**: every push to `main` ran `git reset --hard` + rebuild on EC2 with no tests. | The workflow was disabled, then removed from the repo. There is no deploy workflow now; the app is not deployed. |
| 2026-11-01 | **Auth failed open**: with no `WEALTHOS_JWT_SECRET` every route trusted the user id in the path; `.env.example` shipped the secret `...`; demo accounts with passwords published in the README were seeded. | `backend/app/core/settings.py`: the API refuses to start without a 32+ byte non-placeholder secret, `WEALTHOS_ENV` defaults to production, the only opt-out is `WEALTHOS_ENV=dev` + `WEALTHOS_DEV_INSECURE_AUTH=1`. Old default accounts are locked at startup; `WEALTHOS_DEMO_SEED=1` creates one random account. Tokens last 7 days. Tests: `backend/tests/test_settings.py`. |
| 2026-11-01 | **Error messages leaked internals** (DuckDB file paths, LibreOffice output, `KeyError` text) and any `KeyError` became a 404. | Only `engine.NotFound` maps to 404; server paths are scrubbed; unmapped errors are a generic 500 with a request id (`X-Request-ID`, also on log lines). Tests: `backend/tests/test_stress.py::test_errors_do_not_leak`. |
| 2026-11-01 | **Unlimited password guessing and a username timing oracle** at `/auth/login`. | 10 failures lock the account for 15 minutes; unknown, disabled and locked accounts run the same bcrypt check and get the same 401. Tests: `backend/tests/test_settings.py`. |
