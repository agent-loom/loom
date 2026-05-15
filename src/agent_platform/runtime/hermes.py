from __future__ import annotations

import logging
from typing import Any

from agent_platform.domain.models import (
    AgentError,
    AgentIdentity,
    AgentManifest,
    AgentOutput,
    AgentResponse,
    AgentSpec,
    HermesExtension,
    OutputStatus,
    ResponseText,
    ResponseTrace,
    RuntimeRequest,
    RuntimeResponse,
    ToolCallTrace,
)
from agent_platform.runtime.model_gateway import ModelMessage

logger = logging.getLogger(__name__)


class ManifestMapper:
    @staticmethod
    def to_hermes_config(spec: AgentSpec) -> dict[str, Any]:
        manifest = spec.manifest
        hermes_ext = ManifestMapper._get_hermes_extension(manifest)
        return {
            "agent_id": spec.agent_id,
            "system_prompt": ManifestMapper._load_system_prompt(spec),
            "tools": manifest.tools.allow,
            "enabled_toolsets": hermes_ext.enabled_toolsets,
            "disabled_toolsets": hermes_ext.disabled_toolsets,
            "max_iterations": hermes_ext.max_iterations,
            "memory_provider": hermes_ext.memory_provider,
            "model": ManifestMapper._get_model_config(manifest),
        }

    @staticmethod
    def _get_hermes_extension(manifest: AgentManifest) -> HermesExtension:
        raw = manifest.extensions.get("hermes", {})
        if isinstance(raw, dict):
            return HermesExtension.model_validate(raw)
        return HermesExtension()

    @staticmethod
    def _load_system_prompt(spec: AgentSpec) -> str:
        prompt_ref = spec.manifest.prompts.get("orchestrator")
        if prompt_ref:
            prompt_path = spec.package_path / prompt_ref
            if prompt_path.exists():
                return prompt_path.read_text()
        return f"You are {spec.agent_id} agent."

    @staticmethod
    def _get_model_config(manifest: AgentManifest) -> dict[str, Any]:
        default = manifest.models.get("default")
        if default:
            return default.model_dump()
        return {"provider": "demo", "model": "native-demo"}


class ToolBridge:
    @staticmethod
    def wrap_platform_tools(tool_names: list[str], tool_executor) -> list[dict[str, Any]]:
        tools = []
        for name in tool_names:
            try:
                defn = tool_executor.registry.get(name)
                tools.append({
                    "name": defn.name,
                    "description": defn.description,
                    "input_schema": defn.input_schema,
                })
            except LookupError:
                logger.warning("Tool not found for Hermes bridge: %s", name)
        return tools


class SessionBridge:
    @staticmethod
    def map_session(session_id: str | None, hermes_config: dict) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "memory_provider": hermes_config.get("memory_provider", "session"),
        }


class ResponseMapper:
    @staticmethod
    def to_platform_response(
        hermes_result: dict[str, Any],
        request: RuntimeRequest,
    ) -> RuntimeResponse:
        display = hermes_result.get("text", "Hermes response")
        tool_calls = [
            ToolCallTrace(
                tool_name=tc.get("name", ""),
                status=tc.get("status", "success"),
                latency_ms=tc.get("latency_ms"),
            )
            for tc in hermes_result.get("tool_calls", [])
        ]
        response = AgentResponse(
            request_id=request.request.request_id,
            session_id=request.request.session_id,
            agent=AgentIdentity(
                agent_id=request.agent_spec.agent_id,
                agent_version=request.agent_spec.version,
                deployment_id=request.deployment_id,
            ),
            output=AgentOutput(
                text=ResponseText(display=display, tts=display),
            ),
            trace=ResponseTrace(
                route_reason=request.route_reason,
                tool_calls=tool_calls,
            ),
            debug={"runtime_backend": "hermes"},
        )
        return RuntimeResponse(response=response)


class TraceBridge:
    @staticmethod
    def extract_trace(hermes_result: dict[str, Any]) -> dict[str, Any]:
        return {
            "hermes_run_id": hermes_result.get("run_id"),
            "iterations": hermes_result.get("iterations", 0),
            "model_calls": hermes_result.get("model_calls", 0),
        }


class PolicyEnforcer:
    @staticmethod
    def check_pre_run(spec: AgentSpec) -> list[str]:
        violations: list[str] = []
        denied = set(spec.manifest.tools.deny)
        allowed = set(spec.manifest.tools.allow)
        overlap = denied & allowed
        if overlap:
            violations.append(f"tools in both allow and deny: {overlap}")
        return violations


