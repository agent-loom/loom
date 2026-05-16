"""原生运行时后端，支持编排器模式和自定义适配器加载。"""

from __future__ import annotations

import importlib
import logging
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
from agent_platform.runtime.orchestrator import (
    DirectReplyWorker,
    HandoffWorker,
    ToolWorker,
    WorkerOrchestrator,
)
from agent_platform.tools import ToolExecutor, create_default_tool_registry, load_agent_tools

logger = logging.getLogger(__name__)


class NativeRuntimeBackend:
    """原生运行时后端，内置编排器和适配器加载机制。"""

    name = "native"

    def __init__(
        self, tool_executor: ToolExecutor | None = None,
    ):
        """初始化原生后端，可选注入工具执行器。"""
        self.tool_executor = tool_executor or ToolExecutor(
            create_default_tool_registry()
        )
        self._adapters: dict[str, Any] = {}
        self._orchestrators: dict[str, WorkerOrchestrator] = {}
        self._loaded_agents: set[str] = set()

    def _ensure_agent_tools(
        self, agent_spec: Any,
    ) -> None:
        """Load tools for the agent if not already loaded."""
        agent_id = agent_spec.agent_id
        if agent_id in self._loaded_agents:
            return
        package_path = agent_spec.package_path
        load_agent_tools(
            self.tool_executor.registry,
            package_path,
            agent_id,
        )
        self._loaded_agents.add(agent_id)

    async def run(
        self, request: RuntimeRequest,
    ) -> RuntimeResponse:
        """根据 Agent 入口模式分发请求到编排器或适配器。"""
        self._ensure_agent_tools(request.agent_spec)
        entry_mode = request.agent_spec.manifest.entry.mode

        if entry_mode == "orchestrator_workers":
            return await self._run_orchestrator(request)

        adapter = self._resolve_adapter(request.agent_spec)
        if adapter is not None:
            return await adapter.run(request)
        return self._generic_response(request)

    async def _run_orchestrator(self, request: RuntimeRequest) -> RuntimeResponse:
        agent_id = request.agent_spec.agent_id
        if agent_id not in self._orchestrators:
            default_worker = request.agent_spec.manifest.entry.default_worker
            orchestrator = WorkerOrchestrator(default_worker_name=default_worker)

            orchestrator.register(DirectReplyWorker())
            handoff_intents = (
                request.agent_spec.manifest.routing.human_handoff_intents
                or ["转人工"]
            )
            orchestrator.register(
                HandoffWorker(handoff_intents=handoff_intents),
            )

            for tool_name in request.agent_spec.manifest.tools.allow:
                try:
                    defn = self.tool_executor.registry.get(tool_name)
                    if defn.keywords:
                        orchestrator.register(ToolWorker(
                            name=tool_name,
                            tool_name=tool_name,
                            keywords=defn.keywords,
                            tool_executor=self.tool_executor,
                        ))
                except LookupError:
                    pass

            self._orchestrators[agent_id] = orchestrator
            logger.info(
                "Initialized WorkerOrchestrator for agent %s with default_worker=%s",
                agent_id,
                default_worker,
            )

        return await self._orchestrators[agent_id].route_and_run(request)

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
