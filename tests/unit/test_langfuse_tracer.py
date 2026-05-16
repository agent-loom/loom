"""Tests for LangfuseTracer — src/agent_platform/observability/langfuse_tracer.py"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent_platform.observability.langfuse_tracer import (
    LangfuseTracer,
    _NoOpSpan,
    _NoOpTrace,
)


class TestNoOpFallback:
    def test_tracer_disabled_when_no_keys(self):
        tracer = LangfuseTracer(public_key=None, secret_key=None)
        assert tracer.enabled is False

    def test_tracer_disabled_when_empty_keys(self):
        tracer = LangfuseTracer(public_key="", secret_key="")
        assert tracer.enabled is False

    def test_trace_returns_noop_when_disabled(self):
        tracer = LangfuseTracer(public_key=None, secret_key=None)
        result = tracer.trace(name="test")
        assert isinstance(result, _NoOpTrace)

    def test_generation_is_noop_when_disabled(self):
        tracer = LangfuseTracer(public_key=None, secret_key=None)
        tracer.generation(
            None,
            name="gen",
            model="gpt-4",
            input="hello",
            output="world",
        )

    def test_span_returns_noop_when_disabled(self):
        tracer = LangfuseTracer(public_key=None, secret_key=None)
        span = tracer.span(None, name="test-span")
        assert isinstance(span, _NoOpSpan)

    def test_score_is_noop_when_disabled(self):
        tracer = LangfuseTracer(public_key=None, secret_key=None)
        tracer.score(None, name="quality", value=0.9)

    def test_flush_is_noop_when_disabled(self):
        tracer = LangfuseTracer(public_key=None, secret_key=None)
        tracer.flush()

    @pytest.mark.asyncio
    async def test_shutdown_is_safe_when_disabled(self):
        tracer = LangfuseTracer(public_key=None, secret_key=None)
        await tracer.shutdown()

    @pytest.mark.asyncio
    async def test_close_is_safe_when_disabled(self):
        tracer = LangfuseTracer(public_key=None, secret_key=None)
        await tracer.close()

    @pytest.mark.asyncio
    async def test_health_check_returns_false_when_disabled(self):
        tracer = LangfuseTracer(public_key=None, secret_key=None)
        assert await tracer.health_check() is False


class TestNoOpTrace:
    def test_generation_returns_noop_span(self):
        t = _NoOpTrace()
        result = t.generation(name="gen")
        assert isinstance(result, _NoOpSpan)

    def test_span_returns_noop_span(self):
        t = _NoOpTrace()
        result = t.span(name="s")
        assert isinstance(result, _NoOpSpan)

    def test_score_is_callable(self):
        t = _NoOpTrace()
        t.score(name="q", value=0.5)


class TestNoOpSpan:
    def test_end_is_callable(self):
        s = _NoOpSpan()
        s.end()

    def test_update_is_callable(self):
        s = _NoOpSpan()
        s.update(output="result")


class TestWithMockedSDK:
    @patch("agent_platform.observability.langfuse_tracer._langfuse_available", True)
    @patch("agent_platform.observability.langfuse_tracer.Langfuse")
    def test_tracer_enabled_with_keys(self, mock_langfuse_cls):
        mock_client = MagicMock()
        mock_langfuse_cls.return_value = mock_client

        tracer = LangfuseTracer(
            public_key="pk-test",
            secret_key="sk-test",
            host="https://langfuse.example.com",
        )
        assert tracer.enabled is True
        mock_langfuse_cls.assert_called_once_with(
            public_key="pk-test",
            secret_key="sk-test",
            host="https://langfuse.example.com",
        )

    @patch("agent_platform.observability.langfuse_tracer._langfuse_available", True)
    @patch("agent_platform.observability.langfuse_tracer.Langfuse")
    def test_trace_delegates_to_client(self, mock_langfuse_cls):
        mock_client = MagicMock()
        mock_langfuse_cls.return_value = mock_client
        mock_trace = MagicMock()
        mock_client.trace.return_value = mock_trace

        tracer = LangfuseTracer(public_key="pk", secret_key="sk")
        result = tracer.trace(
            name="my-trace",
            session_id="sess-1",
            user_id="user-1",
            metadata={"key": "val"},
            tags=["test"],
        )

        assert result is mock_trace
        mock_client.trace.assert_called_once_with(
            name="my-trace",
            session_id="sess-1",
            user_id="user-1",
            metadata={"key": "val"},
            tags=["test"],
        )

    @patch("agent_platform.observability.langfuse_tracer._langfuse_available", True)
    @patch("agent_platform.observability.langfuse_tracer.Langfuse")
    def test_generation_delegates_to_trace(self, mock_langfuse_cls):
        mock_client = MagicMock()
        mock_langfuse_cls.return_value = mock_client

        tracer = LangfuseTracer(public_key="pk", secret_key="sk")
        mock_trace = MagicMock()
        mock_gen = MagicMock()
        mock_trace.generation.return_value = mock_gen

        tracer.generation(
            mock_trace,
            name="llm-call",
            model="claude-sonnet-4-6",
            input="hello",
            output="hi there",
            usage={"input_tokens": 10, "output_tokens": 5},
        )
        mock_trace.generation.assert_called_once()

    @patch("agent_platform.observability.langfuse_tracer._langfuse_available", True)
    @patch("agent_platform.observability.langfuse_tracer.Langfuse")
    def test_span_delegates_to_trace(self, mock_langfuse_cls):
        mock_client = MagicMock()
        mock_langfuse_cls.return_value = mock_client

        tracer = LangfuseTracer(public_key="pk", secret_key="sk")
        mock_trace = MagicMock()
        mock_span = MagicMock()
        mock_trace.span.return_value = mock_span

        result = tracer.span(mock_trace, name="my-span", input="data")
        assert result is mock_span

    @patch("agent_platform.observability.langfuse_tracer._langfuse_available", True)
    @patch("agent_platform.observability.langfuse_tracer.Langfuse")
    def test_score_delegates_to_trace(self, mock_langfuse_cls):
        mock_client = MagicMock()
        mock_langfuse_cls.return_value = mock_client

        tracer = LangfuseTracer(public_key="pk", secret_key="sk")
        mock_trace = MagicMock()

        tracer.score(mock_trace, name="accuracy", value=0.95, comment="good")
        mock_trace.score.assert_called_once_with(
            name="accuracy", value=0.95, comment="good",
        )

    @patch("agent_platform.observability.langfuse_tracer._langfuse_available", True)
    @patch("agent_platform.observability.langfuse_tracer.Langfuse")
    def test_flush_delegates_to_client(self, mock_langfuse_cls):
        mock_client = MagicMock()
        mock_langfuse_cls.return_value = mock_client

        tracer = LangfuseTracer(public_key="pk", secret_key="sk")
        tracer.flush()
        mock_client.flush.assert_called_once()

    @patch("agent_platform.observability.langfuse_tracer._langfuse_available", True)
    @patch("agent_platform.observability.langfuse_tracer.Langfuse")
    def test_generation_noop_on_noop_trace(self, mock_langfuse_cls):
        mock_client = MagicMock()
        mock_langfuse_cls.return_value = mock_client

        tracer = LangfuseTracer(public_key="pk", secret_key="sk")
        noop = _NoOpTrace()
        tracer.generation(noop, name="gen", model="gpt-4")

    @patch("agent_platform.observability.langfuse_tracer._langfuse_available", True)
    @patch("agent_platform.observability.langfuse_tracer.Langfuse")
    def test_span_returns_noop_on_noop_trace(self, mock_langfuse_cls):
        mock_client = MagicMock()
        mock_langfuse_cls.return_value = mock_client

        tracer = LangfuseTracer(public_key="pk", secret_key="sk")
        noop = _NoOpTrace()
        result = tracer.span(noop, name="s")
        assert isinstance(result, _NoOpSpan)

    @patch("agent_platform.observability.langfuse_tracer._langfuse_available", True)
    @patch("agent_platform.observability.langfuse_tracer.Langfuse")
    @pytest.mark.asyncio
    async def test_health_check_delegates(self, mock_langfuse_cls):
        mock_client = MagicMock()
        mock_langfuse_cls.return_value = mock_client
        mock_client.auth_check.return_value = True

        tracer = LangfuseTracer(public_key="pk", secret_key="sk")
        result = await tracer.health_check()
        assert result is True
        mock_client.auth_check.assert_called_once()

    @patch("agent_platform.observability.langfuse_tracer._langfuse_available", True)
    @patch("agent_platform.observability.langfuse_tracer.Langfuse")
    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_error(self, mock_langfuse_cls):
        mock_client = MagicMock()
        mock_langfuse_cls.return_value = mock_client
        mock_client.auth_check.side_effect = Exception("connection refused")

        tracer = LangfuseTracer(public_key="pk", secret_key="sk")
        result = await tracer.health_check()
        assert result is False


class TestInitFailure:
    @patch("agent_platform.observability.langfuse_tracer._langfuse_available", True)
    @patch("agent_platform.observability.langfuse_tracer.Langfuse")
    def test_init_exception_disables_tracer(self, mock_langfuse_cls):
        mock_langfuse_cls.side_effect = Exception("init failed")
        tracer = LangfuseTracer(public_key="pk", secret_key="sk")
        assert tracer.enabled is False

    @patch("agent_platform.observability.langfuse_tracer._langfuse_available", False)
    def test_sdk_not_available_disables_tracer(self):
        tracer = LangfuseTracer(public_key="pk", secret_key="sk")
        assert tracer.enabled is False
