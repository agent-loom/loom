from __future__ import annotations

from inspect import isawaitable
from time import perf_counter
from typing import Any

from pydantic import BaseModel, Field

from agent_platform.domain.models import ToolCallTrace
from agent_platform.tools.registry import ToolRegistry


class ToolExecutionResult(BaseModel):
    tool_name: str
    output: dict[str, Any] = Field(default_factory=dict)
    trace: ToolCallTrace


class ToolExecutor:
    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    async def execute(
        self,
        tool_name: str,
        payload: dict[str, Any],
        *,
        allowed_tools: list[str],
    ) -> ToolExecutionResult:
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

        try:
            definition = self.registry.get(tool_name)
            result = definition.handler(payload)
            if isawaitable(result):
                result = await result
            latency_ms = self._latency_ms(started)
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
        except Exception as exc:
            latency_ms = self._latency_ms(started)
            return ToolExecutionResult(
                tool_name=tool_name,
                output={"error": str(exc)},
                trace=ToolCallTrace(
                    tool_name=tool_name,
                    latency_ms=latency_ms,
                    status="failed",
                    error=type(exc).__name__,
                ),
            )

    @staticmethod
    def _latency_ms(started: float) -> int:
        return max(0, round((perf_counter() - started) * 1000))
