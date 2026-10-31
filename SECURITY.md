# Security

Threat model, known ceilings and reporting contact: added in PORTFOLIO_PLAN P0.2.6.

## Fixed issues

| Date | Issue | Fix |
|---|---|---|
| 2026-10-31 | **DB connector SSRF** (D-01): a workspace connection string could point at loopback, the private network, the cloud metadata endpoint or the app's own Postgres (exposing password hashes), and driver errors worked as a port-scan oracle. | `wealthos/netguard.py` resolves the host and refuses any non-public address, the app's own database host, ports other than 5432/3306 (plus `WEALTHOS_DB_ALLOWED_PORTS`) and libpq query options that redirect the connection (`host`, `hostaddr`, `service`, `port`, ...). The connection is pinned to the checked address (`hostaddr` for Postgres, `host=<ip>` for MySQL), so DNS rebinding after the check is not used. Connect errors are generic; the driver message is logged server side only. Private hosts are allowed only with `WEALTHOS_ENV=dev` and `WEALTHOS_ALLOW_PRIVATE_DB=1`. Tests: `tests/test_netguard.py`. |
| 2026-10-31 | **Unguarded auto-deploy** (D-04): every push to `main` ran `git reset --hard` + rebuild on EC2 with no tests. | `deploy.yml` is `workflow_dispatch` only and `if: false` until the CI-gated deploy (P1.2). Test: `tests/test_ci_config.py`. |
