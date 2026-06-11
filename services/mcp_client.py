# services/mcp_client.py
"""
MCP client wrapper for WealthOS.

Replaces direct Python imports from mcp_servers/ with proper MCP protocol
calls over stdio. The server runs as a subprocess; we talk to it via stdin/stdout
using the MCP SDK — which means any language-compatible server could plug in here.

Usage:
    async with MCPClient("mcp_servers/market_server.py") as client:
        result = await client.call_tool("get_price", {"ticker": "AAPL"})
"""

import sys
import json
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters


class MCPClient:
    """
    Wraps an MCP server subprocess and exposes a single call_tool() method.

    The server is spawned fresh on connect() and stays alive until close().
    If a tool call fails (e.g. server crashed), we attempt one reconnect before
    raising — so transient failures don't permanently break a pipeline run.
    """

    def __init__(self, server_script: str):
        # Resolve to absolute path so subprocess can find it from any cwd
        self.server_script = str(Path(server_script).resolve())
        self._session: ClientSession | None = None
        self._stdio_ctx = None

    async def connect(self):
        """Start the server subprocess and initialize the MCP session."""
        params = StdioServerParameters(
            command=sys.executable,
            args=[self.server_script],
        )
        self._stdio_ctx = stdio_client(params)
        read, write = await self._stdio_ctx.__aenter__()
        self._session = ClientSession(read, write)
        await self._session.__aenter__()
        await self._session.initialize()

    async def call_tool(self, tool_name: str, params: dict[str, Any]) -> Any:
        """
        Call a tool on the server. Returns parsed JSON if the result is a JSON
        string, otherwise returns the raw text content.

        Retries once if the server appears to have died.
        """
        if not self._session:
            raise RuntimeError("Not connected — use 'async with MCPClient(...)' or call connect() first")

        try:
            return await self._do_call(tool_name, params)
        except Exception as e:
            # One retry after reconnect — handles the "server crashed mid-run" case
            print(f"[mcp_client] Tool call failed ({e}), reconnecting and retrying once...")
            await self.close()
            await self.connect()
            return await self._do_call(tool_name, params)

    async def _do_call(self, tool_name: str, params: dict[str, Any]) -> Any:
        result = await self._session.call_tool(tool_name, params)
        if not result.content:
            return None
        raw = result.content[0].text if hasattr(result.content[0], "text") else str(result.content[0])
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw

    async def close(self):
        """Shut down the session and subprocess cleanly."""
        if self._session:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:
                pass
            self._session = None
        if self._stdio_ctx:
            try:
                await self._stdio_ctx.__aexit__(None, None, None)
            except Exception:
                pass
            self._stdio_ctx = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.close()
