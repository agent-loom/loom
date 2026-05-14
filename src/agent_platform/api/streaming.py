from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

from agent_platform.domain.models import RuntimeRequest
from agent_platform.runtime.manager import RuntimeManager


class SSEEvent:
    def __init__(self, event: str, data: dict[str, Any]) -> None:
        self.event = event
        self.data = data

    def encode(self) -> str:
        payload = json.dumps(self.data, ensure_ascii=False)
        return f"event: {self.event}\ndata: {payload}\n\n"


async def stream_agent_response(
    runtime_manager: RuntimeManager,
    runtime_request: RuntimeRequest,
) -> AsyncGenerator[str, None]:
    agent = runtime_request.agent_spec
    yield SSEEvent("run.started", {
        "agent_id": agent.agent_id,
        "agent_version": agent.version,
        "run_id": runtime_request.request.request_id,
    }).encode()

    try:
        response = await runtime_manager.run(runtime_request)
        agent_response = response.response

        if agent_response.trace and agent_response.trace.tool_calls:
            for tc in agent_response.trace.tool_calls:
                yield SSEEvent("tool.started", {
                    "tool_name": tc.tool_name,
                }).encode()
                yield SSEEvent("tool.completed", {
                    "tool_name": tc.tool_name,
                    "status": tc.status,
                    "latency_ms": tc.latency_ms,
                }).encode()

        display = agent_response.output.text.display
        chunks = _chunk_text(display, chunk_size=80)
        for chunk in chunks:
            yield SSEEvent("message.delta", {
                "content": chunk,
                "type": "text",
            }).encode()

        yield SSEEvent("message.completed", {
            "text": agent_response.output.text.model_dump(),
            "status": agent_response.output.status.value,
        }).encode()

        if agent_response.output.cards:
            yield SSEEvent("cards", {
                "cards": [c.model_dump() for c in agent_response.output.cards],
            }).encode()

        if agent_response.output.commands:
            yield SSEEvent("commands", {
                "commands": [c.model_dump() for c in agent_response.output.commands],
            }).encode()

        yield SSEEvent("run.completed", {
            "agent_id": agent.agent_id,
            "trace": agent_response.trace.model_dump() if agent_response.trace else None,
        }).encode()

    except Exception as exc:
        yield SSEEvent("error", {
            "code": "STREAM_ERROR",
            "message": str(exc),
        }).encode()


def _chunk_text(text: str, chunk_size: int = 80) -> list[str]:
    if len(text) <= chunk_size:
        return [text]
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]
