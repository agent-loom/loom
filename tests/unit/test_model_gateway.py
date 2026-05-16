"""Tests for agent_platform.runtime.model_gateway."""

from __future__ import annotations

from typing import Any

import pytest

from agent_platform.observability.metrics import MetricsCollector
from agent_platform.runtime.model_gateway import (
    ChatResult,
    ModelGateway,
    ModelMessage,
    ModelProvider,
    ModelResponse,
    StubModelProvider,
    ToolCall,
)

# ---------------------------------------------------------------------------
# Pydantic model tests
# ---------------------------------------------------------------------------


class TestModelMessage:
    def test_create(self):
        msg = ModelMessage(role="user", content="hello")
        assert msg.role == "user"
        assert msg.content == "hello"


class TestToolCall:
    def test_defaults(self):
        tc = ToolCall(name="search")
        assert tc.id == ""
        assert tc.name == "search"
        assert tc.arguments == {}

    def test_with_arguments(self):
        tc = ToolCall(id="tc-1", name="search", arguments={"q": "test"})
        assert tc.id == "tc-1"
        assert tc.arguments == {"q": "test"}


class TestModelResponse:
    def test_defaults(self):
        resp = ModelResponse()
        assert resp.content == ""
        assert resp.tool_calls == []
        assert resp.finish_reason == "stop"
        assert resp.model == ""
        assert resp.usage == {}

    def test_with_tool_calls(self):
        tc = ToolCall(name="search")
        resp = ModelResponse(
            content="ok",
            tool_calls=[tc],
            finish_reason="tool_use",
            model="gpt-4",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "search"
        assert resp.finish_reason == "tool_use"


# ---------------------------------------------------------------------------
# ChatResult tests
# ---------------------------------------------------------------------------


class TestChatResult:
    def test_defaults(self):
        r = ChatResult()
        assert r.content == ""
        assert r.input_tokens == 0
        assert r.output_tokens == 0
        assert r.estimated_cost_usd == 0.0
        assert r.model == ""
        assert r.tool_calls == []
        assert r.finish_reason == "stop"

    def test_fields_populated(self):
        r = ChatResult(
            content="hello world",
            input_tokens=100,
            output_tokens=50,
            estimated_cost_usd=0.0025,
            model="gpt-4o",
            finish_reason="stop",
        )
        assert r.content == "hello world"
        assert r.input_tokens == 100
        assert r.output_tokens == 50
        assert r.estimated_cost_usd == 0.0025
        assert r.model == "gpt-4o"

    def test_from_model_response(self):
        resp = ModelResponse(
            content="answer",
            prompt_tokens=200,
            completion_tokens=80,
            total_tokens=280,
            estimated_cost_usd=0.005,
            model="gpt-4o-mini",
            finish_reason="stop",
            tool_calls=[ToolCall(name="calc", arguments={"x": 1})],
        )
        cr = ChatResult.from_model_response(resp)
        assert cr.content == "answer"
        assert cr.input_tokens == 200
        assert cr.output_tokens == 80
        assert cr.estimated_cost_usd == 0.005
        assert cr.model == "gpt-4o-mini"
        assert cr.finish_reason == "stop"
        assert len(cr.tool_calls) == 1
        assert cr.tool_calls[0].name == "calc"

    def test_from_model_response_null_cost(self):
        resp = ModelResponse(content="ok", estimated_cost_usd=None)
        cr = ChatResult.from_model_response(resp)
        assert cr.estimated_cost_usd == 0.0


# ---------------------------------------------------------------------------
# StubModelProvider tests
# ---------------------------------------------------------------------------


class TestStubModelProvider:
    @pytest.mark.asyncio
    async def test_returns_echo_response(self):
        provider = StubModelProvider()
        messages = [
            ModelMessage(role="system", content="You are helpful."),
            ModelMessage(role="user", content="What is 2+2?"),
        ]
        resp = await provider.chat(messages, model="stub")

        assert "[Stub LLM] Received: What is 2+2?" == resp.content
        assert resp.finish_reason == "stop"
        assert resp.model == "stub"
        assert resp.usage == {"prompt_tokens": 0, "completion_tokens": 0}
        assert resp.tool_calls == []

    @pytest.mark.asyncio
    async def test_echoes_last_user_message(self):
        provider = StubModelProvider()
        messages = [
            ModelMessage(role="user", content="first"),
            ModelMessage(role="assistant", content="reply"),
            ModelMessage(role="user", content="second"),
        ]
        resp = await provider.chat(messages, model="test")
        assert "second" in resp.content

    @pytest.mark.asyncio
    async def test_empty_messages_returns_empty_user_echo(self):
        provider = StubModelProvider()
        resp = await provider.chat([], model="stub")
        assert resp.content == "[Stub LLM] Received: "

    @pytest.mark.asyncio
    async def test_no_user_messages_returns_empty_echo(self):
        provider = StubModelProvider()
        messages = [ModelMessage(role="system", content="system only")]
        resp = await provider.chat(messages, model="stub")
        assert resp.content == "[Stub LLM] Received: "

    def test_provider_name(self):
        assert StubModelProvider.name == "stub"

    def test_satisfies_protocol(self):
        assert isinstance(StubModelProvider(), ModelProvider)


# ---------------------------------------------------------------------------
# Mock providers for gateway tests
# ---------------------------------------------------------------------------


class MockProvider:
    name = "mock"

    async def chat(
        self,
        messages: list[ModelMessage],
        *,
        model: str = "mock-model",
        temperature: float = 0.2,
        max_tokens: int = 1024,
        tools: list[dict[str, Any]] | None = None,
        stop: list[str] | None = None,
    ) -> ModelResponse:
        return ModelResponse(
            content=f"mock reply from {model}",
            model=model,
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            estimated_cost_usd=0.001,
        )


class AltProvider:
    """A second provider to test multi-provider routing."""

    name = "alt"

    async def chat(
        self,
        messages: list[ModelMessage],
        *,
        model: str = "alt-model",
        temperature: float = 0.2,
        max_tokens: int = 1024,
        tools: list[dict[str, Any]] | None = None,
        stop: list[str] | None = None,
    ) -> ModelResponse:
        return ModelResponse(
            content=f"alt reply from {model}",
            model=model,
            finish_reason="stop",
            prompt_tokens=20,
            completion_tokens=10,
        )


# ---------------------------------------------------------------------------
# ModelGateway tests
# ---------------------------------------------------------------------------


class TestModelGateway:
    def test_stub_registered_by_create_default(self):
        gw = ModelGateway.create_default()
        assert "stub" in gw.list_providers()
        provider = gw.get_provider("stub")
        assert provider.name == "stub"

    def test_empty_by_default(self):
        gw = ModelGateway()
        assert gw.list_providers() == []

    def test_register_custom_provider(self):
        gw = ModelGateway()
        mock = MockProvider()
        gw.register(mock)
        assert "mock" in gw.list_providers()
        assert gw.get_provider("mock") is mock

    def test_raises_on_unknown_provider(self):
        gw = ModelGateway()
        with pytest.raises(LookupError, match="model provider not found: nonexistent"):
            gw.get_provider("nonexistent")

    @pytest.mark.asyncio
    async def test_routes_to_registered_provider(self):
        gw = ModelGateway()
        gw.register(MockProvider())

        messages = [ModelMessage(role="user", content="hi")]
        resp = await gw.chat("mock", messages, model="gpt-4o")

        assert resp.content == "mock reply from gpt-4o"
        assert resp.model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_routes_to_stub(self):
        gw = ModelGateway.create_default()
        messages = [ModelMessage(role="user", content="testing")]
        resp = await gw.chat("stub", messages, model="stub")

        assert "[Stub LLM] Received: testing" == resp.content

    @pytest.mark.asyncio
    async def test_chat_raises_on_unknown_provider(self):
        gw = ModelGateway()
        with pytest.raises(LookupError):
            await gw.chat(
                "does_not_exist",
                [ModelMessage(role="user", content="hi")],
                model="m",
            )

    def test_list_providers(self):
        gw = ModelGateway.create_default()
        gw.register(MockProvider())
        providers = gw.list_providers()
        assert "stub" in providers
        assert "mock" in providers


# ---------------------------------------------------------------------------
# ChatResult fields populated correctly
# ---------------------------------------------------------------------------


class TestChatResultFromGateway:
    @pytest.mark.asyncio
    async def test_chat_returns_chat_result(self):
        gw = ModelGateway()
        gw.register(MockProvider())

        result = await gw.chat("mock", [ModelMessage(role="user", content="hi")], model="gpt-4o")
        assert isinstance(result, ChatResult)
        assert result.content == "mock reply from gpt-4o"
        assert result.input_tokens == 10
        assert result.output_tokens == 5
        assert result.estimated_cost_usd == 0.001
        assert result.model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_stub_returns_zero_tokens(self):
        gw = ModelGateway.create_default()
        result = await gw.chat("stub", [ModelMessage(role="user", content="x")], model="stub")
        assert isinstance(result, ChatResult)
        assert result.input_tokens == 0
        assert result.output_tokens == 0
        assert result.estimated_cost_usd == 0.0


# ---------------------------------------------------------------------------
# Multi-provider routing
# ---------------------------------------------------------------------------


class TestMultiProviderRouting:
    @pytest.mark.asyncio
    async def test_routes_to_correct_provider(self):
        gw = ModelGateway()
        gw.register(MockProvider())
        gw.register(AltProvider())

        mock_result = await gw.chat("mock", [ModelMessage(role="user", content="hi")], model="m1")
        assert mock_result.content == "mock reply from m1"

        alt_result = await gw.chat("alt", [ModelMessage(role="user", content="hi")], model="m2")
        assert alt_result.content == "alt reply from m2"

    @pytest.mark.asyncio
    async def test_model_override(self):
        gw = ModelGateway()
        gw.register(MockProvider())

        r1 = await gw.chat("mock", [ModelMessage(role="user", content="hi")], model="model-a")
        assert r1.model == "model-a"

        r2 = await gw.chat("mock", [ModelMessage(role="user", content="hi")], model="model-b")
        assert r2.model == "model-b"


# ---------------------------------------------------------------------------
# Default provider fallback
# ---------------------------------------------------------------------------


class TestDefaultProviderFallback:
    @pytest.mark.asyncio
    async def test_uses_default_when_no_provider_name(self):
        gw = ModelGateway(default_provider="mock")
        gw.register(MockProvider())

        result = await gw.chat(messages=[ModelMessage(role="user", content="hi")], model="m")
        assert result.content == "mock reply from m"

    @pytest.mark.asyncio
    async def test_explicit_provider_overrides_default(self):
        gw = ModelGateway(default_provider="mock")
        gw.register(MockProvider())
        gw.register(AltProvider())

        result = await gw.chat("alt", [ModelMessage(role="user", content="hi")], model="m")
        assert result.content == "alt reply from m"

    @pytest.mark.asyncio
    async def test_raises_when_no_provider_and_no_default(self):
        gw = ModelGateway()
        gw.register(MockProvider())

        with pytest.raises(LookupError, match="no provider_name supplied"):
            await gw.chat(messages=[ModelMessage(role="user", content="hi")], model="m")

    @pytest.mark.asyncio
    async def test_create_default_sets_stub_as_default(self):
        gw = ModelGateway.create_default()
        result = await gw.chat(messages=[ModelMessage(role="user", content="hi")], model="stub")
        assert "[Stub LLM]" in result.content


# ---------------------------------------------------------------------------
# Metrics collector integration
# ---------------------------------------------------------------------------


class TestMetricsIntegration:
    @pytest.mark.asyncio
    async def test_records_metrics_on_chat(self):
        mc = MetricsCollector()
        gw = ModelGateway(default_provider="mock", metrics_collector=mc)
        gw.register(MockProvider())

        await gw.chat(messages=[ModelMessage(role="user", content="hi")], model="test-model")

        prom = mc.format_prometheus()
        assert "llm_calls_total" in prom
        assert "llm_input_tokens_total" in prom
        assert "llm_output_tokens_total" in prom
        assert "llm_cost_usd_total" in prom
        assert 'provider="mock"' in prom
        assert 'model="test-model"' in prom

    @pytest.mark.asyncio
    async def test_no_metrics_without_collector(self):
        gw = ModelGateway(default_provider="mock")
        gw.register(MockProvider())
        result = await gw.chat(messages=[ModelMessage(role="user", content="hi")], model="m")
        assert isinstance(result, ChatResult)

    @pytest.mark.asyncio
    async def test_skips_cost_metric_when_zero(self):
        mc = MetricsCollector()
        gw = ModelGateway.create_default(metrics_collector=mc)

        await gw.chat(messages=[ModelMessage(role="user", content="hi")], model="stub")

        prom = mc.format_prometheus()
        assert "llm_calls_total" in prom
        assert "llm_cost_usd_total" not in prom
