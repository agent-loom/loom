from __future__ import annotations

import json
import logging
from typing import Any, Protocol, runtime_checkable

import httpx
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


class OpenAICompatibleProvider:
    """Provider that calls any OpenAI-compatible chat completions API."""

    name = "openai_compatible"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        default_model: str = "gpt-4o-mini",
        timeout: float = 30.0,
    ):
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        self._default_model = default_model

    async def chat(
        self,
        messages: list[ModelMessage],
        *,
        model: str = "",
        temperature: float = 0.2,
        max_tokens: int = 1024,
        tools: list[dict[str, Any]] | None = None,
        stop: list[str] | None = None,
    ) -> ModelResponse:
        # Build request body
        body: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
        if stop:
            body["stop"] = stop

        try:
            resp = await self._client.post("/v1/chat/completions", json=body)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return ModelResponse(
                content=f"[LLM API error] {exc.response.status_code}: {exc.response.text[:200]}",
                finish_reason="error",
                model=model or self._default_model,
            )
        except httpx.RequestError as exc:
            return ModelResponse(
                content=f"[LLM connection error] {type(exc).__name__}: {exc}",
                finish_reason="error",
                model=model or self._default_model,
            )

        data = resp.json()
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})

        tool_calls = []
        for tc in message.get("tool_calls", []):
            func = tc.get("function", {})
            try:
                arguments = json.loads(func.get("arguments", "{}"))
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append(
                ToolCall(
                    id=tc.get("id", ""),
                    name=func.get("name", ""),
                    arguments=arguments,
                )
            )

        return ModelResponse(
            content=message.get("content", ""),
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason", "stop"),
            model=data.get("model", ""),
            usage=data.get("usage", {}),
        )

    async def close(self) -> None:
        await self._client.aclose()


class ModelGateway:
    """Routes model calls to the appropriate provider based on agent manifest config."""

    def __init__(self) -> None:
        self._providers: dict[str, ModelProvider] = {}

    @classmethod
    def create_default(cls) -> ModelGateway:
        """Create a gateway pre-loaded with the stub provider for testing/dev."""
        gateway = cls()
        gateway.register(StubModelProvider())
        return gateway

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
