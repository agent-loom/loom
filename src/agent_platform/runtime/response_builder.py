from __future__ import annotations

from typing import Any

from agent_platform.domain.models import (
    AgentIdentity,
    AgentOutput,
    AgentResponse,
    OutputStatus,
    ResponseCard,
    ResponseCommand,
    ResponseText,
    ResponseTrace,
    RuntimeRequest,
)


class ResponseBuilder:
    """Builds protocol-compliant AgentResponse from runtime execution results."""

    def build(
        self,
        request: RuntimeRequest,
        *,
        display: str,
        tts: str | None = None,
        status: OutputStatus = OutputStatus.COMPLETED,
        cards: list[dict[str, Any]] | None = None,
        commands: list[dict[str, Any]] | None = None,
        trace: ResponseTrace | None = None,
        debug: dict[str, Any] | None = None,
    ) -> AgentResponse:
        agent = request.agent_spec

        # Filter commands against allowlist
        filtered_commands = self._filter_commands(
            commands or [],
            agent.manifest.output.command_allowlist,
        )

        return AgentResponse(
            request_id=request.request.request_id,
            session_id=request.request.session_id,
            agent=AgentIdentity(
                agent_id=agent.agent_id,
                agent_version=agent.version,
                deployment_id=request.deployment_id,
            ),
            output=AgentOutput(
                status=status,
                text=ResponseText(display=display, tts=tts or display),
                cards=[ResponseCard(**c) for c in (cards or [])],
                commands=[ResponseCommand(**c) for c in filtered_commands],
            ),
            trace=trace or ResponseTrace(route_reason=request.route_reason),
            debug=debug if request.request.options.debug else None,
        )

    def build_handoff(self, request: RuntimeRequest, reason: str) -> AgentResponse:
        return self.build(
            request,
            display=reason,
            status=OutputStatus.HANDOFF_REQUIRED,
            commands=[{"name": "human.handoff", "data": {"reason": reason}}],
        )

    def build_clarification(self, request: RuntimeRequest, question: str) -> AgentResponse:
        return self.build(
            request,
            display=question,
            status=OutputStatus.CLARIFICATION_REQUIRED,
        )

    def _filter_commands(
        self,
        commands: list[dict[str, Any]],
        allowlist: list[str],
    ) -> list[dict[str, Any]]:
        if not allowlist:
            return commands
        return [c for c in commands if c.get("name") in allowlist]
