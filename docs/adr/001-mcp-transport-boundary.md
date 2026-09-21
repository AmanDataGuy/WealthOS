# ADR 001 — MCP Transport Boundary

**Status:** Accepted
**Date:** 2026-09-22

## Context

WealthOS has 5 MCP servers (`market_server`, `sec_edgar_server`, `news_server`,
`finance_server`, `tax_server`, 21 tools total). Only some pipeline calls go
through them via `services/mcp_client.py`'s `MCPClient`; others call the same
server modules' functions directly as plain Python imports. As of the current
graph (`graph/nodes.py`):

| Call | Transport |
|---|---|
| `finance_node` → `finance_server.get_transactions` | `MCPClient` |
| `data_and_research_node` → `market_server` (primary path) | `MCPClient`, direct-import fallback on connection failure |
| `risk_and_code_node` → `market_server` | `MCPClient` |
| `router_node` → `sec_edgar_server` | direct Python import |
| `data_and_research_node` → `news_server` | direct Python import |
| `rebalancing_node` → `market_server` | direct Python import |
| `tax_node` → `tax_server` | direct Python import |

This looked, on the surface, like a migration that stopped halfway — the
honest answer to "why does `rebalancing` bypass MCP" was previously "it just
does," which isn't defensible on its own.

## Decision

Keep the split. Do not migrate `sec_edgar_server`, `news_server`, or
`rebalancing`'s market calls onto `MCPClient`.

`MCPClient.connect()` spawns a fresh server **subprocess** per session
(`services/mcp_client.py:38-45`) — Python interpreter boot + stdio handshake
on top of the actual tool call. That's real, structural overhead on every
call, independent of what the tool does. The three call sites kept as direct
imports share a trait: they're either off the pipeline's hot path or already
protected by their own caching/fallback layer, so the subprocess cost buys
nothing:

- **`sec_edgar_server`** (router_node) — fires once per query, only to check
  whether a ticker's 10-K is indexed; not repeated inside the 8-node run.
- **`news_server`** (data_and_research_node) — one call per analysis, already
  running in parallel with the data-fetch branch; not latency-critical
  relative to the LLM calls elsewhere in the same node.
- **`rebalancing_node`**'s market calls — a single portfolio-concentration
  check, not a per-tool-call hot path like `risk_and_code_node`'s repeated
  market lookups.
- **`tax_server`** (tax_node) — fires at most once per run, and only when
  `involves_taxable_decision()` matches the query at all; most runs never
  call it. No case for paying subprocess-spawn cost on a call this rare.

`finance_node`, `data_and_research_node` (market), and `risk_and_code_node`
keep `MCPClient` because those are the calls made repeatedly within a single
run and benefit most from the subprocess staying warm across the session,
plus `MCPClient`'s built-in retry-on-crash (`mcp_client.py:63-67`).

## Consequences

- The transport choice is now a documented tradeoff, not an unexplained
  inconsistency — this file is the answer to "why does X bypass MCP."
- **Known gap:** this decision is reasoned from the subprocess-per-session
  cost being structural, not from a measured benchmark. No `sec_edgar_server`
  direct-import-vs-MCPClient latency comparison has actually been run in this
  repo. If MCP transport becomes a bottleneck or a correctness gap (e.g. a
  direct-import call needs the retry-on-crash behavior `MCPClient` provides),
  re-open this ADR with real numbers before changing the boundary.
- Any new tool call added to the hot path (called repeatedly within one
  8-node run) should default to `MCPClient`; anything called once per run
  or already cached should default to direct import, per the table above.
