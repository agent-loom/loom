"""TraceEvent 结构化追踪事件模型测试。"""

from datetime import UTC, datetime

from agent_platform.domain.models import (
    AgentRun,
    AgentRunStatus,
    TraceEvent,
    TraceEventType,
)


def test_trace_event_defaults():
    ev = TraceEvent(type=TraceEventType.ROUTE_DECISION)
    assert ev.type == TraceEventType.ROUTE_DECISION
    assert isinstance(ev.timestamp, datetime)
    assert ev.duration_ms is None
    assert ev.data == {}


def test_trace_event_with_data():
    ev = TraceEvent(
        type=TraceEventType.MODEL_CALL,
        duration_ms=150,
        data={"model": "gpt-4o", "tokens": 500},
    )
    assert ev.duration_ms == 150
    assert ev.data["model"] == "gpt-4o"


def test_trace_event_types():
    expected = {
        "route_decision", "context_build", "policy_check",
        "model_call", "tool_call", "response_build", "error", "custom",
    }
    assert {t.value for t in TraceEventType} == expected


def test_agent_run_with_trace_events():
    events = [
        TraceEvent(
            type=TraceEventType.ROUTE_DECISION,
            duration_ms=5,
            data={"backend": "native"},
        ),
        TraceEvent(
            type=TraceEventType.MODEL_CALL,
            duration_ms=200,
            data={"model": "gpt-4o"},
        ),
    ]
    run = AgentRun(
        run_id="run_test",
        agent_id="test-agent",
        agent_version="1.0",
        runtime_backend="native",
        status=AgentRunStatus.SUCCEEDED,
        latency_ms=210,
        trace_events=events,
    )
    assert len(run.trace_events) == 2
    assert run.trace_events[0].type == TraceEventType.ROUTE_DECISION
    assert run.trace_events[1].data["model"] == "gpt-4o"


def test_agent_run_trace_events_serialization():
    run = AgentRun(
        run_id="run_ser",
        agent_id="test-agent",
        agent_version="1.0",
        runtime_backend="native",
        status=AgentRunStatus.SUCCEEDED,
        latency_ms=100,
        trace_events=[
            TraceEvent(
                type=TraceEventType.CONTEXT_BUILD,
                duration_ms=10,
                data={"has_session": True},
            ),
        ],
    )
    data = run.model_dump(mode="json")
    assert len(data["trace_events"]) == 1
    assert data["trace_events"][0]["type"] == "context_build"
    assert data["trace_events"][0]["duration_ms"] == 10


def test_agent_run_default_empty_trace_events():
    run = AgentRun(
        run_id="run_empty",
        agent_id="test-agent",
        agent_version="1.0",
        runtime_backend="native",
        status=AgentRunStatus.SUCCEEDED,
        latency_ms=50,
    )
    assert run.trace_events == []


def test_trace_event_timestamp_is_utc():
    ev = TraceEvent(type=TraceEventType.CUSTOM)
    assert ev.timestamp.tzinfo is not None
    assert ev.timestamp.tzinfo == UTC
