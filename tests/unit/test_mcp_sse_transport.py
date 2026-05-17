"""MCP SSE 传输层单元测试。

测试 MCPSSETransport 的 SSE 连接建立、JSON-RPC 请求处理、
错误处理和 session 管理。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

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
)
from agent_platform.mcp.sse_transport import MCPSSETransport
from agent_platform.registry.deployment import DeploymentEvent

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _make_manifest(
    agent_id: str = "test-agent",
    name: str = "Test Agent",
    version: str = "1.0.0",
) -> AgentManifest:
    return AgentManifest(
        api_version="agent.platform/v1",
        kind="AgentPackage",
        metadata=ManifestMetadata(id=agent_id, name=name, description="测试 agent"),
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
    tool.description = "搜索工具"
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


@pytest.fixture
def sse_transport(mcp_server: AgentPlatformMCPServer) -> MCPSSETransport:
    return MCPSSETransport(mcp_server)


@pytest.fixture
def test_app(sse_transport: MCPSSETransport) -> FastAPI:
    """创建包含 SSE 传输路由的测试 FastAPI 应用。"""
    app = FastAPI()
    app.include_router(sse_transport.router)
    return app


@pytest_asyncio.fixture
async def client(test_app: FastAPI):
    """创建异步测试客户端。"""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


# ===========================================================================
# 测试 — SSE 连接建立
# ===========================================================================


class TestSSEConnection:
    """测试 SSE 事件生成器（直接测试，不走 HTTP 层以避免 ASGI 事件循环死锁）。"""

    @pytest.mark.asyncio
    async def test_sse_generator_yields_connected_event(
        self, sse_transport: MCPSSETransport,
    ):
        """SSE 生成器的第一条消息应为 connected 事件。"""
        session_id = sse_transport.create_session()
        gen = sse_transport._sse_event_generator(session_id)

        # 第一条应该是 connected 事件
        first_event = await gen.__anext__()
        assert "event: connected" in first_event
        assert "session_id" in first_event

        # 解析 data 字段
        for line in first_event.strip().split("\n"):
            if line.startswith("data: "):
                payload = json.loads(line[len("data: "):])
                assert payload["session_id"] == session_id
                break

        # 关闭生成器
        sse_transport.remove_session(session_id)

    @pytest.mark.asyncio
    async def test_sse_generator_delivers_pushed_message(
        self, sse_transport: MCPSSETransport,
    ):
        """推送的消息应通过 SSE 生成器传递。"""
        session_id = sse_transport.create_session()
        gen = sse_transport._sse_event_generator(session_id)

        # 消费 connected 事件
        await gen.__anext__()

        # 推送一条消息
        msg = {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}
        await sse_transport.push_to_session(session_id, msg)

        # 应收到该消息
        event = await gen.__anext__()
        assert "event: message" in event
        assert '"tools"' in event

        sse_transport.remove_session(session_id)

    @pytest.mark.asyncio
    async def test_sse_generator_stops_on_session_removal(
        self, sse_transport: MCPSSETransport,
    ):
        """移除 session 后生成器应终止。"""
        session_id = sse_transport.create_session()
        gen = sse_transport._sse_event_generator(session_id)

        await gen.__anext__()  # connected
        sse_transport.remove_session(session_id)

        # 生成器应在收到 None sentinel 后终止
        events = []
        async for event in gen:
            events.append(event)
        assert events == []


# ===========================================================================
# 测试 — JSON-RPC tools/list 请求和响应
# ===========================================================================


class TestToolsList:
    """测试 tools/list JSON-RPC 方法。"""

    @pytest.mark.asyncio
    async def test_tools_list_returns_all_tools(self, client: AsyncClient):
        """POST /mcp 发送 tools/list 应返回 6 个工具。"""
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        }
        resp = await client.post("/mcp", json=body)
        assert resp.status_code == 200
        result = resp.json()
        assert "result" in result
        tools = result["result"]["tools"]
        assert len(tools) == 6

    @pytest.mark.asyncio
    async def test_tools_list_tool_names(self, client: AsyncClient):
        """tools/list 返回的工具名称应完整。"""
        body = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }
        resp = await client.post("/mcp", json=body)
        result = resp.json()
        names = {t["name"] for t in result["result"]["tools"]}
        expected = {
            "list_agents",
            "get_agent",
            "list_deployments",
            "list_tools",
            "run_eval",
            "deployment_audit",
        }
        assert names == expected


# ===========================================================================
# 测试 — JSON-RPC tools/call 请求和响应
# ===========================================================================


class TestToolsCall:
    """测试 tools/call JSON-RPC 方法。"""

    @pytest.mark.asyncio
    async def test_tools_call_list_agents(self, client: AsyncClient):
        """调用 list_agents 工具应返回 agent 列表。"""
        body = {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {"name": "list_agents", "arguments": {}},
        }
        resp = await client.post("/mcp", json=body)
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["isError"] is False
        content = json.loads(result["content"][0]["text"])
        assert isinstance(content, list)
        assert content[0]["agent_id"] == "test-agent"

    @pytest.mark.asyncio
    async def test_tools_call_get_agent(self, client: AsyncClient):
        """调用 get_agent 工具应返回 agent 详情。"""
        body = {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": "get_agent", "arguments": {"agent_id": "test-agent"}},
        }
        resp = await client.post("/mcp", json=body)
        result = resp.json()["result"]
        assert result["isError"] is False
        content = json.loads(result["content"][0]["text"])
        assert content["agent_id"] == "test-agent"
        assert content["routing_strategy"] == "single"

    @pytest.mark.asyncio
    async def test_tools_call_response_has_jsonrpc_fields(self, client: AsyncClient):
        """响应应包含标准 JSON-RPC 2.0 字段。"""
        body = {
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {"name": "list_agents", "arguments": {}},
        }
        resp = await client.post("/mcp", json=body)
        data = resp.json()
        assert data["jsonrpc"] == "2.0"
        assert data["id"] == 12
        assert "result" in data


# ===========================================================================
# 测试 — 错误处理
# ===========================================================================


class TestErrorHandling:
    """测试各种错误场景。"""

    @pytest.mark.asyncio
    async def test_unknown_method(self, client: AsyncClient):
        """调用未知方法应返回 -32601 错误。"""
        body = {
            "jsonrpc": "2.0",
            "id": 20,
            "method": "unknown/method",
            "params": {},
        }
        resp = await client.post("/mcp", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_missing_method(self, client: AsyncClient):
        """请求中缺少 method 字段应返回 -32600 错误。"""
        body = {
            "jsonrpc": "2.0",
            "id": 21,
        }
        resp = await client.post("/mcp", json=body)
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == -32600
        assert "missing method" in data["error"]["message"].lower()

    @pytest.mark.asyncio
    async def test_tools_call_missing_name(self, client: AsyncClient):
        """tools/call 缺少 name 参数应返回错误。"""
        body = {
            "jsonrpc": "2.0",
            "id": 22,
            "method": "tools/call",
            "params": {"arguments": {}},
        }
        resp = await client.post("/mcp", json=body)
        data = resp.json()
        assert "error" in data
        assert "Missing required parameter" in data["error"]["message"]

    @pytest.mark.asyncio
    async def test_invalid_json_body(self, client: AsyncClient):
        """发送无效 JSON 应返回 -32700 解析错误。"""
        resp = await client.post(
            "/mcp",
            content=b"this is not json",
            headers={"content-type": "application/json"},
        )
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == -32700

    @pytest.mark.asyncio
    async def test_tools_call_unknown_tool(self, client: AsyncClient):
        """调用不存在的工具应返回错误。"""
        body = {
            "jsonrpc": "2.0",
            "id": 23,
            "method": "tools/call",
            "params": {"name": "nonexistent_tool", "arguments": {}},
        }
        resp = await client.post("/mcp", json=body)
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == -32601


# ===========================================================================
# 测试 — Session 管理
# ===========================================================================


class TestSessionManagement:
    """测试 session 生命周期管理。"""

    def test_create_session(self, sse_transport: MCPSSETransport):
        """create_session 应创建新 session 并返回 ID。"""
        session_id = sse_transport.create_session()
        assert session_id in sse_transport.active_sessions

    def test_create_multiple_sessions(self, sse_transport: MCPSSETransport):
        """应支持同时创建多个 session。"""
        s1 = sse_transport.create_session()
        s2 = sse_transport.create_session()
        assert s1 != s2
        assert len(sse_transport.active_sessions) == 2

    def test_remove_session(self, sse_transport: MCPSSETransport):
        """remove_session 应移除 session。"""
        session_id = sse_transport.create_session()
        sse_transport.remove_session(session_id)
        assert session_id not in sse_transport.active_sessions

    def test_remove_nonexistent_session(self, sse_transport: MCPSSETransport):
        """移除不存在的 session 不应报错。"""
        # 不应抛出异常
        sse_transport.remove_session("nonexistent-id")

    @pytest.mark.asyncio
    async def test_push_to_session(self, sse_transport: MCPSSETransport):
        """push_to_session 应成功推送消息到存在的 session。"""
        session_id = sse_transport.create_session()
        message = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        result = await sse_transport.push_to_session(session_id, message)
        assert result is True

    @pytest.mark.asyncio
    async def test_push_to_nonexistent_session(self, sse_transport: MCPSSETransport):
        """push_to_session 推送到不存在的 session 应返回 False。"""
        message = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        result = await sse_transport.push_to_session("nonexistent", message)
        assert result is False

    @pytest.mark.asyncio
    async def test_post_with_session_id_pushes_to_sse(
        self,
        client: AsyncClient,
        sse_transport: MCPSSETransport,
    ):
        """POST /mcp 携带 X-MCP-Session-ID 时应推送响应到 SSE 流。"""
        session_id = sse_transport.create_session()

        body = {
            "jsonrpc": "2.0",
            "id": 100,
            "method": "tools/list",
            "params": {},
        }
        resp = await client.post(
            "/mcp",
            json=body,
            headers={"X-MCP-Session-ID": session_id},
        )
        assert resp.status_code == 200

        # 验证消息已被推送到 queue
        queue = sse_transport._sessions.get(session_id)
        assert queue is not None
        pushed_msg = queue.get_nowait()
        assert pushed_msg["id"] == 100
        assert "result" in pushed_msg


# ===========================================================================
# 测试 — initialize 方法
# ===========================================================================


class TestInitialize:
    """测试 initialize JSON-RPC 方法。"""

    @pytest.mark.asyncio
    async def test_initialize_returns_protocol_version(self, client: AsyncClient):
        """initialize 应返回协议版本。"""
        body = {
            "jsonrpc": "2.0",
            "id": 30,
            "method": "initialize",
            "params": {},
        }
        resp = await client.post("/mcp", json=body)
        result = resp.json()["result"]
        assert "protocolVersion" in result
        assert result["protocolVersion"] == "2024-11-05"

    @pytest.mark.asyncio
    async def test_initialize_returns_server_info(self, client: AsyncClient):
        """initialize 应返回服务器信息。"""
        body = {
            "jsonrpc": "2.0",
            "id": 31,
            "method": "initialize",
            "params": {},
        }
        resp = await client.post("/mcp", json=body)
        result = resp.json()["result"]
        assert result["serverInfo"]["name"] == "agent-platform"
        assert result["serverInfo"]["version"] == "0.1.0"

    @pytest.mark.asyncio
    async def test_initialize_returns_capabilities(self, client: AsyncClient):
        """initialize 应返回服务器能力声明。"""
        body = {
            "jsonrpc": "2.0",
            "id": 32,
            "method": "initialize",
            "params": {},
        }
        resp = await client.post("/mcp", json=body)
        result = resp.json()["result"]
        assert "capabilities" in result
        assert "tools" in result["capabilities"]
