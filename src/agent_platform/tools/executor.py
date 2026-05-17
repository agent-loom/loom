"""工具执行器：权限检查、输入校验、超时重试、生命周期 Hook。"""

from __future__ import annotations

import asyncio
import logging
from inspect import isawaitable
from time import perf_counter
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from agent_platform.domain.models import ToolCallTrace
from agent_platform.observability.instrumentation import instrument_tool_call
from agent_platform.observability.tracing import get_tracer
from agent_platform.tools.approval import ApprovalGate, ApprovalRequest, ApprovalStatus
from agent_platform.tools.registry import ToolRegistry
from agent_platform.tools.schema_validator import validate_tool_input

logger = logging.getLogger(__name__)
tracer = get_tracer("agent_platform.tools")


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
        approval_gate: ApprovalGate | None = None,
        audit_repo: Any | None = None,
    ):
        """初始化执行器，注入注册中心、策略引擎及可选的 Hook 与指标收集器。"""
        self.registry = registry
        self.policy_engine = policy_engine
        self.hook_registry = hook_registry
        self.metrics_collector = metrics_collector
        self.approval_gate = approval_gate
        self.audit_repo = audit_repo

    async def execute(
        self,
        tool_name: str,
        payload: dict[str, Any],
        *,
        allowed_tools: list[str],
        timeout_ms: int = 3000,
        agent_spec: Any | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
    ) -> ToolExecutionResult:
        """异步执行指定工具，返回执行结果与追踪信息。"""
        result = await self._execute_inner(
            tool_name, payload,
            allowed_tools=allowed_tools,
            timeout_ms=timeout_ms,
            agent_spec=agent_spec,
            agent_id=agent_id,
            run_id=run_id,
        )
        await self._record_audit(result, payload, agent_id, run_id)
        return result

    async def _execute_inner(
        self,
        tool_name: str,
        payload: dict[str, Any],
        *,
        allowed_tools: list[str],
        timeout_ms: int = 3000,
        agent_spec: Any | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
    ) -> ToolExecutionResult:
        """内部执行逻辑。"""
        started = perf_counter()

        with tracer.start_as_current_span("tool_call") as span:
            span.set_attribute("tool.name", tool_name)

            if tool_name not in allowed_tools:
                latency_ms = self._latency_ms(started)
                instrument_tool_call(span, tool_name, "denied")
                span.set_attribute("tool.latency_ms", latency_ms)
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
                        instrument_tool_call(span, tool_name, "denied")
                        span.set_attribute("tool.latency_ms", latency_ms)
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
                instrument_tool_call(span, tool_name, "failed")
                span.set_attribute("tool.latency_ms", latency_ms)
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
                instrument_tool_call(span, tool_name, "failed")
                span.set_attribute("tool.latency_ms", latency_ms)
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

            # --- Approval gate for high-risk tools ---
            if self.approval_gate and definition.risk_level in ("high", "critical"):
                approval_request = ApprovalRequest(
                    request_id=f"apr_{uuid4().hex}",
                    tool_name=tool_name,
                    risk_level=definition.risk_level,
                    payload=payload,
                    agent_id=agent_id,
                    run_id=run_id,
                    reason=f"Tool '{tool_name}' has risk_level={definition.risk_level}",
                )
                approval_status = await self.approval_gate.request_approval(
                    approval_request,
                )
                # If the gate returned PENDING, poll once more (gate may have
                # resolved externally between request and check).
                if approval_status == ApprovalStatus.PENDING:
                    approval_status = await self.approval_gate.check_status(
                        approval_request.request_id,
                    )

                if approval_status == ApprovalStatus.REJECTED:
                    latency_ms = self._latency_ms(started)
                    instrument_tool_call(span, tool_name, "denied")
                    span.set_attribute("tool.latency_ms", latency_ms)
                    return ToolExecutionResult(
                        tool_name=tool_name,
                        output={"error": "tool execution rejected by approval gate"},
                        trace=ToolCallTrace(
                            tool_name=tool_name,
                            latency_ms=latency_ms,
                            status="denied",
                            error="APPROVAL_DENIED",
                        ),
                    )
                if approval_status in (
                    ApprovalStatus.EXPIRED,
                    ApprovalStatus.PENDING,
                ):
                    latency_ms = self._latency_ms(started)
                    instrument_tool_call(span, tool_name, "denied")
                    span.set_attribute("tool.latency_ms", latency_ms)
                    return ToolExecutionResult(
                        tool_name=tool_name,
                        output={"error": "approval request expired or not resolved"},
                        trace=ToolCallTrace(
                            tool_name=tool_name,
                            latency_ms=latency_ms,
                            status="denied",
                            error="APPROVAL_EXPIRED",
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
                            # 记录工具执行耗时（转换为秒）
                            self.metrics_collector.record_tool_duration(
                                tool_name, latency_ms / 1000.0,
                            )
                        except Exception:
                            logger.exception("metrics record_tool_call failed")
                    instrument_tool_call(span, tool_name, "success")
                    span.set_attribute("tool.latency_ms", latency_ms)
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
                    # 记录工具执行耗时（转换为秒）
                    self.metrics_collector.record_tool_duration(
                        tool_name, latency_ms / 1000.0,
                    )
                except Exception:
                    logger.exception("metrics record_tool_call failed")

            if isinstance(last_error, TimeoutError):
                instrument_tool_call(span, tool_name, "timeout")
                span.set_attribute("tool.latency_ms", latency_ms)
                span.set_status("ERROR", str(last_error))
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
            instrument_tool_call(span, tool_name, "failed")
            span.set_attribute("tool.latency_ms", latency_ms)
            span.set_status("ERROR", str(last_error))
            if last_error:
                span.record_exception(last_error)
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
        """校验工具调用参数，优先使用 JSON Schema 完整校验。"""
        return validate_tool_input(schema, payload)

    @staticmethod
    def _latency_ms(started: float) -> int:
        return max(0, round((perf_counter() - started) * 1000))

    async def _record_audit(
        self,
        result: ToolExecutionResult,
        payload: dict[str, Any],
        agent_id: str | None,
        run_id: str | None,
    ) -> None:
        if self.audit_repo is None:
            return
        try:
            await self.audit_repo.record(
                tool_name=result.tool_name,
                status=result.trace.status,
                latency_ms=int(result.trace.latency_ms),
                error=result.trace.error,
                payload=payload,
                output=result.output,
                run_id=run_id,
                agent_id=agent_id,
            )
        except Exception:
            logger.exception("failed to persist tool audit for %s", result.tool_name)
