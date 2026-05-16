"""Minimal stdio transport for the Agent Platform MCP server.

Reads JSON-RPC 2.0 requests from stdin, dispatches them through
:class:`AgentPlatformMCPServer`, and writes JSON-RPC 2.0 responses to
stdout.  Supports the ``initialize``, ``tools/list``, and ``tools/call``
methods required by the MCP specification.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any, TextIO

from agent_platform.mcp.server import AgentPlatformMCPServer, MCPToolError

logger = logging.getLogger(__name__)

# MCP protocol constants
MCP_PROTOCOL_VERSION = "2024-11-05"

SERVER_INFO = {
    "name": "agent-platform",
    "version": "0.1.0",
}

SERVER_CAPABILITIES = {
    "tools": {"listChanged": False},
}


def _make_response(id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id, "result": result}


def _make_error(id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": id, "error": error}


class StdioTransport:
    """JSON-RPC 2.0 stdio transport for MCP."""

    def __init__(
        self,
        server: AgentPlatformMCPServer,
        *,
        input_stream: TextIO | None = None,
        output_stream: TextIO | None = None,
    ) -> None:
        self.server = server
        self._input = input_stream or sys.stdin
        self._output = output_stream or sys.stdout

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Read lines from stdin and process them until EOF."""
        loop = asyncio.get_event_loop()
        while True:
            line = await loop.run_in_executor(None, self._input.readline)
            if not line:
                break  # EOF
            line = line.strip()
            if not line:
                continue
            response = await self._process_line(line)
            if response is not None:
                self._write(response)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _process_line(self, line: str) -> dict[str, Any] | None:
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            return _make_error(None, -32700, "Parse error")

        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})

        if method is None:
            return _make_error(req_id, -32600, "Invalid Request: missing method")

        # Notifications (no id) -- we don't send a response.
        is_notification = req_id is None

        try:
            result = await self._dispatch(method, params)
        except Exception as exc:
            if is_notification:
                logger.exception("Error handling notification %s", method)
                return None
            code = getattr(exc, "code", -32603)
            return _make_error(req_id, code, str(exc))

        if is_notification:
            return None
        return _make_response(req_id, result)

    async def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "initialize":
            return self._handle_initialize(params)
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return self._handle_tools_list()
        if method == "tools/call":
            return await self._handle_tools_call(params)
        raise MCPToolError(f"Method not found: {method}", code=-32601)

    # ------------------------------------------------------------------
    # MCP method handlers
    # ------------------------------------------------------------------

    def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "serverInfo": SERVER_INFO,
            "capabilities": SERVER_CAPABILITIES,
        }

    def _handle_tools_list(self) -> dict[str, Any]:
        return {"tools": self.server.list_tools()}

    async def _handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        tool_name = params.get("name")
        if not tool_name:
            raise MCPToolError("Missing required parameter: name")
        arguments = params.get("arguments", {})
        try:
            result = await self.server.handle_request(tool_name, arguments)
            return {
                "content": [
                    {"type": "text", "text": json.dumps(result, default=str)},
                ],
                "isError": False,
            }
        except MCPToolError:
            raise
        except Exception as exc:
            logger.exception("Tool call failed: %s", tool_name)
            return {
                "content": [
                    {"type": "text", "text": str(exc)},
                ],
                "isError": True,
            }

    def _write(self, response: dict[str, Any]) -> None:
        self._output.write(json.dumps(response) + "\n")
        self._output.flush()
