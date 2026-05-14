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


class HermesRuntimeBackend:
    name = "hermes"

    def __init__(self):
        self.manifest_mapper = ManifestMapper()
        self.tool_bridge = ToolBridge()
        self.session_bridge = SessionBridge()
        self.response_mapper = ResponseMapper()
        self.trace_bridge = TraceBridge()
        self.policy_enforcer = PolicyEnforcer()

    async def run(self, request: RuntimeRequest) -> RuntimeResponse:
        violations = self.policy_enforcer.check_pre_run(request.agent_spec)
        if violations:
            return self._policy_error(request, violations)

        hermes_config = self.manifest_mapper.to_hermes_config(request.agent_spec)
        session_config = self.session_bridge.map_session(
            request.request.session_id, hermes_config
        )

        hermes_result = await self._execute_hermes(
            hermes_config, request, session_config
        )

        return self.response_mapper.to_platform_response(hermes_result, request)

    async def _execute_hermes(
        self,
        config: dict[str, Any],
        request: RuntimeRequest,
        session_config: dict[str, Any],
    ) -> dict[str, Any]:
        logger.info(
            "Hermes execution requested for %s (not yet connected to real Hermes runtime)",
            config.get("agent_id"),
        )
        return {
            "text": (
                f"[Hermes] Agent {config['agent_id']} received: "
                f"{request.request.input.query}"
            ),
            "tool_calls": [],
            "run_id": None,
            "iterations": 0,
            "model_calls": 0,
        }

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
