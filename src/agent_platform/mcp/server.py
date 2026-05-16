"""MCP server exposing agent-platform capabilities as tool calls.

Implements a lightweight Model Context Protocol server that external AI
tools (Claude Code, Cursor, etc.) can use to interact with the platform.
"""

from __future__ import annotations

import logging
from typing import Any

from agent_platform.evals.runner import EvalRunner
from agent_platform.registry.deployment import DeploymentAuditLog
from agent_platform.registry.registry import AgentNotFoundError, AgentRegistry
from agent_platform.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool definitions (MCP format)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "list_agents",
        "description": "List all registered agents on the platform.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_agent",
        "description": "Get details for a specific agent by its ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "The unique identifier of the agent.",
                },
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "list_deployments",
        "description": "List all agent deployments across channels.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "list_tools",
        "description": "List all tools registered in the platform tool registry.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "run_eval",
        "description": "Run the evaluation suite for a specific agent and return the report.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "The agent ID to evaluate.",
                },
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "deployment_audit",
        "description": "List deployment audit events, optionally filtered by agent or channel.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Filter events by agent ID.",
                },
                "channel": {
                    "type": "string",
                    "description": "Filter events by deployment channel (dev, staging, prod).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of events to return (default 50).",
                },
            },
            "required": [],
        },
    },
]


class MCPToolError(Exception):
    """Raised when an MCP tool invocation fails."""

    def __init__(self, message: str, code: int = -32000):
        super().__init__(message)
        self.code = code


class AgentPlatformMCPServer:
    """Exposes agent-platform capabilities as MCP tools."""

    def __init__(
        self,
        *,
        registry: AgentRegistry,
        tool_registry: ToolRegistry,
        eval_runner: EvalRunner,
        audit_log: DeploymentAuditLog,
    ) -> None:
        self.registry = registry
        self.tool_registry = tool_registry
        self.eval_runner = eval_runner
        self.audit_log = audit_log

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def list_tools(self) -> list[dict[str, Any]]:
        """Return tool definitions in MCP ``tools/list`` format."""
        return list(TOOL_DEFINITIONS)

    async def handle_request(
        self, method: str, params: dict[str, Any] | None = None,
    ) -> Any:
        """Dispatch an MCP tool call to the appropriate handler.

        *method* is the tool name.  Returns a JSON-serialisable result
        or raises :class:`MCPToolError`.
        """
        params = params or {}
        handler = self._handlers.get(method)
        if handler is None:
            raise MCPToolError(f"Unknown tool: {method}", code=-32601)
        return await handler(self, params)

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    async def _handle_list_agents(self, _params: dict[str, Any]) -> list[dict[str, Any]]:
        agents = await self.registry.list_agents()
        return [
            {
                "agent_id": spec.agent_id,
                "version": spec.version,
                "name": spec.manifest.metadata.name,
                "description": spec.manifest.metadata.description,
                "runtime_backend": spec.manifest.runtime.backend,
            }
            for spec in agents
        ]

    async def _handle_get_agent(self, params: dict[str, Any]) -> dict[str, Any]:
        agent_id = params.get("agent_id")
        if not agent_id:
            raise MCPToolError("Missing required parameter: agent_id")
        try:
            spec = await self.registry.get(agent_id)
        except AgentNotFoundError as exc:
            raise MCPToolError(str(exc)) from exc
        return {
            "agent_id": spec.agent_id,
            "version": spec.version,
            "name": spec.manifest.metadata.name,
            "description": spec.manifest.metadata.description,
            "runtime_backend": spec.manifest.runtime.backend,
            "tools": {
                "allow": spec.manifest.tools.allow,
                "deny": spec.manifest.tools.deny,
            },
            "routing_strategy": spec.manifest.routing.strategy,
        }

    async def _handle_list_deployments(self, _params: dict[str, Any]) -> list[dict[str, Any]]:
        deployments = await self.registry.list_deployments()
        return [
            {
                "deployment_id": d.deployment_id,
                "agent_id": d.agent_id,
                "version": d.version,
                "channel": d.channel,
                "status": str(d.status),
                "traffic_percent": d.traffic_percent,
            }
            for d in deployments
        ]

    async def _handle_list_tools(self, _params: dict[str, Any]) -> list[dict[str, Any]]:
        tools = self.tool_registry.list_tools()
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
                "owner": t.owner,
                "risk_level": t.risk_level,
            }
            for t in tools
        ]

    async def _handle_run_eval(self, params: dict[str, Any]) -> dict[str, Any]:
        agent_id = params.get("agent_id")
        if not agent_id:
            raise MCPToolError("Missing required parameter: agent_id")
        try:
            spec = await self.registry.get(agent_id)
        except AgentNotFoundError as exc:
            raise MCPToolError(str(exc)) from exc
        report = await self.eval_runner.run_agent(spec)
        return report.model_dump(mode="json")

    async def _handle_deployment_audit(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        events = await self.audit_log.list_events(
            agent_id=params.get("agent_id"),
            channel=params.get("channel"),
            limit=params.get("limit", 50),
        )
        return [e.model_dump(mode="json") for e in events]

    # ------------------------------------------------------------------
    # Dispatch table
    # ------------------------------------------------------------------

    _handlers: dict[str, Any] = {
        "list_agents": _handle_list_agents,
        "get_agent": _handle_get_agent,
        "list_deployments": _handle_list_deployments,
        "list_tools": _handle_list_tools,
        "run_eval": _handle_run_eval,
        "deployment_audit": _handle_deployment_audit,
    }
