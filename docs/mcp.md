# The MCP server

`backend/app/mcp_server.py` is a small, **read-only** [Model Context Protocol](https://modelcontextprotocol.io)
server. It lets Claude Desktop (or any MCP client) ask WealthOS for your numbers instead of guessing them. It runs
over stdio on the same machine as your WealthOS data, uses the same engine and metric definitions as the app, and
returns every number with where it came from.

## Connect it to Claude Desktop

Add this to `claude_desktop_config.json` (Settings > Developer > Edit Config), then restart Claude Desktop.
Replace the placeholders with your own paths and token.

```json
{
  "mcpServers": {
    "wealthos": {
      "command": "/path/to/WealthOS/venv/bin/python",
      "args": ["/path/to/WealthOS/backend/app/mcp_server.py"],
      "env": {
        "WEALTHOS_TOKEN": "<the token from signing in to WealthOS>",
        "WEALTHOS_JWT_SECRET": "<the API's WEALTHOS_JWT_SECRET>"
      }
    }
  }
}
```

- On Windows the paths look like `C:/path/to/WealthOS/venv/Scripts/python.exe`.
- **The token** is the one the app gets at sign-in (`POST /auth/login` returns it as `token`). It lasts
  `WEALTHOS_TOKEN_DAYS` (default 7); after that, sign in again and paste the new one.
- **The secret** may be left out of the config: the server also reads the repository's `.env`, where the API's
  `WEALTHOS_JWT_SECRET` already lives.
- **Local-only shortcut:** with no secret at all, `WEALTHOS_ENV=dev` plus `WEALTHOS_USER=<user id>` names the user
  directly. Any other environment refuses this, so it cannot happen on a server by accident.
- Data is read from `backend/storage/users/<user id>/`, whatever folder Claude Desktop starts the server in.

Try: "Using WealthOS, what did I spend this financial year, and how is that number computed?"

## Tools

All tools take an optional `workspace` (default `"default"`, the "My files" workspace) except where noted.

| Tool | Arguments | Returns |
|---|---|---|
| `list_workspaces` | none | Your workspaces, each with its tables: `table` (transactions, trades, holdings, prices ...), source `file`, `rows`. |
| `list_metrics` | `workspace` | `metrics`: every built-in and workspace metric with `name`, `table`, `unit`; the accepted `time` expressions; what `dimensions` may be. |
| `query_metric` | `metrics`, `time` (default `fy`), `dimensions`, `filters`, `workspace` | `rows` of results, `warnings`, and `provenance`: the SQL that ran with its parameters, the period's start and end dates, the source files with their content hashes, and the data fingerprint. |
| `explain_number` | `metric`, `time`, `workspace` | One metric's definition (its SQL, or the code that computes it), its `value`, and the same provenance fields as above. An unknown metric is an error that points to `list_metrics`. |
| `tax_estimate` | `fy` (e.g. `"2026-27"`), `salary`, `other_income`, `deductions_80c`, `deductions_80d`, `workspace` | Tax under the new and the old regime, which is `lower` and by how much, capital gains (short and long term) taken from your uploaded tradebook with their provenance, 80C headroom, warnings and the rules' sources. An educational estimate, not tax advice. |
| `portfolio_drift` | `workspace` | Total holdings value and, per holding, its value, current and target weight, drift in percentage points, the rupee change back to target and whether it is outside the 5-point band. Targets are the ones you set in the app. |
| `list_backtests` | none | Your earlier strategy-lab runs, newest first: stocks, period, number of tries, Sharpe and deflated Sharpe. Historical simulation, not advice. |

There is deliberately no tool that runs free SQL, uploads, edits or deletes anything.

## Security model

- **Read-only.** Every tool reads; none writes. The server cannot upload files, change dashboards or targets,
  delete data, or run model-written SQL.
- **One user per server.** The user comes only from the verified token (`sub` claim), checked with the same secret
  and algorithm as the API. A missing, expired or forged token is refused, and every tool reads only that user's
  folder; a token for another user sees only that user's data. This is tested in `backend/tests/test_mcp_server.py`.
- **The secret is the limit.** Checking the token needs the API's signing secret on the machine running the MCP
  server. Anyone holding that secret can sign a token for any user, so this setup is for the machine that already
  runs WealthOS (your own laptop or server), never for handing to someone else. Letting other people connect from
  their own machines needs a per-user API key that the server can check without sharing the secret; that is not
  built.
- **No network.** The server talks only to your local files over stdio; it opens no port.

## Tests

`backend/tests/test_mcp_server.py` calls every tool through the MCP layer on the synthetic sample data
(`backend/app/data/samples.py`): workspaces and metrics, a metric with provenance, the explanation of one number,
the tax estimate with capital gains from the sample tradebook, drift against a 50/50 target, and the backtest list.
It also checks the tool list holds no write or SQL tool and that a missing or forged token is refused.
