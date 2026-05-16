"""Tests for the OpenTelemetry tracing integration (no-op path).

All tests exercise the code path where ``opentelemetry`` is **not** installed,
verifying that instrumentation call-sites degrade gracefully.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch

import pytest

from agent_platform.observability.instrumentation import (
    instrument_agent_run,
    instrument_route,
    instrument_tool_call,
)
from agent_platform.observability.tracing import (
    NoOpSpan,
    NoOpTracer,
    configure_tracing,
    get_tracer,
    traced,
)

# ---------------------------------------------------------------------------
# NoOpSpan
# ---------------------------------------------------------------------------


class TestNoOpSpan:
    """Ensure NoOpSpan implements the expected interface without errors."""

    def test_context_manager(self) -> None:
        span = NoOpSpan()
        with span as s:
            assert s is span

    def test_set_attribute(self) -> None:
        span = NoOpSpan()
        span.set_attribute("key", "value")  # should not raise

    def test_set_status(self) -> None:
        span = NoOpSpan()
        span.set_status("ERROR", "boom")

    def test_record_exception(self) -> None:
        span = NoOpSpan()
        span.record_exception(RuntimeError("oops"))

    def test_add_event(self) -> None:
        span = NoOpSpan()
        span.add_event("checkpoint", {"step": 1})

    def test_exit_suppresses_nothing(self) -> None:
        """__exit__ should not suppress exceptions."""
        span = NoOpSpan()
        with pytest.raises(ValueError, match="test"):
            with span:
                raise ValueError("test")


# ---------------------------------------------------------------------------
# NoOpTracer
# ---------------------------------------------------------------------------


class TestNoOpTracer:
    def test_start_as_current_span_returns_noop(self) -> None:
        tracer = NoOpTracer()
        span = tracer.start_as_current_span("test_span")
        assert isinstance(span, NoOpSpan)

    def test_as_context_manager(self) -> None:
        tracer = NoOpTracer()
        with tracer.start_as_current_span("op") as span:
            assert isinstance(span, NoOpSpan)
            span.set_attribute("x", 1)


# ---------------------------------------------------------------------------
# get_tracer
# ---------------------------------------------------------------------------


class TestGetTracer:
    def test_returns_noop_when_otel_unavailable(self) -> None:
        with patch("agent_platform.observability.tracing.OTEL_AVAILABLE", False):
            t = get_tracer("test")
            assert isinstance(t, NoOpTracer)

    def test_noop_tracer_span_is_usable(self) -> None:
        with patch("agent_platform.observability.tracing.OTEL_AVAILABLE", False):
            t = get_tracer("test")
            with t.start_as_current_span("span") as s:
                s.set_attribute("k", "v")
                s.add_event("ev")


# ---------------------------------------------------------------------------
# @traced decorator
# ---------------------------------------------------------------------------


class TestTracedDecorator:
    def test_decorated_async_function_runs(self) -> None:
        @traced(name="custom_span")
        async def add(a: int, b: int) -> int:
            return a + b

        result = asyncio.get_event_loop().run_until_complete(add(2, 3))
        assert result == 5

    def test_decorated_function_preserves_exception(self) -> None:
        @traced()
        async def fail() -> None:
            raise RuntimeError("expected")

        with pytest.raises(RuntimeError, match="expected"):
            asyncio.get_event_loop().run_until_complete(fail())

    def test_decorated_function_preserves_name(self) -> None:
        @traced()
        async def my_func() -> None:
            pass

        assert my_func.__name__ == "my_func"


# ---------------------------------------------------------------------------
# Instrumentation helpers
# ---------------------------------------------------------------------------


class TestInstrumentationHelpers:
    """All helpers must work with NoOpSpan without raising."""

    def test_instrument_agent_run(self) -> None:
        span = NoOpSpan()
        instrument_agent_run(span, agent_id="a1", run_id="r1", backend_name="native")

    def test_instrument_tool_call(self) -> None:
        span = NoOpSpan()
        instrument_tool_call(span, tool_name="search", status="success")

    def test_instrument_route(self) -> None:
        span = NoOpSpan()
        instrument_route(span, agent_id="a1", reason="manifest")


# ---------------------------------------------------------------------------
# configure_tracing
# ---------------------------------------------------------------------------


class TestConfigureTracing:
    def test_logs_warning_when_otel_unavailable(self, caplog: pytest.LogCaptureFixture) -> None:
        with patch("agent_platform.observability.tracing.OTEL_AVAILABLE", False):
            with caplog.at_level(logging.WARNING, logger="agent_platform.observability.tracing"):
                configure_tracing("test-service")
            assert any("not installed" in r.message for r in caplog.records)
