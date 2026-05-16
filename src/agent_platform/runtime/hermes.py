from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
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

try:
    from hermes_agent import AIAgent as _HermesAIAgent

    HERMES_AVAILABLE = True
except ImportError:
    HERMES_AVAILABLE = False
    _HermesAIAgent = None


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
    def wrap_platform_tools(tool_names: list[str], tool_executor: Any) -> list[dict[str, Any]]:
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
        return RuntimeResponse(
            response=AgentResponse(
                request_id=request.request.request_id,
                session_id=request.request.session_id,
                agent=AgentIdentity(
                    agent_id=request.agent_spec.agent_id,
                    agent_version=request.agent_spec.version,
                    deployment_id=request.deployment_id,
                ),
                output=AgentOutput(
                    text=ResponseText(
                        display=hermes_result.get("text", "Hermes response"),
                        tts=hermes_result.get("text", "Hermes response"),
                    ),
                ),
                trace=ResponseTrace(
                    route_reason=request.route_reason,
                    tool_calls=[
                        ToolCallTrace(
                            tool_name=tc.get("name", ""),
                            status=tc.get("status", "success"),
                            latency_ms=tc.get("latency_ms"),
                        )
                        for tc in hermes_result.get("tool_calls", [])
                    ],
                    model=hermes_result.get("model"),
                    prompt_tokens=hermes_result.get("prompt_tokens", 0),
                    completion_tokens=hermes_result.get("completion_tokens", 0),
                    total_tokens=hermes_result.get("total_tokens", 0),
                    estimated_cost_usd=hermes_result.get("estimated_cost_usd"),
                ),
                debug={
                    "runtime_backend": "hermes",
                    **hermes_result.get("debug_extra", {}),
                },
            )
        )


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


# ---------------------------------------------------------------------------
# P1-2: Hermes tool bridging
# ---------------------------------------------------------------------------

def register_platform_tools_to_hermes(
    tool_executor: Any,
    agent_id: str,
) -> Callable[[], None]:
    """Register every tool from *tool_executor* into the Hermes SDK global
    registry, prefixed by *agent_id* to avoid name collisions.

    Returns a zero-arg **deregister** callable that removes all registered
    tools — intended to be called in a ``finally`` block after the Hermes run.

    If the Hermes SDK is not installed the function is a no-op and the
    returned deregister callable is also a no-op.
    """
    if not HERMES_AVAILABLE:
        return lambda: None

    try:
        from hermes_agent.tools.registry import global_registry
    except ImportError:
        return lambda: None

    registered_names: list[str] = []

    for defn in tool_executor.registry.list_tools():
        hermes_name = f"{agent_id}__{defn.name}"

        def _make_handler(tool_name: str) -> Callable[[dict[str, Any]], str]:
            def handler(args: dict[str, Any], **_kw: Any) -> str:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

                result = loop.run_until_complete(
                    tool_executor.execute(
                        tool_name,
                        args,
                        allowed_tools=[tool_name],
                        timeout_ms=defn.timeout_ms,
                    )
                )
                return str(result.output)

            return handler

        global_registry.register(
            name=hermes_name,
            toolset=f"platform_{agent_id}",
            schema={
                "name": hermes_name,
                "description": defn.description,
                "parameters": defn.input_schema,
            },
            handler=_make_handler(defn.name),
            is_async=False,
            description=defn.description,
            emoji="",
        )
        registered_names.append(hermes_name)

    def deregister() -> None:
        for name in registered_names:
            try:
                global_registry.deregister(name)
            except Exception:
                pass

    return deregister


# ---------------------------------------------------------------------------
# P1-4: Hermes result normalization
# ---------------------------------------------------------------------------

def normalize_hermes_result(hermes_result: Any) -> dict[str, Any]:
    """Convert a raw Hermes SDK response into a flat dict consumable by
    ``ResponseMapper.to_platform_response``.

    Handles both dict-like and object-attribute access patterns so the code
    is resilient to SDK version changes.
    """
    if isinstance(hermes_result, dict):
        raw = hermes_result
    else:
        raw = getattr(hermes_result, "__dict__", {}) or {}

    final_response = raw.get("final_response") or raw.get("response") or str(hermes_result)

    api_calls = raw.get("api_calls", [])
    input_tokens = raw.get("input_tokens") or raw.get("prompt_tokens", 0)
    output_tokens = raw.get("output_tokens") or raw.get("completion_tokens", 0)
    total_tokens = raw.get("total_tokens", input_tokens + output_tokens)
    estimated_cost = raw.get("estimated_cost_usd")

    raw_tool_calls = raw.get("tool_calls", [])
    tool_calls: list[dict[str, Any]] = []
    for tc in raw_tool_calls:
        if isinstance(tc, dict):
            tool_calls.append({
                "name": tc.get("name", tc.get("tool_name", "")),
                "status": tc.get("status", "success"),
                "latency_ms": tc.get("latency_ms"),
            })
        else:
            tool_calls.append({
                "name": getattr(tc, "name", getattr(tc, "tool_name", "")),
                "status": getattr(tc, "status", "success"),
                "latency_ms": getattr(tc, "latency_ms", None),
            })

    return {
        "text": str(final_response),
        "tool_calls": tool_calls,
        "run_id": raw.get("run_id"),
        "iterations": len(raw.get("messages", [])) if "messages" in raw else raw.get("iterations", 0),
        "model_calls": len(api_calls) if api_calls else raw.get("model_calls", 0),
        "model": raw.get("model"),
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": total_tokens,
        "estimated_cost_usd": estimated_cost,
        "debug_extra": {
            "hermes_run_id": raw.get("run_id"),
            "api_calls_count": len(api_calls) if api_calls else 0,
        },
    }