class ConversationEngine:
    """Lightweight conversation engine that calls a model gateway and executes tools.

    When *model_gateway* is ``None`` the engine falls back to a canned stub
    response so the rest of the pipeline can still be exercised without a live
    LLM connection.
    """

    def __init__(
        self,
        model_gateway: Any | None = None,
        tool_executor: Any | None = None,
    ):
        self.model_gateway = model_gateway
        self.tool_executor = tool_executor

    async def converse(
        self,
        system_prompt: str,
        user_query: str,
        *,
        model_config: dict[str, Any],
        tools: list[dict[str, Any]],
        max_iterations: int = 4,
        session_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run a multi-turn tool-use loop and return a result dict.

        Returns a dict with keys: text, tool_calls, iterations,
        model_calls.
        """
        if self.model_gateway is None:
            return self._stub_response(
                system_prompt, user_query, model_config,
            )

        messages: list[ModelMessage] = [
            ModelMessage(role="system", content=system_prompt),
            ModelMessage(role="user", content=user_query),
        ]
        provider = model_config.get("provider", "stub")
        tool_call_traces: list[dict[str, Any]] = []
        total_model_calls = 0

        for iteration in range(max_iterations):
            total_model_calls += 1
            model_response = await self.model_gateway.chat(
                provider,
                messages,
                model=model_config.get("model", "native-demo"),
                temperature=model_config.get(
                    "temperature", 0.2,
                ),
                max_tokens=model_config.get(
                    "max_tokens", 1024,
                ),
                tools=tools or None,
            )

            # If the model did not request tool calls, done.
            if not model_response.tool_calls:
                return {
                    "text": model_response.content,
                    "tool_calls": tool_call_traces,
                    "iterations": iteration + 1,
                    "model_calls": total_model_calls,
                }

            # Execute each requested tool, feed results back.
            for tc in model_response.tool_calls:
                tool_name = tc.name
                tool_input = tc.arguments
                if self.tool_executor:
                    tool_result = (
                        await self.tool_executor.execute(
                            tool_name,
                            tool_input,
                            allowed_tools=[
                                t["name"] for t in tools
                            ],
                            timeout_ms=3000,
                        )
                    )
                    tool_output = tool_result.output
                    tool_call_traces.append({
                        "name": tool_name,
                        "status": tool_result.trace.status,
                        "latency_ms": (
                            tool_result.trace.latency_ms
                        ),
                    })
                else:
                    tool_output = {
                        "result": (
                            f"[stub] {tool_name} not executed"
                        ),
                    }
                    tool_call_traces.append({
                        "name": tool_name,
                        "status": "skipped",
                    })

                messages.append(
                    ModelMessage(
                        role="tool",
                        content=str(tool_output),
                    ),
                )

        # Exhausted iterations -- final call for closing answer.
        total_model_calls += 1
        final = await self.model_gateway.chat(
            provider,
            messages,
            model=model_config.get("model", "native-demo"),
            temperature=model_config.get("temperature", 0.2),
            max_tokens=model_config.get("max_tokens", 1024),
        )
        return {
            "text": final.content,
            "tool_calls": tool_call_traces,
            "iterations": max_iterations,
            "model_calls": total_model_calls,
        }

    @staticmethod
    def _stub_response(
        system_prompt: str, user_query: str, model_config: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "text": f"[Hermes-stub] Received: {user_query}",
            "tool_calls": [],
            "run_id": None,
            "iterations": 0,
            "model_calls": 0,
        }


class HermesRuntimeBackend:
    name = "hermes"

    def __init__(
        self,
        model_gateway: Any | None = None,
        tool_executor: Any | None = None,
    ):
        self.manifest_mapper = ManifestMapper()
        self.tool_bridge = ToolBridge()
        self.session_bridge = SessionBridge()
        self.response_mapper = ResponseMapper()
        self.trace_bridge = TraceBridge()
        self.policy_enforcer = PolicyEnforcer()
        self.model_gateway = model_gateway
        self.tool_executor = tool_executor
        self.conversation_engine = ConversationEngine(
            model_gateway=model_gateway,
            tool_executor=tool_executor,
        )

    async def run(self, request: RuntimeRequest) -> RuntimeResponse:
        violations = self.policy_enforcer.check_pre_run(request.agent_spec)
        if violations:
            return self._policy_error(request, violations)

        hermes_config = self.manifest_mapper.to_hermes_config(request.agent_spec)
        session_config = self.session_bridge.map_session(
            request.request.session_id, hermes_config
        )

        # Build tool definitions for the conversation engine
        tools: list[dict[str, Any]] = []
        if self.tool_executor:
            tools = self.tool_bridge.wrap_platform_tools(
                hermes_config.get("tools", []), self.tool_executor
            )

        hermes_result = await self.conversation_engine.converse(
            system_prompt=hermes_config.get("system_prompt", ""),
            user_query=request.request.input.query,
            model_config=hermes_config.get("model", {}),
            tools=tools,
            max_iterations=hermes_config.get("max_iterations", 4),
            session_config=session_config,
        )

        return self.response_mapper.to_platform_response(hermes_result, request)

    @staticmethod
    def _policy_error(request: RuntimeRequest, violations: list[str]) -> RuntimeResponse:
        error = AgentError(
            code="POLICY_VIOLATION",
            message=f"Pre-run policy check failed: {'; '.join(violations)}",
            retryable=False,
        )
        response = AgentResponse(
            request_id=request.request.request_id,
            session_id=request.request.session_id,
            agent=AgentIdentity(
                agent_id=request.agent_spec.agent_id,
                agent_version=request.agent_spec.version,
            ),
            output=AgentOutput(
                status=OutputStatus.FAILED,
                text=ResponseText(display="Policy violation", tts="Policy violation"),
            ),
            error=error,
            debug={"runtime_backend": "hermes"},
        )
        return RuntimeResponse(response=response)
