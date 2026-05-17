"""统一的 Agent 流式事件模型，供 SSE 和 WebSocket 共用。"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class StreamEventType(StrEnum):
    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    MESSAGE_DELTA = "message.delta"
    MESSAGE_COMPLETED = "message.completed"
    CARDS = "cards"
    COMMANDS = "commands"
    ROUTE_DECISION = "route.decision"
    MODEL_CALL = "model.call"
    ERROR = "error"
    REPLAY = "replay"
    PING = "ping"
    PONG = "pong"


class AgentStreamEvent(BaseModel):
    """Typed event for all agent streaming transports (SSE + WebSocket)."""

    type: StreamEventType
    data: dict[str, Any] = Field(default_factory=dict)
    seq: int | None = None

    def to_sse(self) -> str:
        payload = json.dumps(self.data, ensure_ascii=False)
        return f"event: {self.type.value}\ndata: {payload}\n\n"

    def to_ws_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type.value, **self.data}
        if self.seq is not None:
            d["seq"] = self.seq
        return d


# ── Factory helpers ───────────────────────────────────────────────


def run_started(agent_id: str, agent_version: str, run_id: str) -> AgentStreamEvent:
    return AgentStreamEvent(
        type=StreamEventType.RUN_STARTED,
        data={"agent_id": agent_id, "agent_version": agent_version, "run_id": run_id},
    )


def run_completed(agent_id: str, trace: dict[str, Any] | None = None) -> AgentStreamEvent:
    return AgentStreamEvent(
        type=StreamEventType.RUN_COMPLETED,
        data={"agent_id": agent_id, "trace": trace},
    )


def tool_started(tool_name: str) -> AgentStreamEvent:
    return AgentStreamEvent(
        type=StreamEventType.TOOL_STARTED,
        data={"tool_name": tool_name},
    )


def tool_completed(
    tool_name: str, status: str, latency_ms: int | float,
) -> AgentStreamEvent:
    return AgentStreamEvent(
        type=StreamEventType.TOOL_COMPLETED,
        data={"tool_name": tool_name, "status": status, "latency_ms": latency_ms},
    )


def message_delta(content: str, content_type: str = "text") -> AgentStreamEvent:
    return AgentStreamEvent(
        type=StreamEventType.MESSAGE_DELTA,
        data={"content": content, "type": content_type},
    )


def message_completed(text: dict[str, Any], status: str) -> AgentStreamEvent:
    return AgentStreamEvent(
        type=StreamEventType.MESSAGE_COMPLETED,
        data={"text": text, "status": status},
    )


def cards_event(cards: list[dict[str, Any]]) -> AgentStreamEvent:
    return AgentStreamEvent(
        type=StreamEventType.CARDS,
        data={"cards": cards},
    )


def commands_event(commands: list[dict[str, Any]]) -> AgentStreamEvent:
    return AgentStreamEvent(
        type=StreamEventType.COMMANDS,
        data={"commands": commands},
    )


def route_decision(
    agent_id: str, reason: str, deployment_id: str | None = None,
    traffic_bucket: int | None = None,
) -> AgentStreamEvent:
    return AgentStreamEvent(
        type=StreamEventType.ROUTE_DECISION,
        data={
            "agent_id": agent_id,
            "reason": reason,
            "deployment_id": deployment_id,
            "traffic_bucket": traffic_bucket,
        },
    )


def model_call_event(
    provider: str, model: str, input_tokens: int, output_tokens: int,
    cost_usd: float, latency_ms: float,
) -> AgentStreamEvent:
    return AgentStreamEvent(
        type=StreamEventType.MODEL_CALL,
        data={
            "provider": provider,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
            "latency_ms": latency_ms,
        },
    )


def error_event(code: str, message: str) -> AgentStreamEvent:
    return AgentStreamEvent(
        type=StreamEventType.ERROR,
        data={"code": code, "message": message},
    )