# ---------------------------------------------------------------------------
# Spike A conversation engine (kept for fallback)
# ---------------------------------------------------------------------------

class ConversationEngine:
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
        if self.model_gateway is None:
            return self._stub_response(system_prompt, user_query, model_config)

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
                temperature=model_config.get("temperature", 0.2),
                max_tokens=model_config.get("max_tokens", 1024),
                tools=tools or None,
            )

            if not model_response.tool_calls:
                return {
                    "text": model_response.content,
                    "tool_calls": tool_call_traces,
                    "iterations": iteration + 1,
                    "model_calls": total_model_calls,
                }

            for tc in model_response.tool_calls:
                tool_name = tc.name
                tool_input = tc.arguments
                if self.tool_executor:
                    tool_result = await self.tool_executor.execute(
                        tool_name,
                        tool_input,
                        allowed_tools=[t["name"] for t in tools],
                        timeout_ms=3000,
                    )
                    tool_output = tool_result.output
                    tool_call_traces.append({
                        "name": tool_name,
                        "status": tool_result.trace.status,
                        "latency_ms": tool_result.trace.latency_ms,
                    })
                else:
                    tool_output = {"result": f"[stub] {tool_name} not executed"}
                    tool_call_traces.append({
                        "name": tool_name,
                        "status": "skipped",
                    })

                messages.append(ModelMessage(role="tool", content=str(tool_output)))

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


# ---------------------------------------------------------------------------
# HermesRuntimeBackend (P1-3 / P1-5)
# ---------------------------------------------------------------------------

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

    # P1-5: fallback dispatch --------------------------------------------------

    async def run(self, request: RuntimeRequest) -> RuntimeResponse:
        violations = self.policy_enforcer.check_pre_run(request.agent_spec)
        if violations:
            return self._policy_error(request, violations)

        hermes_config = self.manifest_mapper.to_hermes_config(request.agent_spec)

        if HERMES_AVAILABLE:
            try:
                return await self._run_with_hermes(request, hermes_config)
            except Exception as e:
                logger.error("Hermes SDK run failed, falling back to engine: %s", e)

        return await self._run_with_engine(request, hermes_config)

    # Spike A path --------------------------------------------------------------

    async def _run_with_engine(
        self, request: RuntimeRequest, hermes_config: dict[str, Any]
    ) -> RuntimeResponse:
        session_config = self.session_bridge.map_session(
            request.request.session_id, hermes_config
        )

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

    # P1-3: Hermes SDK path -----------------------------------------------------

    async def _run_with_hermes(
        self, request: RuntimeRequest, hermes_config: dict[str, Any]
    ) -> RuntimeResponse:
        import anyio

        deregister = lambda: None  # noqa: E731
        if self.tool_executor:
            deregister = register_platform_tools_to_hermes(
                self.tool_executor,
                hermes_config.get("agent_id", "unknown"),
            )

        model_cfg = hermes_config.get("model", {})
        agent_id = hermes_config.get("agent_id", "unknown")
        toolset_name = f"platform_{agent_id}"

        agent = _HermesAIAgent(
            provider=model_cfg.get("provider", "openai"),
            model=model_cfg.get("model", "native-demo"),
            enabled_toolsets=[toolset_name] if self.tool_executor else [],
        )

        system_prompt = hermes_config.get("system_prompt", "")
        if request.runtime_context and getattr(request.runtime_context, "knowledge_snippets", None):
            knowledge_block = "\n\n".join(request.runtime_context.knowledge_snippets)
            system_prompt += f"\n\n[Knowledge Context]\n{knowledge_block}"

        conversation_history: list[dict[str, str]] = []
        if request.runtime_context and getattr(request.runtime_context, "messages", None):
            msgs = request.runtime_context.messages
            if (
                msgs
                and msgs[-1].get("role") == "user"
                and msgs[-1].get("content") == request.request.input.query
            ):
                msgs = msgs[:-1]
            conversation_history = [
                {"role": m["role"], "content": m["content"]} for m in msgs
            ]

        try:
            hermes_result = await anyio.to_thread.run_sync(
                lambda: agent.run_conversation(
                    user_message=request.request.input.query,
                    system_message=system_prompt,
                    conversation_history=conversation_history,
                )
            )
        finally:
            deregister()

        result_dict = normalize_hermes_result(hermes_result)
        return self.response_mapper.to_platform_response(result_dict, request)

    # Policy error helper -------------------------------------------------------

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
