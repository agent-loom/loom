"""Tests for unified AgentStreamEvent model."""

from __future__ import annotations

import json

from agent_platform.api.stream_events import (
    AgentStreamEvent,
    StreamEventType,
    cards_event,
    commands_event,
    error_event,
    message_completed,
    message_delta,
    model_call_event,
    route_decision,
    run_completed,
    run_started,
    tool_completed,
    tool_started,
)


def test_to_sse_format():
    event = run_started("echo", "1.0.0", "req-1")
    sse = event.to_sse()
    assert sse.startswith("event: run.started\n")
    assert "data: " in sse
    data = json.loads(sse.split("data: ")[1].strip())
    assert data["agent_id"] == "echo"
    assert data["run_id"] == "req-1"


def test_to_ws_dict():
    event = tool_completed("search", "success", 42)
    d = event.to_ws_dict()
    assert d["type"] == "tool.completed"
    assert d["tool_name"] == "search"
    assert d["latency_ms"] == 42


def test_to_ws_dict_with_seq():
    event = AgentStreamEvent(
        type=StreamEventType.MESSAGE_DELTA,
        data={"content": "hi"},
        seq=5,
    )
    d = event.to_ws_dict()
    assert d["seq"] == 5


def test_run_started_factory():
    e = run_started("a", "1.0", "r1")
    assert e.type == StreamEventType.RUN_STARTED
    assert e.data["agent_id"] == "a"


def test_run_completed_factory():
    e = run_completed("a", {"tool_calls": []})
    assert e.type == StreamEventType.RUN_COMPLETED
    assert e.data["trace"] == {"tool_calls": []}


def test_tool_started_factory():
    e = tool_started("search")
    assert e.type == StreamEventType.TOOL_STARTED
    assert e.data["tool_name"] == "search"


def test_message_delta_factory():
    e = message_delta("hello world")
    assert e.type == StreamEventType.MESSAGE_DELTA
    assert e.data["content"] == "hello world"
    assert e.data["type"] == "text"


def test_message_completed_factory():
    e = message_completed({"display": "hi"}, "success")
    assert e.type == StreamEventType.MESSAGE_COMPLETED
    assert e.data["status"] == "success"


def test_cards_event_factory():
    e = cards_event([{"title": "Card 1"}])
    assert e.type == StreamEventType.CARDS
    assert len(e.data["cards"]) == 1


def test_commands_event_factory():
    e = commands_event([{"action": "navigate"}])
    assert e.type == StreamEventType.COMMANDS


def test_route_decision_factory():
    e = route_decision("echo", "agent_id", "dep-1", 42)
    assert e.type == StreamEventType.ROUTE_DECISION
    assert e.data["agent_id"] == "echo"
    assert e.data["traffic_bucket"] == 42


def test_model_call_factory():
    e = model_call_event("openai", "gpt-4o", 100, 200, 0.003, 500.0)
    assert e.type == StreamEventType.MODEL_CALL
    assert e.data["provider"] == "openai"
    assert e.data["input_tokens"] == 100
    assert e.data["cost_usd"] == 0.003


def test_error_event_factory():
    e = error_event("STREAM_ERROR", "something broke")
    assert e.type == StreamEventType.ERROR
    assert e.data["code"] == "STREAM_ERROR"


def test_stream_event_type_values():
    assert StreamEventType.RUN_STARTED == "run.started"
    assert StreamEventType.TOOL_COMPLETED == "tool.completed"
    assert StreamEventType.REPLAY == "replay"
