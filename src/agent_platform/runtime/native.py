from __future__ import annotations

import importlib
from typing import Any

from agent_platform.domain.models import (
    AgentIdentity,
    AgentOutput,
    AgentResponse,
    AgentSpec,
    ResponseText,
    ResponseTrace,
    RuntimeRequest,
    RuntimeResponse,
)
from agent_platform.tools import ToolExecutor, create_default_tool_registry


class NativeRuntimeBackend:
    name = "native"

    def __init__(self, tool_executor: ToolExecutor | None = None):
        self.tool_executor = tool_executor or ToolExecutor(create_default_tool_registry())
        self._adapters: dict[str, Any] = {}

    async def run(self, request: RuntimeRequest) -> RuntimeResponse:
        adapter = self._resolve_adapter(request.agent_spec)
        if adapter is not None:
            return await adapter.run(request)
        return self._generic_response(request)

    def _resolve_adapter(self, spec: AgentSpec):
        agent_id = spec.agent_id
        if agent_id in self._adapters:
            return self._adapters[agent_id]

        entrypoint = spec.manifest.runtime.entrypoint
        if entrypoint:
            adapter = self._load_adapter(entrypoint)
            self._adapters[agent_id] = adapter
            return adapter

        self._adapters[agent_id] = None
        return None

    def _load_adapter(self, entrypoint: str):
        module_path, _, class_name = entrypoint.rpartition(":")
        if not module_path or not class_name:
            return None
        try:
            module = importlib.import_module(module_path)
            adapter_cls = getattr(module, class_name)
            return adapter_cls(tool_executor=self.tool_executor)
        except (ImportError, AttributeError):
            return None

    @staticmethod
    def _generic_response(request: RuntimeRequest) -> RuntimeResponse:
        agent = request.agent_spec
        query = request.request.input.query
        display = f"Agent {agent.agent_id} 已收到：{query}"
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
            trace=ResponseTrace(route_reason=request.route_reason),
        )
        return RuntimeResponse(response=response)
