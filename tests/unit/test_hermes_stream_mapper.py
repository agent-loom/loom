"""HermesStreamMapper 测试 — Hermes 事件到平台 AgentStreamEvent 的映射。"""

import pytest

from agent_platform.api.stream_events import StreamEventType
from agent_platform.runtime.hermes_stream import HermesStreamMapper

# ── 辅助 ─────────────────────────────────────────────────


def _make_mapper() -> HermesStreamMapper:
    return HermesStreamMapper(agent_id="test-agent", agent_version="1.0.0")


def _make_hermes_result(
    text: str = "hi",
    tool_calls: list | None = None,
    model: str = "gpt-4o",
    provider: str = "openai",
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    cost: float | None = 0.001,
) -> dict:
    return {
        "text": text,
        "tool_calls": tool_calls or [],
        "model": model,
        "provider": provider,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "estimated_cost_usd": cost,
        "run_id": "run-123",
    }


# ── from_result 基本流 ───────────────────────────────────


@pytest.mark.asyncio
async def test_from_result_basic_flow():
    mapper = _make_mapper()
    hermes_result = _make_hermes_result()
    response = type("R", (), {"response": type("Resp", (), {"trace": None})()})()

    events = []
    async for ev in mapper.from_result("run-1", hermes_result, response, 200):
        events.append(ev)

    event_types = [e.type for e in events]
    assert event_types[0] == StreamEventType.RUN_STARTED
    assert StreamEventType.MODEL_CALL in event_types
    assert StreamEventType.MESSAGE_DELTA in event_types
    assert StreamEventType.MESSAGE_COMPLETED in event_types
    assert event_types[-1] == StreamEventType.RUN_COMPLETED


@pytest.mark.asyncio
async def test_from_result_with_tool_calls():
    mapper = _make_mapper()
    hermes_result = _make_hermes_result(tool_calls=[
        {"name": "search", "status": "success", "latency_ms": 50},
        {"name": "compute", "status": "success", "latency_ms": 30},
    ])
    response = type("R", (), {"response": type("Resp", (), {"trace": None})()})()

    events = []
    async for ev in mapper.from_result("run-2", hermes_result, response, 300):
        events.append(ev)

    tool_events = [
        e for e in events
        if e.type in (StreamEventType.TOOL_STARTED, StreamEventType.TOOL_COMPLETED)
    ]
    assert len(tool_events) == 4


@pytest.mark.asyncio
async def test_from_result_seq_monotonic():
    mapper = _make_mapper()
    hermes_result = _make_hermes_result()
    response = type("R", (), {"response": type("Resp", (), {"trace": None})()})()

    events = []
    async for ev in mapper.from_result("run-3", hermes_result, response, 100):
        events.append(ev)

    seqs = [e.seq for e in events if e.seq is not None]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


# ── map_hermes_event 单事件映射 ──────────────────────────


def test_map_conversation_start():
    mapper = _make_mapper()
    ev = mapper.map_hermes_event("conversation_start", {"run_id": "run-x"})
    assert ev is not None
    assert ev.type == StreamEventType.RUN_STARTED
    assert ev.data["agent_id"] == "test-agent"


def test_map_conversation_end():
    mapper = _make_mapper()
    ev = mapper.map_hermes_event("conversation_end", {})
    assert ev is not None
    assert ev.type == StreamEventType.RUN_COMPLETED


def test_map_tool_call_start():
    mapper = _make_mapper()
    ev = mapper.map_hermes_event("tool_call_start", {"tool_name": "search"})
    assert ev is not None
    assert ev.type == StreamEventType.TOOL_STARTED
    assert ev.data["tool_name"] == "search"


def test_map_tool_call_end():
    mapper = _make_mapper()
    ev = mapper.map_hermes_event(
        "tool_call_end",
        {"tool_name": "search", "status": "success", "latency_ms": 42},
    )
    assert ev is not None
    assert ev.type == StreamEventType.TOOL_COMPLETED
    assert ev.data["latency_ms"] == 42


def test_map_llm_response():
    mapper = _make_mapper()
    ev = mapper.map_hermes_event("llm_response", {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "input_tokens": 500,
        "output_tokens": 200,
        "cost_usd": 0.005,
        "latency_ms": 1200,
    })
    assert ev is not None
    assert ev.type == StreamEventType.MODEL_CALL
    assert ev.data["provider"] == "anthropic"


def test_map_text_chunk():
    mapper = _make_mapper()
    ev = mapper.map_hermes_event("text_chunk", {"content": "hello"})
    assert ev is not None
    assert ev.type == StreamEventType.MESSAGE_DELTA
    assert ev.data["content"] == "hello"


def test_map_chunk_alias():
    mapper = _make_mapper()
    ev = mapper.map_hermes_event("chunk", {"text": "world"})
    assert ev is not None
    assert ev.type == StreamEventType.MESSAGE_DELTA


def test_map_error():
    mapper = _make_mapper()
    ev = mapper.map_hermes_event("error", {"code": "TIMEOUT", "message": "timed out"})
    assert ev is not None
    assert ev.type == StreamEventType.ERROR
    assert ev.data["code"] == "TIMEOUT"


def test_map_unknown_event_returns_none():
    mapper = _make_mapper()
    ev = mapper.map_hermes_event("some_unknown_event", {"data": "test"})
    assert ev is None


def test_map_tool_start_alias():
    mapper = _make_mapper()
    ev = mapper.map_hermes_event("tool_start", {"name": "calc"})
    assert ev is not None
    assert ev.type == StreamEventType.TOOL_STARTED


def test_map_tool_end_alias():
    mapper = _make_mapper()
    ev = mapper.map_hermes_event("tool_end", {"name": "calc", "status": "error", "latency_ms": 100})
    assert ev is not None
    assert ev.type == StreamEventType.TOOL_COMPLETED
    assert ev.data["status"] == "error"


# ── wrap_streaming_run ───────────────────────────────────


@pytest.mark.asyncio
async def test_wrap_streaming_run_basic():
    mapper = _make_mapper()

    async def _fake_stream():
        yield ("text_chunk", {"content": "hello "})
        yield ("text_chunk", {"content": "world"})

    events = []
    async for ev in mapper.wrap_streaming_run("run-s1", _fake_stream()):
        events.append(ev)

    assert events[0].type == StreamEventType.RUN_STARTED
    assert events[-1].type == StreamEventType.RUN_COMPLETED
    deltas = [e for e in events if e.type == StreamEventType.MESSAGE_DELTA]
    assert len(deltas) == 2


@pytest.mark.asyncio
async def test_wrap_streaming_run_with_error():
    mapper = _make_mapper()

    async def _error_stream():
        yield ("text_chunk", {"content": "partial"})
        raise RuntimeError("stream broke")

    events = []
    async for ev in mapper.wrap_streaming_run("run-s2", _error_stream()):
        events.append(ev)

    error_events = [e for e in events if e.type == StreamEventType.ERROR]
    assert len(error_events) == 1
    assert "stream broke" in error_events[0].data["message"]
    assert events[-1].type == StreamEventType.RUN_COMPLETED
