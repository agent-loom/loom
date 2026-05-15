from __future__ import annotations

from agent_platform.domain.models import AgentError, AgentRun, AgentRunStatus, ToolCallTrace
from agent_platform.observability.sanitizer import LogSanitizer, TraceSanitizer


def test_sanitize_phone_number():
    assert LogSanitizer.sanitize("call 13812345678 now") == "call 138****5678 now"


def test_sanitize_email():
    assert LogSanitizer.sanitize("email user@example.com") == "email u***@example.com"


def test_sanitize_api_key():
    assert "sk-abcdef1234567890" not in LogSanitizer.sanitize("key=sk-abcdef1234567890")
    assert "***SECRET***" in LogSanitizer.sanitize("key=sk-abcdef1234567890")


def test_sanitize_bearer_token():
    text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    result = LogSanitizer.sanitize(text)
    assert "eyJ" not in result
    assert "Bearer ***SECRET***" in result


def test_sanitize_preserves_normal_text():
    text = "hello world 123"
    assert LogSanitizer.sanitize(text) == text


def test_sanitize_with_known_secrets():
    text = "response contains my-secret-value here"
    result = LogSanitizer.sanitize_with_secrets(text, ["my-secret-value"])
    assert "my-secret-value" not in result
    assert "***SECRET***" in result


def test_sanitize_with_empty_secrets():
    text = "no secrets here"
    assert LogSanitizer.sanitize_with_secrets(text, []) == text
    assert LogSanitizer.sanitize_with_secrets(text, None) == text


def test_trace_sanitizer_sanitize_tool_trace():
    trace = ToolCallTrace(
        tool_name="test_tool",
        status="error",
        error="failed for user@example.com",
    )
    result = TraceSanitizer.sanitize_tool_trace(trace)
    assert "user@example.com" not in result.error
    assert "u***@" in result.error


def test_trace_sanitizer_sanitize_tool_trace_no_error():
    trace = ToolCallTrace(tool_name="test_tool", status="success")
    result = TraceSanitizer.sanitize_tool_trace(trace)
    assert result.error is None


def test_trace_sanitizer_sanitize_run():
    run = AgentRun(
        run_id="run-1",
        request_id="req-1",
        session_id="sess-1",
        agent_id="test",
        agent_version="0.1.0",
        runtime_backend="native",
        status=AgentRunStatus.FAILED,
        latency_ms=100,
        tool_calls=[
            ToolCallTrace(
                tool_name="tool1",
                status="error",
                error="token-abcdefghijklmn leaked",
            ),
        ],
        error=AgentError(
            code="FAIL",
            message="user 13812345678 had an error",
            retryable=False,
        ),
    )
    result = TraceSanitizer.sanitize_run(run)
    assert "13812345678" not in result.error.message
    assert "138****5678" in result.error.message
    assert "***SECRET***" in result.tool_calls[0].error
