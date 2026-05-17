"""SSE (Server-Sent Events) 流式响应支持。"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from agent_platform.api.stream_events import (
    cards_event,
    commands_event,
    error_event,
    message_completed,
    message_delta,
    run_completed,
    run_started,
    tool_completed,
    tool_started,
)
from agent_platform.domain.models import RuntimeRequest
from agent_platform.runtime.manager import RuntimeManager


async def stream_agent_response(
    runtime_manager: RuntimeManager,
    runtime_request: RuntimeRequest,
) -> AsyncGenerator[str, None]:
    """以 SSE 流的方式输出 Agent 运行结果。"""
    agent = runtime_request.agent_spec
    yield run_started(
        agent.agent_id, agent.version, runtime_request.request.request_id,
    ).to_sse()

    try:
        response = await runtime_manager.run(runtime_request)
        agent_response = response.response

        if agent_response.trace and agent_response.trace.tool_calls:
            for tc in agent_response.trace.tool_calls:
                yield tool_started(tc.tool_name).to_sse()
                yield tool_completed(
                    tc.tool_name, tc.status, tc.latency_ms,
                ).to_sse()

        display = agent_response.output.text.display
        chunks = _chunk_text(display, chunk_size=80)
        for chunk in chunks:
            yield message_delta(chunk).to_sse()

        yield message_completed(
            agent_response.output.text.model_dump(),
            agent_response.output.status.value,
        ).to_sse()

        if agent_response.output.cards:
            yield cards_event(
                [c.model_dump() for c in agent_response.output.cards],
            ).to_sse()

        if agent_response.output.commands:
            yield commands_event(
                [c.model_dump() for c in agent_response.output.commands],
            ).to_sse()

        yield run_completed(
            agent.agent_id,
            agent_response.trace.model_dump() if agent_response.trace else None,
        ).to_sse()

    except Exception as exc:
        yield error_event("STREAM_ERROR", str(exc)).to_sse()


def _chunk_text(text: str, chunk_size: int = 80) -> list[str]:
    if len(text) <= chunk_size:
        return [text]
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]
