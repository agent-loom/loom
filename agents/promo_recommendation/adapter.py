from __future__ import annotations

from agent_platform.domain.models import (
    AgentIdentity,
    AgentOutput,
    AgentResponse,
    ResponseText,
    ResponseTrace,
    RuntimeRequest,
    RuntimeResponse,
)
from agent_platform.tools import ToolExecutor


class PromoRecommendationAdapter:
    def __init__(self, tool_executor: ToolExecutor | None = None):
        self.tool_executor = tool_executor

    async def run(self, request: RuntimeRequest) -> RuntimeResponse:
        agent = request.agent_spec
        query = request.request.input.query

        tool_traces = []
        display = f"促销推荐 Agent 已收到：{query}"

        if self.tool_executor:
            allowed = agent.manifest.tools.allow
            if any(kw in query for kw in ["优惠", "促销", "打折", "活动", "推荐"]):
                result = await self.tool_executor.execute(
                    "promo.promotion_search",
                    {"query": query},
                    allowed_tools=allowed,
                    timeout_ms=agent.manifest.tools.timeout_ms,
                )
                tool_traces.append(result.trace)
                if result.trace.status == "success":
                    summary = result.output.get("summary", "")
                    display = summary or display

        response = AgentResponse(
            request_id=request.request.request_id,
            session_id=request.request.session_id,
            agent=AgentIdentity(
                agent_id=agent.agent_id,
                agent_version=agent.version,
                deployment_id=request.deployment_id,
            ),
            output=AgentOutput(
                text=ResponseText(display=display, tts=display),
            ),
            trace=ResponseTrace(
                route_reason=request.route_reason,
                tool_calls=tool_traces,
            ),
            debug={"runtime_backend": "native"},
        )
        return RuntimeResponse(response=response)
