from __future__ import annotations

import logging

from agent_platform.domain.models import (
    AgentIdentity,
    AgentOutput,
    AgentResponse,
    ResponseText,
    ResponseTrace,
    RuntimeRequest,
    RuntimeResponse,
)

logger = logging.getLogger(__name__)


class LangGraphRuntimeBackend:
    """LangGraph-based runtime backend.

    Supports the `graph` entry mode from manifest. Currently returns a
    placeholder response — real implementation will build a LangGraph
    StateGraph from the agent's graph definition.
    """

    name = "langgraph"

    async def run(self, request: RuntimeRequest) -> RuntimeResponse:
        agent = request.agent_spec
        query = request.request.input.query

        graph_config = agent.manifest.extensions.get("langgraph", {})
        logger.info(
            "LangGraph execution for %s (graph_config=%s)",
            agent.agent_id,
            graph_config,
        )

        display = f"[LangGraph] Agent {agent.agent_id} received: {query}"

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
            ),
            debug={"runtime_backend": "langgraph", "graph_config": graph_config},
        )
        return RuntimeResponse(response=response)
