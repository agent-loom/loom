"""模型网关层，统一管理多种 LLM 提供商的调用与路由。"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from agent_platform.observability.metrics import MetricsCollector

logger = logging.getLogger(__name__)


class ModelMessage(BaseModel):
    """模型对话消息，包含角色、内容及可选的工具调用信息。"""

    tool_calls: list[Any] | None = None
    tool_call_id: str | None = None
    role: str
    content: str


class ToolCall(BaseModel):
    """模型返回的工具调用请求。"""

    id: str = ""
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ModelResponse(BaseModel):
    """模型推理响应，包含文本内容、工具调用和用量统计。"""

    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str = "stop"
    model: str = ""
    usage: dict[str, int] = Field(default_factory=dict)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float | None = None


class ChatResult(BaseModel):
    """High-level result from ModelGateway.chat(), exposing content, token counts and cost."""

    content: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    model: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str = "stop"

    @classmethod
    def from_model_response(cls, resp: ModelResponse) -> ChatResult:
        return cls(
            content=resp.content,
            input_tokens=resp.prompt_tokens,
            output_tokens=resp.completion_tokens,
            estimated_cost_usd=resp.estimated_cost_usd or 0.0,
            model=resp.model,
            tool_calls=resp.tool_calls,
            finish_reason=resp.finish_reason,
        )


@runtime_checkable
class ModelProvider(Protocol):
    """模型提供商协议，定义统一的 chat 接口。"""

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
        """返回固定格式的存根响应，不调用真实 LLM。"""
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
        """初始化 OpenAI 兼容提供商，配置 HTTP 客户端。"""
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
        """调用 OpenAI 兼容 API 进行对话补全。"""
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

        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)
        
        # Super simple cost estimator (example pricing for typical models)
        _cost_usd: float | None = None
        _used_model = data.get("model", model or self._default_model)
        if "gpt-4o-mini" in _used_model:
            _cost_usd = (
                (prompt_tokens / 1_000_000) * 0.150
                + (completion_tokens / 1_000_000) * 0.600
            )
        elif "gpt-4o" in _used_model:
            _cost_usd = (prompt_tokens / 1_000_000) * 5.00 + (completion_tokens / 1_000_000) * 15.00
        elif "claude-3-5" in _used_model:
            _cost_usd = (prompt_tokens / 1_000_000) * 3.00 + (completion_tokens / 1_000_000) * 15.00

        return ModelResponse(
            content=message.get("content", ""),
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason", "stop"),
            model=_used_model,
            usage=usage,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=_cost_usd,
        )

    async def close(self) -> None:
        """关闭底层 HTTP 客户端连接。"""
        await self._client.aclose()


class ModelGateway:
    """Routes model calls to the appropriate provider based on agent manifest config."""

    def __init__(
        self,
        *,
        default_provider: str | None = None,
        metrics_collector: MetricsCollector | None = None,
    ) -> None:
        self._providers: dict[str, ModelProvider] = {}
        self._default_provider = default_provider
        self._metrics: MetricsCollector | None = metrics_collector

    @classmethod
    def create_default(
        cls,
        *,
        metrics_collector: MetricsCollector | None = None,
    ) -> ModelGateway:
        """Create a gateway pre-loaded with the stub provider for testing/dev."""
        gateway = cls(default_provider="stub", metrics_collector=metrics_collector)
        gateway.register(StubModelProvider())
        return gateway

    def register(self, provider: ModelProvider) -> None:
        self._providers[provider.name] = provider

    def get_provider(self, name: str) -> ModelProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise LookupError(f"model provider not found: {name}") from exc

    def _resolve_provider(self, provider_name: str | None) -> ModelProvider:
        name = provider_name or self._default_provider
        if name is None:
            raise LookupError(
                "no provider_name supplied and no default_provider configured"
            )
        return self.get_provider(name)

    async def chat(
        self,
        provider_name: str | None = None,
        messages: list[ModelMessage] | None = None,
        *,
        model: str = "",
        temperature: float = 0.2,
        max_tokens: int = 1024,
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        provider = self._resolve_provider(provider_name)
        resp = await provider.chat(
            messages or [],
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
        )

        result = ChatResult.from_model_response(resp)

        if self._metrics:
            labels = {"provider": provider.name, "model": result.model}
            self._metrics.inc_counter("llm_calls_total", labels)
            self._metrics.inc_counter(
                "llm_input_tokens_total", labels, value=float(result.input_tokens),
            )
            self._metrics.inc_counter(
                "llm_output_tokens_total", labels, value=float(result.output_tokens),
            )
            if result.estimated_cost_usd:
                self._metrics.inc_counter(
                    "llm_cost_usd_total", labels, value=result.estimated_cost_usd,
                )

        return result

    def list_providers(self) -> list[str]:
        return list(self._providers.keys())
