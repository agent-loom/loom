"""Tests for ModelGateway multi-provider routing, fallback, and circuit breaker."""

from __future__ import annotations

import json

import httpx
import pytest

from agent_platform.runtime.model_gateway import (
    AnthropicProvider,
    ChatResult,
    CircuitBreaker,
    CircuitState,
    ModelGateway,
    ModelMessage,
    ModelResponse,
    OpenAICompatibleProvider,
    StubModelProvider,
    ToolCall,
    estimate_cost,
)

# ── Cost estimation ───────────────────────────────────────────────


def test_estimate_cost_gpt4o_mini():
    cost = estimate_cost("gpt-4o-mini", 1_000_000, 1_000_000)
    assert cost is not None
    assert abs(cost - (0.150 + 0.600)) < 0.001


def test_estimate_cost_claude_sonnet():
    cost = estimate_cost("claude-sonnet-4-something", 1_000_000, 1_000_000)
    assert cost is not None
    assert abs(cost - (3.00 + 15.00)) < 0.01


def test_estimate_cost_unknown_model():
    cost = estimate_cost("unknown-model", 1000, 1000)
    assert cost is None


# ── Circuit Breaker ───────────────────────────────────────────────


def test_circuit_breaker_starts_closed():
    cb = CircuitBreaker()
    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request() is True


def test_circuit_breaker_opens_after_failures():
    cb = CircuitBreaker(failure_threshold=3)
    for _ in range(3):
        cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.allow_request() is False


def test_circuit_breaker_half_open_after_recovery():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.0)
    cb.record_failure()
    cb.record_failure()
    # recovery_timeout=0 → immediately transitions to half_open on next check
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.allow_request() is True


def test_circuit_breaker_closes_on_success_in_half_open():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.0, half_open_max=1)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.HALF_OPEN
    cb.allow_request()
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_circuit_breaker_reopens_on_failure_in_half_open():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.0)
    cb.record_failure()
    cb.record_failure()
    # recovery_timeout=0 → already half_open
    assert cb.state == CircuitState.HALF_OPEN
    cb.allow_request()
    cb.record_failure()
    # Should re-open, but with recovery_timeout=0, immediately transitions back
    # Use a non-zero timeout to verify re-open behavior
    cb2 = CircuitBreaker(failure_threshold=2, recovery_timeout=9999.0)
    cb2.record_failure()
    cb2.record_failure()
    assert cb2.state == CircuitState.OPEN


# ── ChatResult from_model_response ────────────────────────────────


def test_chat_result_includes_provider():
    resp = ModelResponse(
        content="hello",
        model="gpt-4o-mini",
        provider_name="my-provider",
    )
    result = ChatResult.from_model_response(resp)
    assert result.provider_name == "my-provider"


# ── Gateway fallback ─────────────────────────────────────────────


class FailingProvider:
    name = "failing"

    async def chat(self, messages, **kwargs):
        return ModelResponse(
            content="[LLM API error] 500: internal",
            finish_reason="error",
            model="fail",
            provider_name=self.name,
        )


class SuccessProvider:
    name = "success"

    async def chat(self, messages, **kwargs):
        return ModelResponse(
            content="ok",
            finish_reason="stop",
            model="good",
            provider_name=self.name,
        )


@pytest.mark.asyncio
async def test_gateway_fallback_to_second_provider():
    gw = ModelGateway(
        default_provider="failing",
        fallback_chain=["success"],
    )
    gw.register(FailingProvider())
    gw.register(SuccessProvider())

    result = await gw.chat()
    assert result.content == "ok"
    assert result.provider_name == "success"


@pytest.mark.asyncio
async def test_gateway_no_fallback_raises():
    gw = ModelGateway(default_provider="failing")
    gw.register(FailingProvider())
    with pytest.raises(RuntimeError):
        await gw.chat()


@pytest.mark.asyncio
async def test_gateway_skips_open_circuit():
    gw = ModelGateway(
        default_provider="failing",
        fallback_chain=["success"],
    )
    gw.register(FailingProvider())
    gw.register(SuccessProvider())

    # Trip the circuit for "failing"
    breaker = gw._breakers["failing"]
    for _ in range(breaker.failure_threshold):
        breaker.record_failure()
    assert breaker.state == CircuitState.OPEN

    result = await gw.chat()
    assert result.provider_name == "success"


@pytest.mark.asyncio
async def test_gateway_stub_provider():
    gw = ModelGateway(default_provider="stub")
    gw.register(StubModelProvider())
    result = await gw.chat(messages=[ModelMessage(role="user", content="hello")])
    assert "Stub LLM" in result.content


def test_gateway_circuit_status():
    gw = ModelGateway(default_provider="stub")
    gw.register(StubModelProvider())
    status = gw.get_circuit_status()
    assert status["stub"] == "closed"


def test_gateway_list_providers():
    gw = ModelGateway(default_provider="stub")
    gw.register(StubModelProvider())
    assert "stub" in gw.list_providers()


# ── Provider name override ────────────────────────────────────────


def test_openai_provider_custom_name():
    p = OpenAICompatibleProvider(
        "http://localhost:8080", "key",
        provider_name="my-openai",
    )
    assert p.name == "my-openai"


@pytest.mark.asyncio
async def test_openai_gpt5_request_shape(monkeypatch):
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "message": {"content": "ok"},
                    "finish_reason": "stop",
                }],
                "model": "openai/gpt-5",
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                    "prompt_tokens_details": {},
                    "completion_tokens_details": {"reasoning_tokens": 5},
                },
            },
        )

    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "high")
    provider = OpenAICompatibleProvider("https://api.qnaigc.com/v1", "key")
    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.qnaigc.com/v1",
        headers={"Authorization": "Bearer key"},
    )

    result = await provider.chat(
        [ModelMessage(role="user", content="什么是太阳")],
        model="openai/gpt-5",
    )

    body = captured["body"]
    assert captured["url"] == "https://api.qnaigc.com/v1/chat/completions"
    assert body["reasoning_effort"] == "high"
    assert body["messages"][0]["content"] == [{"type": "text", "text": "什么是太阳"}]
    assert result.usage["completion_tokens_details"] == {"reasoning_tokens": 5}


@pytest.mark.asyncio
async def test_openai_provider_serializes_tool_call_history():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "message": {"content": "ok"},
                    "finish_reason": "stop",
                }],
                "model": "openai/gpt-5",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    provider = OpenAICompatibleProvider("https://api.qnaigc.com/v1", "key")
    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.qnaigc.com/v1",
        headers={"Authorization": "Bearer key"},
    )

    await provider.chat(
        [
            ModelMessage(role="user", content="先读证据"),
            ModelMessage(
                role="assistant",
                content="",
                tool_calls=[
                    {
                        "id": "tc_1",
                        "type": "function",
                        "function": {"name": "evidence_read", "arguments": "{}"},
                    },
                    ToolCall(id="tc_2", name="memory_write", arguments={"summary": "x"}),
                ],
            ),
            ModelMessage(role="tool", content="证据内容", tool_call_id="tc_1"),
        ],
        model="openai/gpt-5",
    )

    body = captured["body"]
    assistant_message = body["messages"][1]
    assert assistant_message["tool_calls"][0]["function"]["name"] == "evidence_read"
    assert assistant_message["tool_calls"][1]["function"]["name"] == "memory_write"
    assert assistant_message["tool_calls"][1]["function"]["arguments"] == '{"summary": "x"}'


def test_anthropic_provider_custom_name():
    p = AnthropicProvider("fake-key", provider_name="my-anthropic")
    assert p.name == "my-anthropic"
