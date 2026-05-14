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


class EchoAdapter:
    """Echoes user input back — validates that a new agent works without core changes."""

    def __init__(self, **_kwargs):
        pass

    async def run(self, request: RuntimeRequest) -> RuntimeResponse:
        agent = request.agent_spec
        query = request.request.input.query
        display = f"Echo: {query}"
        response = AgentResponse(
            request_id=request.request.request_id,
            session_id=request.request.session_id,
            agent=AgentIdentity(
                agent_id=agent.agent_id,
                agent_version=agent.version,
                deployment_id=request.deployment_id,
            ),
            output=AgentOutput(text=ResponseText(display=display, tts=display)),
            trace=ResponseTrace(route_reason=request.route_reason),
        )
        return RuntimeResponse(response=response)
