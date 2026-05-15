"""Tests for agent_platform.runtime.model_gateway."""

from __future__ import annotations

from typing import Any

import pytest

from agent_platform.runtime.model_gateway import (
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
# Mock provider for gateway tests
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
        )


# ---------------------------------------------------------------------------
# ModelGateway tests
# ---------------------------------------------------------------------------


class TestModelGateway:
    def test_stub_registered_by_default(self):
        gw = ModelGateway()
        assert "stub" in gw.list_providers()
        provider = gw.get_provider("stub")
        assert provider.name == "stub"

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
        gw = ModelGateway()
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
        gw = ModelGateway()
        gw.register(MockProvider())
        providers = gw.list_providers()
        assert "stub" in providers
        assert "mock" in providers
