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

## Fixed issues

| Date | Issue | Fix |
|---|---|---|
| 2026-10-31 | **DB connector SSRF**: a workspace connection string could point at loopback, the private network, the cloud metadata endpoint or the app's own Postgres (exposing password hashes), and driver errors worked as a port-scan oracle. | `wealthos/netguard.py` resolves the host and refuses any non-public address, the app's own database host, ports other than 5432/3306 (plus `WEALTHOS_DB_ALLOWED_PORTS`) and libpq query options that redirect the connection (`host`, `hostaddr`, `service`, `port`, ...). The connection is pinned to the checked address (`hostaddr` for Postgres, `host=<ip>` for MySQL), so DNS rebinding after the check is not used. Connect errors are generic; the driver message is logged server side only. Private hosts are allowed only with `WEALTHOS_ENV=dev` and `WEALTHOS_ALLOW_PRIVATE_DB=1`. Tests: `tests/test_netguard.py`. |
| 2026-10-31 | **Unguarded auto-deploy**: every push to `main` ran `git reset --hard` + rebuild on EC2 with no tests. | `deploy.yml` is `workflow_dispatch` only and `if: false` until a deploy gated on CI replaces it. Test: `tests/test_ci_config.py`. |
| 2026-11-01 | **Auth failed open**: with no `WEALTHOS_JWT_SECRET` every route trusted the user id in the path; `.env.example` shipped the secret `...`; demo accounts with passwords published in the README were seeded. | `wealthos/settings.py`: the API refuses to start without a 32+ byte non-placeholder secret, `WEALTHOS_ENV` defaults to production, the only opt-out is `WEALTHOS_ENV=dev` + `WEALTHOS_DEV_INSECURE_AUTH=1`. Old default accounts are locked at startup; `WEALTHOS_DEMO_SEED=1` creates one random account. Tokens last 7 days. Tests: `tests/test_settings.py`. |
| 2026-11-01 | **Error messages leaked internals** (DuckDB file paths, LibreOffice output, `KeyError` text) and any `KeyError` became a 404. | Only `engine.NotFound` maps to 404; server paths are scrubbed; unmapped errors are a generic 500 with a request id (`X-Request-ID`, also on log lines). Tests: `tests/test_stress.py::test_errors_do_not_leak`. |
| 2026-11-01 | **Unlimited password guessing and a username timing oracle** at `/auth/login`. | 10 failures lock the account for 15 minutes; unknown, disabled and locked accounts run the same bcrypt check and get the same 401. Tests: `tests/test_settings.py`. |
