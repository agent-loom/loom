"""Hermes SDK 事件到平台 AgentStreamEvent 的映射层。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from agent_platform.api.stream_events import (
    AgentStreamEvent,
    error_event,
    message_completed,
    message_delta,
    model_call_event,
    run_completed,
    run_started,
    tool_completed,
    tool_started,
)
from agent_platform.domain.models import RuntimeResponse

logger = logging.getLogger(__name__)


class HermesStreamMapper:
    """将 Hermes 运行时的中间事件映射为平台统一流式事件。

    支持两种模式：
    1. 包装同步 run — 在运行前后发射 run_started/run_completed，
       从最终结果中提取 tool_call、model_call 等事件。
    2. 原生流式 — 如果 Hermes SDK 提供 streaming callback，
       逐事件映射为 AgentStreamEvent。
    """

    def __init__(self, agent_id: str, agent_version: str = ""):
        self._agent_id = agent_id
        self._agent_version = agent_version
        self._seq = 0  # 事件序列号，保证流内事件有序

    def _next_seq(self) -> int:
        """递增并返回下一个事件序列号。"""
        self._seq += 1
        return self._seq

    # ── 从完成的结果重建事件流 ──────────────────────────────

    async def from_result(
        self,
        run_id: str,
        hermes_result: dict[str, Any],
        response: RuntimeResponse,
        elapsed_ms: int,
    ) -> AsyncIterator[AgentStreamEvent]:
        """从 Hermes 同步运行结果重建完整事件流。"""
        ev = run_started(self._agent_id, self._agent_version, run_id)
        ev.seq = self._next_seq()
        yield ev

        for tc in hermes_result.get("tool_calls", []):
            name = tc.get("name", tc.get("tool_name", "unknown"))
            ev_start = tool_started(name)
            ev_start.seq = self._next_seq()
            yield ev_start

            ev_end = tool_completed(
                name,
                tc.get("status", "success"),
                tc.get("latency_ms", 0),
            )
            ev_end.seq = self._next_seq()
            yield ev_end

        model = hermes_result.get("model", "unknown")
        provider = hermes_result.get("provider", "unknown")
        prompt_tokens = hermes_result.get("prompt_tokens", 0)
        completion_tokens = hermes_result.get("completion_tokens", 0)
        cost_usd = hermes_result.get("estimated_cost_usd", 0.0) or 0.0
        ev_model = model_call_event(
            provider=provider,
            model=model,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            cost_usd=cost_usd,
            latency_ms=elapsed_ms,
        )
        ev_model.seq = self._next_seq()
        yield ev_model

        text = hermes_result.get("text", "")
        if text:
            ev_delta = message_delta(text)
            ev_delta.seq = self._next_seq()
            yield ev_delta

        trace_dict = None
        if response.response.trace:
            trace_dict = response.response.trace.model_dump(mode="json")

        ev_msg = message_completed(
            text={"display": text, "tts": text},
            status="success",
        )
        ev_msg.seq = self._next_seq()
        yield ev_msg

        ev_done = run_completed(self._agent_id, trace=trace_dict)
        ev_done.seq = self._next_seq()
        yield ev_done

    # ── Hermes SDK 原生流式回调映射 ────────────────────────

    def map_hermes_event(
        self, event_type: str, event_data: dict[str, Any],
    ) -> AgentStreamEvent | None:
        """将单个 Hermes SDK 原生事件映射为 AgentStreamEvent。

        返回 None 表示该事件无需转发。
        """
        mapper = _EVENT_MAPPERS.get(event_type)
        if mapper is None:
            logger.debug("忽略未映射的 Hermes 事件类型: %s", event_type)
            return None

        ev = mapper(self, event_data)
        if ev is not None:
            ev.seq = self._next_seq()
        return ev

    # ── 各事件类型映射函数（Hermes SDK 事件 -> 平台统一事件） ──

    def _map_conversation_start(self, data: dict[str, Any]) -> AgentStreamEvent:
        """映射会话开始事件。"""
        run_id = data.get("run_id", "unknown")
        return run_started(self._agent_id, self._agent_version, run_id)

    def _map_conversation_end(self, data: dict[str, Any]) -> AgentStreamEvent:
        """映射会话结束事件。"""
        return run_completed(self._agent_id, trace=data.get("trace"))

    def _map_tool_call_start(self, data: dict[str, Any]) -> AgentStreamEvent:
        """映射工具调用开始事件。"""
        return tool_started(data.get("tool_name", data.get("name", "unknown")))

    def _map_tool_call_end(self, data: dict[str, Any]) -> AgentStreamEvent:
        """映射工具调用结束事件。"""
        return tool_completed(
            data.get("tool_name", data.get("name", "unknown")),
            data.get("status", "success"),
            data.get("latency_ms", 0),
        )

    def _map_llm_response(self, data: dict[str, Any]) -> AgentStreamEvent:
        """映射 LLM 模型调用响应事件，包含 token 用量和成本。"""
        return model_call_event(
            provider=data.get("provider", "unknown"),
            model=data.get("model", "unknown"),
            input_tokens=data.get("input_tokens", data.get("prompt_tokens", 0)),
            output_tokens=data.get("output_tokens", data.get("completion_tokens", 0)),
            cost_usd=data.get("cost_usd", data.get("estimated_cost_usd", 0.0)) or 0.0,
            latency_ms=data.get("latency_ms", 0),
        )

    def _map_text_chunk(self, data: dict[str, Any]) -> AgentStreamEvent:
        """映射文本增量片段事件。"""
        return message_delta(data.get("content", data.get("text", "")))

    def _map_error(self, data: dict[str, Any]) -> AgentStreamEvent:
        """映射 Hermes 运行时错误事件。"""
        return error_event(
            code=data.get("code", "HERMES_ERROR"),
            message=data.get("message", str(data)),
        )

    # ── 流式 run 包装 ─────────────────────────────────────

    async def wrap_streaming_run(
        self,
        run_id: str,
        hermes_event_iterator: AsyncIterator[tuple[str, dict[str, Any]]],
    ) -> AsyncIterator[AgentStreamEvent]:
        """包装 Hermes SDK 的流式事件迭代器，产出平台统一事件。"""
        ev = run_started(self._agent_id, self._agent_version, run_id)
        ev.seq = self._next_seq()
        yield ev

        try:
            async for event_type, event_data in hermes_event_iterator:
                mapped = self.map_hermes_event(event_type, event_data)
                if mapped is not None:
                    yield mapped
        except Exception as exc:
            ev_err = error_event("HERMES_STREAM_ERROR", str(exc))
            ev_err.seq = self._next_seq()
            yield ev_err

        ev_done = run_completed(self._agent_id)
        ev_done.seq = self._next_seq()
        yield ev_done


# Hermes SDK 事件类型到映射函数的注册表，支持多种事件别名
_EVENT_MAPPERS: dict[str, Any] = {
    "conversation_start": HermesStreamMapper._map_conversation_start,
    "conversation_end": HermesStreamMapper._map_conversation_end,
    "tool_call_start": HermesStreamMapper._map_tool_call_start,
    "tool_call_end": HermesStreamMapper._map_tool_call_end,
    "llm_response": HermesStreamMapper._map_llm_response,
    "text_chunk": HermesStreamMapper._map_text_chunk,
    "error": HermesStreamMapper._map_error,
    "tool_start": HermesStreamMapper._map_tool_call_start,
    "tool_end": HermesStreamMapper._map_tool_call_end,
    "chunk": HermesStreamMapper._map_text_chunk,
}
