"""Tests for the observability layer: structured logging, metrics, and Prometheus output."""

from __future__ import annotations

import json
import logging

from agent_platform.observability.logging_config import JSONFormatter, setup_logging
from agent_platform.observability.metrics import MetricsCollector

# ── JSON Formatter ──────────────────────────────────────────────────


class TestJSONFormatter:
    def test_output_is_valid_json(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello world",
            args=None,
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)

        assert data["level"] == "INFO"
        assert data["logger"] == "test.logger"
        assert data["message"] == "hello world"
        assert "timestamp" in data

    def test_extra_context_is_included(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="",
            lineno=0,
            msg="with context",
            args=None,
            exc_info=None,
        )
        record.request_id = "req_123"
        record.agent_id = "myj"

        output = formatter.format(record)
        data = json.loads(output)

        assert data["request_id"] == "req_123"
        assert data["agent_id"] == "myj"

    def test_exception_info_is_included(self):
        formatter = JSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys as _sys

            exc_info = _sys.exc_info()
            record = logging.LogRecord(
                name="test",
                level=logging.ERROR,
                pathname="",
                lineno=0,
                msg="failure",
                args=None,
                exc_info=exc_info,
            )

        output = formatter.format(record)
        data = json.loads(output)

        assert "exception" in data
        assert "ValueError" in data["exception"]


# ── setup_logging ───────────────────────────────────────────────────


class TestSetupLogging:
    def test_does_not_crash(self):
        # Calling setup_logging should not raise.
        setup_logging(log_level=logging.DEBUG)

    def test_idempotent(self):
        """Calling setup_logging twice should not duplicate handlers."""
        root = logging.getLogger()
        before = len(root.handlers)
        setup_logging()
        setup_logging()
        after = len(root.handlers)
        # At most one new handler should have been added.
        assert after <= before + 1


# ── MetricsCollector ────────────────────────────────────────────────


class TestMetricsCollector:
    def test_counter_increment(self):
        mc = MetricsCollector()
        mc.inc_counter("agent_requests_total", {"agent_id": "a1", "status": "ok"})
        mc.inc_counter("agent_requests_total", {"agent_id": "a1", "status": "ok"})

        output = mc.format_prometheus()
        assert 'agent_requests_total{agent_id="a1",status="ok"} 2' in output

    def test_record_request_convenience(self):
        mc = MetricsCollector()
        mc.record_request("bot1", "ok")
        mc.record_request("bot1", "error")
        mc.record_request("bot1", "ok")

        output = mc.format_prometheus()
        assert 'agent_requests_total{agent_id="bot1",status="ok"} 2' in output
        assert 'agent_requests_total{agent_id="bot1",status="error"} 1' in output

    def test_gauge_set_and_output(self):
        mc = MetricsCollector()
        mc.set_active_sessions(5)

        output = mc.format_prometheus()
        assert "active_sessions 5" in output

    def test_gauge_increment(self):
        mc = MetricsCollector()
        mc.inc_gauge("active_sessions", value=3.0)
        mc.inc_gauge("active_sessions", value=-1.0)

        output = mc.format_prometheus()
        assert "active_sessions 2" in output

    def test_observe_and_summary(self):
        mc = MetricsCollector()
        for v in [0.1, 0.2, 0.3, 0.4, 0.5]:
            mc.observe("agent_request_duration_seconds", v, {"agent_id": "a1"})

        output = mc.format_prometheus()
        # Should contain quantile lines, _count, and _sum.
        assert "quantile=" in output
        assert "agent_request_duration_seconds_count" in output
        assert "agent_request_duration_seconds_sum" in output

    def test_record_duration_convenience(self):
        mc = MetricsCollector()
        mc.record_duration("a1", 0.25)
        mc.record_duration("a1", 0.75)

        output = mc.format_prometheus()
        assert 'agent_request_duration_seconds_count{agent_id="a1"} 2' in output

    def test_tool_calls_counter(self):
        mc = MetricsCollector()
        mc.record_tool_call("search", "ok")
        mc.record_tool_call("search", "ok")
        mc.record_tool_call("search", "error")

        output = mc.format_prometheus()
        assert 'tool_calls_total{status="ok",tool_name="search"} 2' in output
        assert 'tool_calls_total{status="error",tool_name="search"} 1' in output


# ── Prometheus format structure ─────────────────────────────────────


class TestFormatPrometheus:
    def test_empty_collector_returns_empty(self):
        mc = MetricsCollector()
        output = mc.format_prometheus()
        # Should not contain any metric lines (only possible trailing newline).
        assert output.strip() == ""

    def test_type_and_help_annotations(self):
        mc = MetricsCollector()
        mc.inc_counter("agent_requests_total", {"agent_id": "a1", "status": "ok"})

        output = mc.format_prometheus()
        assert "# HELP agent_requests_total" in output
        assert "# TYPE agent_requests_total counter" in output

    def test_summary_type_annotation(self):
        mc = MetricsCollector()
        mc.observe("agent_request_duration_seconds", 0.1, {"agent_id": "a1"})

        output = mc.format_prometheus()
        assert "# TYPE agent_request_duration_seconds summary" in output

    def test_gauge_type_annotation(self):
        mc = MetricsCollector()
        mc.set_gauge("active_sessions", 3.0)

        output = mc.format_prometheus()
        assert "# TYPE active_sessions gauge" in output
