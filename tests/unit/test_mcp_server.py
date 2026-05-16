"""Tests for AgentPlatformMCPServer and StdioTransport."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_platform.domain.models import (
    AgentDeployment,
    AgentDeploymentStatus,
    AgentManifest,
    AgentSpec,
    ManifestEntry,
    ManifestEvals,
    ManifestMetadata,
    ManifestOutput,
    ManifestRouting,
    ManifestRuntime,
    ManifestTools,
    ManifestVersion,
)
from agent_platform.mcp.server import (
    AgentPlatformMCPServer,
    MCPToolError,
)
from agent_platform.mcp.stdio_transport import StdioTransport
from agent_platform.registry.deployment import DeploymentEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manifest(
    agent_id: str = "test-agent",
    name: str = "Test Agent",
    version: str = "1.0.0",
) -> AgentManifest:
    return AgentManifest(
        api_version="agent.platform/v1",
        kind="AgentPackage",
        metadata=ManifestMetadata(id=agent_id, name=name, description="A test agent"),
        version=ManifestVersion(package_version=version),
        entry=ManifestEntry(),
        runtime=ManifestRuntime(backend="native"),
        tools=ManifestTools(allow=["search"], deny=["delete"]),
        routing=ManifestRouting(strategy="single"),
        output=ManifestOutput(),
        evals=ManifestEvals(),
    )


def _make_spec(agent_id: str = "test-agent", version: str = "1.0.0") -> AgentSpec:
    return AgentSpec(
        manifest=_make_manifest(agent_id=agent_id, version=version),
        package_path=Path("/tmp/agents") / agent_id,
    )


def _make_deployment(
    agent_id: str = "test-agent",
    version: str = "1.0.0",
    channel: str = "dev",
) -> AgentDeployment:
    return AgentDeployment(
        deployment_id=f"dep_{agent_id}_{channel}",
        agent_id=agent_id,
        version=version,
        channel=channel,
        status=AgentDeploymentStatus.REGISTERED,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_registry() -> MagicMock:
    reg = MagicMock()
    reg.list_agents = AsyncMock(return_value=[_make_spec()])
    reg.get = AsyncMock(return_value=_make_spec())
    reg.list_deployments = AsyncMock(return_value=[_make_deployment()])
    return reg


@pytest.fixture
def mock_tool_registry() -> MagicMock:
    tool = MagicMock()
    tool.name = "search"
    tool.description = "Search things"
    tool.input_schema = {"type": "object"}
    tool.owner = "test-agent"
    tool.risk_level = "low"

    reg = MagicMock()
    reg.list_tools = MagicMock(return_value=[tool])
    return reg


@pytest.fixture
def mock_eval_runner() -> MagicMock:
    report = MagicMock()
    report.model_dump = MagicMock(return_value={
        "agent_id": "test-agent",
        "total": 2,
        "passed": 2,
        "pass_rate": 1.0,
        "required_pass_rate": 0.9,
        "gate_passed": True,
        "results": [],
    })

    runner = MagicMock()
    runner.run_agent = AsyncMock(return_value=report)
    return runner


@pytest.fixture
def mock_audit_log() -> MagicMock:
    event = DeploymentEvent(
        event_type="deploy",
        agent_id="test-agent",
        version="1.0.0",
        channel="prod",
        status=AgentDeploymentStatus.PROD,
    )
    log = MagicMock()
    log.list_events = AsyncMock(return_value=[event])
    return log


@pytest.fixture
def mcp_server(
    mock_registry: MagicMock,
    mock_tool_registry: MagicMock,
    mock_eval_runner: MagicMock,
    mock_audit_log: MagicMock,
) -> AgentPlatformMCPServer:
    return AgentPlatformMCPServer(
        registry=mock_registry,
        tool_registry=mock_tool_registry,
        eval_runner=mock_eval_runner,
        audit_log=mock_audit_log,
    )


# ===========================================================================
# Tests — tool listing
# ===========================================================================


class TestToolListing:
    def test_list_tools_returns_all_six(self, mcp_server: AgentPlatformMCPServer):
        tools = mcp_server.list_tools()
        assert len(tools) == 6

    def test_list_tools_names(self, mcp_server: AgentPlatformMCPServer):
        names = {t["name"] for t in mcp_server.list_tools()}
        expected = {
            "list_agents",
            "get_agent",
            "list_deployments",
            "list_tools",
            "run_eval",
            "deployment_audit",
        }
        assert names == expected

    def test_each_tool_has_required_fields(self, mcp_server: AgentPlatformMCPServer):
        for tool in mcp_server.list_tools():
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            assert isinstance(tool["inputSchema"], dict)

    def test_get_agent_schema_requires_agent_id(self, mcp_server: AgentPlatformMCPServer):
        get_agent = next(t for t in mcp_server.list_tools() if t["name"] == "get_agent")
        assert "agent_id" in get_agent["inputSchema"]["properties"]
        assert "agent_id" in get_agent["inputSchema"]["required"]

    def test_run_eval_schema_requires_agent_id(self, mcp_server: AgentPlatformMCPServer):
        run_eval = next(t for t in mcp_server.list_tools() if t["name"] == "run_eval")
        assert "agent_id" in run_eval["inputSchema"]["required"]

    def test_deployment_audit_schema_has_optional_filters(
        self, mcp_server: AgentPlatformMCPServer,
    ):
        audit = next(t for t in mcp_server.list_tools() if t["name"] == "deployment_audit")
        props = audit["inputSchema"]["properties"]
        assert "agent_id" in props
        assert "channel" in props
        assert "limit" in props
        assert audit["inputSchema"]["required"] == []

    def test_tool_definitions_are_independent_copies(
        self, mcp_server: AgentPlatformMCPServer,
    ):
        """Ensure list_tools returns a fresh list each time."""
        a = mcp_server.list_tools()
        b = mcp_server.list_tools()
        assert a is not b


# ===========================================================================
# Tests — tool handlers
# ===========================================================================


class TestListAgentsHandler:
    @pytest.mark.asyncio
    async def test_returns_agent_list(self, mcp_server: AgentPlatformMCPServer):
        result = await mcp_server.handle_request("list_agents")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["agent_id"] == "test-agent"
        assert result[0]["version"] == "1.0.0"
        assert result[0]["name"] == "Test Agent"

    @pytest.mark.asyncio
    async def test_calls_registry(
        self,
        mcp_server: AgentPlatformMCPServer,
        mock_registry: MagicMock,
    ):
        await mcp_server.handle_request("list_agents")
        mock_registry.list_agents.assert_awaited_once()


class TestGetAgentHandler:
    @pytest.mark.asyncio
    async def test_returns_agent_details(self, mcp_server: AgentPlatformMCPServer):
        result = await mcp_server.handle_request("get_agent", {"agent_id": "test-agent"})
        assert result["agent_id"] == "test-agent"
        assert result["tools"]["allow"] == ["search"]
        assert result["routing_strategy"] == "single"

    @pytest.mark.asyncio
    async def test_missing_agent_id_raises(self, mcp_server: AgentPlatformMCPServer):
        with pytest.raises(MCPToolError, match="Missing required parameter"):
            await mcp_server.handle_request("get_agent", {})

    @pytest.mark.asyncio
    async def test_agent_not_found_raises(
        self,
        mcp_server: AgentPlatformMCPServer,
        mock_registry: MagicMock,
    ):
        from agent_platform.registry.registry import AgentNotFoundError
        mock_registry.get = AsyncMock(side_effect=AgentNotFoundError("not found"))
        with pytest.raises(MCPToolError, match="not found"):
            await mcp_server.handle_request("get_agent", {"agent_id": "nope"})


class TestListDeploymentsHandler:
    @pytest.mark.asyncio
    async def test_returns_deployments(self, mcp_server: AgentPlatformMCPServer):
        result = await mcp_server.handle_request("list_deployments")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["deployment_id"] == "dep_test-agent_dev"
        assert result[0]["channel"] == "dev"


class TestListToolsHandler:
    @pytest.mark.asyncio
    async def test_returns_tools(self, mcp_server: AgentPlatformMCPServer):
        result = await mcp_server.handle_request("list_tools")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["name"] == "search"
        assert result[0]["risk_level"] == "low"


class TestRunEvalHandler:
    @pytest.mark.asyncio
    async def test_returns_report(self, mcp_server: AgentPlatformMCPServer):
        result = await mcp_server.handle_request("run_eval", {"agent_id": "test-agent"})
        assert result["gate_passed"] is True
        assert result["pass_rate"] == 1.0

    @pytest.mark.asyncio
    async def test_missing_agent_id_raises(self, mcp_server: AgentPlatformMCPServer):
        with pytest.raises(MCPToolError, match="Missing required parameter"):
            await mcp_server.handle_request("run_eval", {})


class TestDeploymentAuditHandler:
    @pytest.mark.asyncio
    async def test_returns_events(self, mcp_server: AgentPlatformMCPServer):
        result = await mcp_server.handle_request("deployment_audit")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["agent_id"] == "test-agent"
        assert result[0]["event_type"] == "deploy"

    @pytest.mark.asyncio
    async def test_passes_filters(
        self,
        mcp_server: AgentPlatformMCPServer,
        mock_audit_log: MagicMock,
    ):
        await mcp_server.handle_request(
            "deployment_audit",
            {"agent_id": "myj", "channel": "prod", "limit": 10},
        )
        mock_audit_log.list_events.assert_awaited_once_with(
            agent_id="myj", channel="prod", limit=10,
        )


# ===========================================================================
# Tests — unknown tool
# ===========================================================================


class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_raises_error(self, mcp_server: AgentPlatformMCPServer):
        with pytest.raises(MCPToolError, match="Unknown tool"):
            await mcp_server.handle_request("nonexistent_tool")

    @pytest.mark.asyncio
    async def test_unknown_tool_error_code(self, mcp_server: AgentPlatformMCPServer):
        with pytest.raises(MCPToolError) as exc_info:
            await mcp_server.handle_request("nonexistent_tool")
        assert exc_info.value.code == -32601


# ===========================================================================
# Tests — StdioTransport / JSON-RPC dispatch
# ===========================================================================


class TestStdioTransport:
    def _make_transport(
        self,
        mcp_server: AgentPlatformMCPServer,
        requests: list[dict[str, Any]],
    ) -> tuple[StdioTransport, io.StringIO]:
        input_lines = "\n".join(json.dumps(r) for r in requests) + "\n"
        input_stream = io.StringIO(input_lines)
        output_stream = io.StringIO()
        transport = StdioTransport(
            mcp_server,
            input_stream=input_stream,
            output_stream=output_stream,
        )
        return transport, output_stream

    def _read_responses(self, output: io.StringIO) -> list[dict[str, Any]]:
        output.seek(0)
        lines = [line.strip() for line in output.readlines() if line.strip()]
        return [json.loads(line) for line in lines]

    @pytest.mark.asyncio
    async def test_initialize(self, mcp_server: AgentPlatformMCPServer):
        transport, output = self._make_transport(mcp_server, [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        ])
        await transport.run()
        responses = self._read_responses(output)
        assert len(responses) == 1
        result = responses[0]["result"]
        assert "protocolVersion" in result
        assert result["serverInfo"]["name"] == "agent-platform"

    @pytest.mark.asyncio
    async def test_tools_list(self, mcp_server: AgentPlatformMCPServer):
        transport, output = self._make_transport(mcp_server, [
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ])
        await transport.run()
        responses = self._read_responses(output)
        tools = responses[0]["result"]["tools"]
        assert len(tools) == 6

    @pytest.mark.asyncio
    async def test_tools_call_success(self, mcp_server: AgentPlatformMCPServer):
        transport, output = self._make_transport(mcp_server, [
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "list_agents", "arguments": {}},
            },
        ])
        await transport.run()
        responses = self._read_responses(output)
        result = responses[0]["result"]
        assert result["isError"] is False
        content = json.loads(result["content"][0]["text"])
        assert isinstance(content, list)

    @pytest.mark.asyncio
    async def test_tools_call_unknown_tool(self, mcp_server: AgentPlatformMCPServer):
        transport, output = self._make_transport(mcp_server, [
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "nope", "arguments": {}},
            },
        ])
        await transport.run()
        responses = self._read_responses(output)
        assert "error" in responses[0]
        assert responses[0]["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_invalid_json(self, mcp_server: AgentPlatformMCPServer):
        input_stream = io.StringIO("this is not json\n")
        output_stream = io.StringIO()
        transport = StdioTransport(
            mcp_server,
            input_stream=input_stream,
            output_stream=output_stream,
        )
        await transport.run()
        responses = self._read_responses(output_stream)
        assert responses[0]["error"]["code"] == -32700

    @pytest.mark.asyncio
    async def test_missing_method(self, mcp_server: AgentPlatformMCPServer):
        transport, output = self._make_transport(mcp_server, [
            {"jsonrpc": "2.0", "id": 5},
        ])
        await transport.run()
        responses = self._read_responses(output)
        assert responses[0]["error"]["code"] == -32600

    @pytest.mark.asyncio
    async def test_unknown_method(self, mcp_server: AgentPlatformMCPServer):
        transport, output = self._make_transport(mcp_server, [
            {"jsonrpc": "2.0", "id": 6, "method": "some/unknown"},
        ])
        await transport.run()
        responses = self._read_responses(output)
        assert "error" in responses[0]
        assert responses[0]["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_notification_no_response(self, mcp_server: AgentPlatformMCPServer):
        """Notifications (no id) should not produce a response."""
        input_stream = io.StringIO(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        )
        output_stream = io.StringIO()
        transport = StdioTransport(
            mcp_server,
            input_stream=input_stream,
            output_stream=output_stream,
        )
        await transport.run()
        responses = self._read_responses(output_stream)
        assert len(responses) == 0

    @pytest.mark.asyncio
    async def test_multiple_requests(self, mcp_server: AgentPlatformMCPServer):
        transport, output = self._make_transport(mcp_server, [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ])
        await transport.run()
        responses = self._read_responses(output)
        assert len(responses) == 2
        assert responses[0]["id"] == 1
        assert responses[1]["id"] == 2
