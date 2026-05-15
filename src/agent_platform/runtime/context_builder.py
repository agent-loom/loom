from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from agent_platform.domain.models import AgentRequest, AgentSpec, SessionMessage

logger = logging.getLogger(__name__)

class RuntimeContext(BaseModel):
    system_prompt: str = ""
    messages: list[dict[str, str]] = Field(default_factory=list)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    knowledge_snippets: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

class ContextBuilder:
    """Assembles RuntimeContext from request, session, knowledge, and agent config."""

    def build(
        self,
        spec: AgentSpec,
        request: AgentRequest,
        session_history: list[SessionMessage] | None = None,
        knowledge_results: list[str] | None = None,
    ) -> RuntimeContext:
        # 1. Load system prompt from manifest prompts
        system_prompt = self._load_system_prompt(spec)

        # 2. Build message history from session
        messages = self._build_messages(session_history or [], request, spec)

        # 3. Build tool definitions from manifest
        tools = self._build_tool_defs(spec)

        # 4. Include knowledge snippets
        snippets = knowledge_results or []

        # 5. Build context metadata (tenant, store, channel, etc.)
        metadata = self._build_metadata(request, spec)

        return RuntimeContext(
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
            knowledge_snippets=snippets,
            metadata=metadata,
        )

    def _load_system_prompt(self, spec: AgentSpec) -> str:
        """Load orchestrator prompt from the agent package."""
        orchestrator_path = spec.manifest.prompts.get("orchestrator")
        if not orchestrator_path:
            return f"You are agent {spec.agent_id}."

        full_path = spec.package_path / orchestrator_path
        try:
            return full_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning("prompt file not found: %s", full_path)
            return f"You are agent {spec.agent_id}."

    def _build_messages(
        self,
        history: list[SessionMessage],
        request: AgentRequest,
        spec: AgentSpec,
    ) -> list[dict[str, str]]:
        window = spec.manifest.session.history_window
        recent = history[-window:] if window > 0 else []
        messages = [{"role": m.role, "content": m.content} for m in recent]
        messages.append({"role": "user", "content": request.input.query})
        return messages

    def _build_tool_defs(self, spec: AgentSpec) -> list[dict[str, Any]]:
        allowed = spec.manifest.tools.allow
        return [{"name": t, "type": "function"} for t in allowed]

    def _build_metadata(self, request: AgentRequest, spec: AgentSpec) -> dict[str, Any]:
        return {
            "agent_id": spec.agent_id,
            "tenant_id": request.context.tenant.tenant_id,
            "org_id": request.context.tenant.org_id,
            "location_id": request.context.location.location_id,
            "channel_id": request.context.channel.channel_id,
            "user_id": request.context.user.user_id,
            "locale": request.context.locale,
            "timezone": request.context.timezone,
            "session_scope": spec.manifest.session.scope,
        }
