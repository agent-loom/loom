"""MCP SSE (Server-Sent Events) 传输层实现。

通过 HTTP SSE 协议暴露 MCP 服务，支持：
- GET /mcp/sse — 建立 SSE 长连接，接收服务端推送的 JSON-RPC 2.0 响应
- POST /mcp — 接收 JSON-RPC 2.0 请求，将响应推送到对应 session 的 SSE 流

每个 SSE 连接分配唯一的 session ID，客户端在 POST 请求中通过
``X-MCP-Session-ID`` 头部指定目标 session。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

from agent_platform.mcp.server import AgentPlatformMCPServer, MCPToolError

logger = logging.getLogger(__name__)

# MCP 协议常量
MCP_PROTOCOL_VERSION = "2024-11-05"

SERVER_INFO = {
    "name": "agent-platform",
    "version": "0.1.0",
}

SERVER_CAPABILITIES = {
    "tools": {"listChanged": False},
}


def _make_response(req_id: Any, result: Any) -> dict[str, Any]:
    """构建 JSON-RPC 2.0 成功响应。"""
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _make_error(
    req_id: Any, code: int, message: str, data: Any = None,
) -> dict[str, Any]:
    """构建 JSON-RPC 2.0 错误响应。"""
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": error}


class MCPSSETransport:
    """基于 SSE 的 MCP 传输层。

    管理多个 SSE session，每个 session 对应一个活跃的 SSE 连接。
    客户端通过 POST /mcp 发送 JSON-RPC 请求，服务端通过对应
    session 的 SSE 流返回响应。
    """

    def __init__(self, server: AgentPlatformMCPServer) -> None:
        self.server = server
        # session_id -> asyncio.Queue，用于向 SSE 流推送消息
        self._sessions: dict[str, asyncio.Queue[dict[str, Any] | None]] = {}
        # 路由器，供外部挂载到 FastAPI 应用
        self.router = APIRouter()
        self._register_routes()

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    @property
    def active_sessions(self) -> list[str]:
        """返回当前活跃的 session ID 列表。"""
        return list(self._sessions.keys())

    def create_session(self) -> str:
        """创建新 session 并返回其 ID。"""
        session_id = uuid.uuid4().hex
        self._sessions[session_id] = asyncio.Queue()
        logger.info("SSE session 已创建: %s", session_id)
        return session_id

    def remove_session(self, session_id: str) -> None:
        """移除 session，清理资源。"""
        queue = self._sessions.pop(session_id, None)
        if queue is not None:
            # 发送 sentinel 通知 SSE 生成器停止
            queue.put_nowait(None)
            logger.info("SSE session 已移除: %s", session_id)

    async def push_to_session(
        self, session_id: str, message: dict[str, Any],
    ) -> bool:
        """向指定 session 推送消息。返回是否成功。"""
        queue = self._sessions.get(session_id)
        if queue is None:
            return False
        await queue.put(message)
        return True

    # ------------------------------------------------------------------
    # JSON-RPC 请求处理
    # ------------------------------------------------------------------

    async def handle_jsonrpc(
        self, body: dict[str, Any],
    ) -> dict[str, Any]:
        """处理单条 JSON-RPC 2.0 请求，返回响应字典。"""
        req_id = body.get("id")
        method = body.get("method")
        params = body.get("params", {})

        if method is None:
            return _make_error(req_id, -32600, "Invalid Request: missing method")

        try:
            result = await self._dispatch(method, params)
        except Exception as exc:
            code = getattr(exc, "code", -32603)
            return _make_error(req_id, code, str(exc))

        return _make_response(req_id, result)

    async def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        """根据方法名分发请求到对应处理器。"""
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
    # MCP 方法处理器
    # ------------------------------------------------------------------

    def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        """处理 initialize 请求。"""
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "serverInfo": SERVER_INFO,
            "capabilities": SERVER_CAPABILITIES,
        }

    def _handle_tools_list(self) -> dict[str, Any]:
        """处理 tools/list 请求。"""
        return {"tools": self.server.list_tools()}

    async def _handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        """处理 tools/call 请求。"""
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
            logger.exception("工具调用失败: %s", tool_name)
            return {
                "content": [
                    {"type": "text", "text": str(exc)},
                ],
                "isError": True,
            }

    # ------------------------------------------------------------------
    # SSE 事件生成器
    # ------------------------------------------------------------------

    async def _sse_event_generator(
        self, session_id: str,
    ):
        """为指定 session 生成 SSE 事件流。"""
        queue = self._sessions.get(session_id)
        if queue is None:
            return

        # 发送连接建立事件，携带 session_id
        yield self._format_sse_event(
            event="connected",
            data=json.dumps({"session_id": session_id}),
        )

        try:
            while True:
                message = await queue.get()
                if message is None:
                    # sentinel：session 被关闭
                    break
                yield self._format_sse_event(
                    event="message",
                    data=json.dumps(message, default=str),
                )
        except asyncio.CancelledError:
            # 客户端断开连接
            logger.info("SSE 连接被取消: session=%s", session_id)
        finally:
            # 清理 session（如果还存在）
            self._sessions.pop(session_id, None)
            logger.info("SSE 连接结束: session=%s", session_id)

    @staticmethod
    def _format_sse_event(event: str, data: str) -> str:
        """格式化一条 SSE 事件。"""
        return f"event: {event}\ndata: {data}\n\n"

    # ------------------------------------------------------------------
    # 路由注册
    # ------------------------------------------------------------------

    def _register_routes(self) -> None:
        """注册 GET /mcp/sse 和 POST /mcp 路由。"""

        @self.router.get("/mcp/sse")
        async def sse_connect(
            request: Request,
        ) -> StreamingResponse:
            """建立 SSE 连接，分配唯一 session ID。"""
            session_id = self.create_session()
            return StreamingResponse(
                self._sse_event_generator(session_id),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                    "X-MCP-Session-ID": session_id,
                },
            )

        @self.router.post("/mcp")
        async def mcp_request(
            request: Request,
            x_mcp_session_id: str | None = Header(default=None),
        ) -> JSONResponse:
            """接收 JSON-RPC 2.0 请求并处理。

            如果提供了 X-MCP-Session-ID，响应会同时推送到对应的 SSE 流。
            无论是否有 SSE 连接，都会在 HTTP 响应中直接返回结果。
            """
            # 解析请求体
            try:
                body = await request.json()
            except Exception:
                error_resp = _make_error(None, -32700, "Parse error")
                return JSONResponse(content=error_resp, status_code=200)

            # 处理 JSON-RPC 请求
            response = await self.handle_jsonrpc(body)

            # 如果有关联的 SSE session，推送响应
            if x_mcp_session_id:
                pushed = await self.push_to_session(x_mcp_session_id, response)
                if not pushed:
                    logger.warning(
                        "无法推送到 session %s（session 不存在或已关闭）",
                        x_mcp_session_id,
                    )

            return JSONResponse(content=response, status_code=200)
