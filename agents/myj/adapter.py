from __future__ import annotations

from typing import Any

from agent_platform.domain.models import (
    AgentIdentity,
    AgentOutput,
    AgentResponse,
    ResponseText,
    ResponseTrace,
    RuntimeRequest,
    RuntimeResponse,
)
from agent_platform.tools import ToolExecutor, create_default_tool_registry


class MyjAdapter:
    """MYJ demo Agent adapter for NativeRuntimeBackend."""

    def __init__(self, tool_executor: ToolExecutor | None = None):
        self.tool_executor = tool_executor or ToolExecutor(create_default_tool_registry())

    async def run(self, request: RuntimeRequest) -> RuntimeResponse:
        agent = request.agent_spec
        user_query = request.request.input.query
        tool_result = await self._maybe_execute_tool(request)
        tool_output = tool_result.output if tool_result else None
        display = self._build_response(user_query, tool_output)
        tool_calls = [tool_result.trace] if tool_result else []
        response = AgentResponse(
            request_id=request.request.request_id,
            session_id=request.request.session_id,
            agent=AgentIdentity(
                agent_id=agent.agent_id,
                agent_version=agent.version,
                deployment_id=request.deployment_id,
            ),
            output=AgentOutput(text=ResponseText(display=display, tts=display)),
            debug={"runtime_backend": "native"} if request.request.options.debug else None,
            trace=ResponseTrace(
                route_reason=request.route_reason,
                model=self._model_name(agent),
                tool_calls=tool_calls,
            ),
        )
        return RuntimeResponse(response=response)

    async def _maybe_execute_tool(self, request: RuntimeRequest):
        query = request.request.input.query
        tool_name = self._select_tool(query)
        if tool_name is None:
            return None
        return await self.tool_executor.execute(
            tool_name,
            {"query": query, "context": request.request.context.model_dump()},
            allowed_tools=request.agent_spec.manifest.tools.allow,
        )

    @staticmethod
    def _select_tool(query: str) -> str | None:
        if any(kw in query for kw in ["哪里", "在哪", "位置", "货架", "可乐"]):
            return "myj.goods_location"
        if any(kw in query for kw in ["推荐", "商品", "饮料", "低糖"]):
            return "myj.goods_search"
        return None

    @staticmethod
    def _build_response(query: str, tool_output: dict[str, Any] | None = None) -> str:
        if tool_output and tool_output.get("summary"):
            return f"MYJ demo agent 已收到：{query}。{tool_output['summary']}"
        return f"MYJ demo agent 已收到：{query}"

    @staticmethod
    def _model_name(agent) -> str | None:
        default_model = agent.manifest.models.get("default")
        return default_model.model if default_model else None
