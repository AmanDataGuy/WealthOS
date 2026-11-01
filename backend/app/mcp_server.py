"""
# mcp_server.py - your WealthOS numbers inside Claude Desktop / any MCP client

One **read-only** MCP server (stdio) over the same engine the app uses, so an AI assistant can look up your trusted
metrics instead of guessing. It can't upload, edit, delete or run free SQL.

| Tool | What it returns |
|---|---|
| `list_workspaces` | your workspaces and their tables |
| `list_metrics` | every metric you can ask for (built-in + the workspace's own), with table and unit |
| `query_metric` | the numbers, plus provenance: SQL, period dates, source files, data fingerprint |
| `explain_number` | how one metric is defined (its SQL / method), which files and dates it covers, and its value |
| `tax_estimate` | old vs new regime for a FY, capital gains from the tradebook |
| `portfolio_drift` | current vs target mix of the holdings |
| `list_backtests` | earlier strategy-lab runs |

**Who you are:** set `WEALTHOS_TOKEN` to the token the app gave you at sign-in; it is checked with
`WEALTHOS_JWT_SECRET`, exactly like the API. On a local install with no secret configured, `WEALTHOS_USER` names the
user instead, but only with `WEALTHOS_ENV=dev` (unset means production, which refuses).

Claude Desktop (`claude_desktop_config.json`):

    "wealthos": {"command": "D:/projects/WealthOS/venv/Scripts/python.exe",
                 "args": ["D:/projects/WealthOS/backend/app/mcp_server.py"],
                 "env": {"WEALTHOS_TOKEN": "<your token>", "WEALTHOS_JWT_SECRET": "<server secret>"}}
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/, so `app` imports

import jwt  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

from app.data import engine  # noqa: E402
from app.core import settings  # noqa: E402
from app.core import workspaces  # noqa: E402
from app.data.engine import MetricQuery  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
mcp = FastMCP("wealthos", instructions=(
    "Read-only access to the user's own WealthOS financial data (Indian user, INR, financial year April-March). "
    "Use list_metrics first, then query_metric. Quote numbers exactly as returned and mention the period. "
    "This is analytics on the user's data, not investment advice: never recommend buying or selling."))


# The signed-in user: from a verified WealthOS token, or WEALTHOS_USER on a local install without a secret.
def current_user() -> str:
    secret, token = os.getenv("WEALTHOS_JWT_SECRET", ""), os.getenv("WEALTHOS_TOKEN", "")
    if secret:
        secret = settings.jwt_secret()  # refuses a short or placeholder secret (forgeable tokens)
        if not token:
            raise PermissionError("set WEALTHOS_TOKEN to the token from signing in to WealthOS")
        return jwt.decode(token, secret, algorithms=["HS256"])["sub"]  # raises on a bad / expired token
    if settings.env() != "dev":
        raise PermissionError("WEALTHOS_JWT_SECRET is required (WEALTHOS_USER works only with WEALTHOS_ENV=dev)")
    user = os.getenv("WEALTHOS_USER", "")
    if not user:
        raise PermissionError("set WEALTHOS_USER (local install) or WEALTHOS_TOKEN + WEALTHOS_JWT_SECRET")
    return user


# Tool: the user's workspaces and their tables.
@mcp.tool()
def list_workspaces() -> list[dict]:
    """Your workspaces (groups of data) with the tables in each."""
    user = current_user()
    return [{"id": w["id"], "name": w["name"],
             "tables": [{"table": d["shape"], "file": d.get("source", {}).get("file"), "rows": d.get("rows")}
                        for d in engine.list_datasets(user, w["id"])]} for w in workspaces.list_workspaces(user)]


# Tool: the metrics that can be queried in a workspace.
@mcp.tool()
def list_metrics(workspace: str = "default") -> dict:
    """Every metric you can query in a workspace, with the table it reads and its unit; plus the time expressions."""
    defs = engine.load_metrics(current_user(), workspace)
    return {"metrics": {k: {"name": v["name"], "table": v["shape"], "unit": v.get("unit")} for k, v in defs.items()},
            "time": ["today", "mtd", "last_month", "qtd", "ytd", "fy", "last_fy", "trailing_12m", "all",
                     "YYYY-MM-DD..YYYY-MM-DD"],
            "dimensions": "any column of the metric's table, or 'month' / 'year'"}


# Tool: compute metrics for a period.
@mcp.tool()
def query_metric(metrics: list[str], time: str = "fy", dimensions: list[str] | None = None,
                 filters: list[dict] | None = None, workspace: str = "default") -> dict:
    """Compute metrics (all from the same table) for a period, optionally split by dimensions.
    filters: [{"column": "description", "op": "contains", "value": "rent"}]. Returns rows and provenance."""
    q = MetricQuery(metrics=metrics, time=time, dimensions=dimensions or [], filters=filters or [])
    r = engine.run_metric(current_user(), q, workspace_id=workspace)
    return {"rows": r.rows, "warnings": r.warnings, "provenance": r.provenance.model_dump()}


# Tool: show how one metric is computed.
@mcp.tool()
def explain_number(metric: str, time: str = "fy", workspace: str = "default") -> dict:
    """How one metric is computed: its definition, the exact SQL that ran, the period dates, the source files and
    the data fingerprint, together with its value."""
    user = current_user()
    d = engine.load_metrics(user, workspace).get(metric)
    if not d:
        raise ValueError(f"unknown metric '{metric}': use list_metrics")
    r = engine.run_metric(user, MetricQuery(metrics=[metric], time=time), workspace_id=workspace)
    return {"metric": metric, "name": d["name"], "unit": d.get("unit"), "table": d["shape"],
            "definition": d.get("sql") or f"computed in code ({d.get('python')})", "value": r.rows,
            **{k: v for k, v in r.provenance.model_dump().items() if k != "params"}}


# Tool: both income-tax regimes for a financial year.
@mcp.tool()
def tax_estimate(fy: str = "2026-27", salary: float = 0, other_income: float = 0, deductions_80c: float = 0,
                 deductions_80d: float = 0, workspace: str = "default") -> dict:
    """Tax under the old and new regime for a financial year ("2026-27"); capital gains are taken from the uploaded
    tradebook. An educational estimate from published rates, not tax advice."""
    from app.features.tax import compare
    return compare(current_user(), workspace, fy, salary, other_income, deductions_80c, deductions_80d)


# Tool: how far the portfolio is from the user's target mix.
@mcp.tool()
def portfolio_drift(workspace: str = "default") -> dict:
    """Each holding's current vs target weight (targets set by the user in WealthOS), drift in percentage points and
    the rupee change back to target. Arithmetic on the user's own targets, not advice."""
    from app.features.rebalance import drift
    return drift(current_user(), workspace)


# Tool: earlier backtests.
@mcp.tool()
def list_backtests() -> list[dict]:
    """The user's earlier strategy-lab runs (newest first): stocks, period, deflated Sharpe. Historical simulation,
    not advice."""
    from app.features.strategy import list_runs
    return list_runs(current_user())


if __name__ == "__main__":
    mcp.run()
