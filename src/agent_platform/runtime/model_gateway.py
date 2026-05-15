from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ModelMessage(BaseModel):
    role: str
    content: str


class ToolCall(BaseModel):
    id: str = ""
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ModelResponse(BaseModel):
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str = "stop"
    model: str = ""
    usage: dict[str, int] = Field(default_factory=dict)


@runtime_checkable
class ModelProvider(Protocol):
    name: str

    async def chat(
        self,
        messages: list[ModelMessage],
        *,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        tools: list[dict[str, Any]] | None = None,
        stop: list[str] | None = None,
    ) -> ModelResponse: ...


class StubModelProvider:
    """Stub provider for testing and development without real LLM access."""

    name = "stub"

    async def chat(
        self,
        messages: list[ModelMessage],
        *,
        model: str = "stub",
        temperature: float = 0.2,
        max_tokens: int = 1024,
        tools: list[dict[str, Any]] | None = None,
        stop: list[str] | None = None,
    ) -> ModelResponse:
        last_user = ""
        for m in reversed(messages):
            if m.role == "user":
                last_user = m.content
                break
        return ModelResponse(
            content=f"[Stub LLM] Received: {last_user}",
            finish_reason="stop",
            model=model,
            usage={"prompt_tokens": 0, "completion_tokens": 0},
        )


class ModelGateway:
    """Routes model calls to the appropriate provider based on agent manifest config."""

    def __init__(self) -> None:
        self._providers: dict[str, ModelProvider] = {}
        self.register(StubModelProvider())

    def register(self, provider: ModelProvider) -> None:
        self._providers[provider.name] = provider

    def get_provider(self, name: str) -> ModelProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise LookupError(f"model provider not found: {name}") from exc

    async def chat(
        self,
        provider_name: str,
        messages: list[ModelMessage],
        *,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        provider = self.get_provider(provider_name)
        return await provider.chat(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
        )

    def list_providers(self) -> list[str]:
        return list(self._providers.keys())
