from __future__ import annotations

import asyncio
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
        timeout_ms: int = 3000,
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
            effective_timeout = min(timeout_ms, definition.timeout_ms) / 1000.0
            result = definition.handler(payload)
            if isawaitable(result):
                result = await asyncio.wait_for(result, timeout=effective_timeout)
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
        except TimeoutError:
            latency_ms = self._latency_ms(started)
            return ToolExecutionResult(
                tool_name=tool_name,
                output={"error": f"tool execution timed out after {timeout_ms}ms"},
                trace=ToolCallTrace(
                    tool_name=tool_name,
                    latency_ms=latency_ms,
                    status="timeout",
                    error="TOOL_TIMEOUT",
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
