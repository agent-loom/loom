"""工具执行器：权限检查、输入校验、超时重试、生命周期 Hook。"""

from __future__ import annotations

import asyncio
import logging
from inspect import isawaitable
from time import perf_counter
from typing import Any

from pydantic import BaseModel, Field

from agent_platform.domain.models import ToolCallTrace
from agent_platform.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ToolExecutionResult(BaseModel):
    """工具执行结果，包含输出数据和调用追踪信息。"""
    tool_name: str
    output: dict[str, Any] = Field(default_factory=dict)
    trace: ToolCallTrace


class ToolExecutor:
    """工具执行器，负责权限检查、输入校验及带重试的异步执行。"""

    def __init__(
        self,
        registry: ToolRegistry,
        policy_engine: Any | None = None,
        hook_registry: Any | None = None,
        metrics_collector: Any | None = None,
    ):
        """初始化执行器，注入注册中心、策略引擎及可选的 Hook 与指标收集器。"""
        self.registry = registry
        self.policy_engine = policy_engine
        self.hook_registry = hook_registry
        self.metrics_collector = metrics_collector

    async def execute(
        self,
        tool_name: str,
        payload: dict[str, Any],
        *,
        allowed_tools: list[str],
        timeout_ms: int = 3000,
        agent_spec: Any | None = None,
    ) -> ToolExecutionResult:
        """异步执行指定工具，返回执行结果与追踪信息。"""
        started = perf_counter()
        if tool_name not in allowed_tools:
            latency_ms = self._latency_ms(started)
            return ToolExecutionResult(
                tool_name=tool_name,
                output={"error": "tool is not allowed by manifest"},
                trace=ToolCallTrace(
                    tool_name=tool_name,
                    latency_ms=latency_ms,
                    status="denied",
                    error="TOOL_NOT_ALLOWED",
                ),
            )

        if self.policy_engine and agent_spec:
            try:
                policy_set = self.policy_engine.load_policies(agent_spec)
                violations = self.policy_engine.check_tool_allowed(
                    tool_name, policy_set,
                )
                if violations:
                    latency_ms = self._latency_ms(started)
                    msg = "; ".join(v.message for v in violations)
                    return ToolExecutionResult(
                        tool_name=tool_name,
                        output={"error": msg},
                        trace=ToolCallTrace(
                            tool_name=tool_name,
                            latency_ms=latency_ms,
                            status="denied",
                            error="TOOL_POLICY_DENIED",
                        ),
                    )
            except Exception:
                logger.exception("check_tool_allowed failed for %s", tool_name)

        try:
            definition = self.registry.get(tool_name)
        except LookupError:
            latency_ms = self._latency_ms(started)
            return ToolExecutionResult(
                tool_name=tool_name,
                output={"error": f"tool not found: {tool_name}"},
                trace=ToolCallTrace(
                    tool_name=tool_name,
                    latency_ms=latency_ms,
                    status="failed",
                    error="TOOL_NOT_FOUND",
                ),
            )

        validation_error = self._validate_input(definition.input_schema, payload)
        if validation_error:
            latency_ms = self._latency_ms(started)
            return ToolExecutionResult(
                tool_name=tool_name,
                output={"error": validation_error},
                trace=ToolCallTrace(
                    tool_name=tool_name,
                    latency_ms=latency_ms,
                    status="failed",
                    error="VALIDATION_ERROR",
                ),
            )

        effective_timeout = min(timeout_ms, definition.timeout_ms) / 1000.0
        max_attempts = definition.max_retries + 1
        last_error: Exception | None = None

        # Hook: pre_tool
        if self.hook_registry:
            try:
                await self.hook_registry.emit(
                    "pre_tool", {"tool_name": tool_name, "payload": payload},
                )
            except Exception:
                logger.exception("hook pre_tool failed")

        for attempt in range(max_attempts):
            try:
                result = definition.handler(payload)
                if isawaitable(result):
                    result = await asyncio.wait_for(result, timeout=effective_timeout)
                latency_ms = self._latency_ms(started)
                # Hook: post_tool (success)
                if self.hook_registry:
                    try:
                        await self.hook_registry.emit(
                            "post_tool",
                            {"tool_name": tool_name, "status": "success"},
                        )
                    except Exception:
                        logger.exception("hook post_tool failed")
                # Metrics: success
                if self.metrics_collector:
                    try:
                        self.metrics_collector.record_tool_call(tool_name, "success")
                    except Exception:
                        logger.exception("metrics record_tool_call failed")
                return ToolExecutionResult(
                    tool_name=tool_name,
                    output=dict(result),
                    trace=ToolCallTrace(
                        tool_name=tool_name,
                        runtime_tool_name=definition.name,
                        latency_ms=latency_ms,
                        status="success",
                    ),
                )
            except TimeoutError:
                last_error = TimeoutError(f"tool execution timed out after {timeout_ms}ms")
                if attempt < max_attempts - 1:
                    logger.warning(
                        "tool %s timeout (attempt %d/%d)",
                        tool_name,
                        attempt + 1,
                        max_attempts,
                    )
                    continue
                break
            except Exception as exc:
                last_error = exc
                if attempt < max_attempts - 1:
                    logger.warning(
                        "tool %s failed (attempt %d/%d): %s",
                        tool_name,
                        attempt + 1,
                        max_attempts,
                        exc,
                    )
                    continue
                break

        latency_ms = self._latency_ms(started)
        # Hook: post_tool (failed)
        if self.hook_registry:
            try:
                await self.hook_registry.emit(
                    "post_tool",
                    {"tool_name": tool_name, "status": "failed", "error": str(last_error)},
                )
            except Exception:
                logger.exception("hook post_tool failed")
        # Metrics: failed
        if self.metrics_collector:
            try:
                self.metrics_collector.record_tool_call(tool_name, "failed")
            except Exception:
                logger.exception("metrics record_tool_call failed")
        if isinstance(last_error, TimeoutError):
            return ToolExecutionResult(
                tool_name=tool_name,
                output={"error": str(last_error)},
                trace=ToolCallTrace(
                    tool_name=tool_name,
                    latency_ms=latency_ms,
                    status="timeout",
                    error="TOOL_TIMEOUT",
                ),
            )
        return ToolExecutionResult(
            tool_name=tool_name,
            output={"error": str(last_error)},
            trace=ToolCallTrace(
                tool_name=tool_name,
                latency_ms=latency_ms,
                status="failed",
                error=type(last_error).__name__ if last_error else "UNKNOWN",
            ),
        )

    @staticmethod
    def _validate_input(schema: dict[str, Any], payload: dict[str, Any]) -> str | None:
        if not schema:
            return None
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        for field in required:
            if field not in payload:
                return f"missing required field: {field}"
        for key, value in payload.items():
            if key in properties:
                prop_spec = properties[key]
                expected_type = prop_spec.get("type")
                if expected_type and not _check_type(value, expected_type):
                    return (
                        f"field '{key}' expected type "
                        f"'{expected_type}', got "
                        f"'{type(value).__name__}'"
                    )
        return None

    @staticmethod
    def _latency_ms(started: float) -> int:
        return max(0, round((perf_counter() - started) * 1000))


def _check_type(value: Any, expected: str) -> bool:
    type_map = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    expected_types = type_map.get(expected)
    if expected_types is None:
        return True
    return isinstance(value, expected_types)
