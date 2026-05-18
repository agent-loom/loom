"""模型网关层，统一管理多种 LLM 提供商的调用与路由。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from enum import StrEnum
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
    provider_name: str = ""


class ChatResult(BaseModel):
    """High-level result from ModelGateway.chat(), exposing content, token counts and cost."""

    content: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    model: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str = "stop"
    provider_name: str = ""

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
            provider_name=resp.provider_name,
        )


class RoutingStrategy(StrEnum):
    PRIORITY = "priority"
    ROUND_ROBIN = "round_robin"
    COST_OPTIMIZED = "cost_optimized"


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


# ── Cost table ────────────────────────────────────────────────────

COST_PER_MILLION: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.150, 0.600),
    "gpt-4o": (5.00, 15.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.5": (75.00, 150.00),
    "o3-mini": (1.10, 4.40),
    "o4-mini": (1.10, 4.40),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-opus-4": (15.00, 75.00),
    "claude-haiku-4": (0.80, 4.00),
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    for prefix in sorted(COST_PER_MILLION, key=len, reverse=True):
        if model.startswith(prefix):
            input_price, output_price = COST_PER_MILLION[prefix]
            return (
                (prompt_tokens / 1_000_000) * input_price
                + (completion_tokens / 1_000_000) * output_price
            )
    return None


# ── Circuit breaker ───────────────────────────────────────────────


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-provider circuit breaker to avoid cascading failures."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max: int = 1,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max = half_open_max
        self._failure_count = 0
        self._success_count = 0
        self._state = CircuitState.CLOSED
        self._opened_at: float = 0.0
        self._half_open_calls = 0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        self._maybe_transition_to_half_open()
        return self._state

    def _maybe_transition_to_half_open(self) -> None:
        if (
            self._state == CircuitState.OPEN
            and time.monotonic() - self._opened_at >= self.recovery_timeout
        ):
            self._state = CircuitState.HALF_OPEN
            self._half_open_calls = 0

    def allow_request(self) -> bool:
        st = self.state
        if st == CircuitState.CLOSED:
            return True
        if st == CircuitState.HALF_OPEN:
            if self._half_open_calls < self.half_open_max:
                self._half_open_calls += 1
                return True
            return False
        return False

    def record_success(self) -> None:
        self._failure_count = 0
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.half_open_max:
                self._state = CircuitState.CLOSED
                self._success_count = 0

    def record_failure(self) -> None:
        self._failure_count += 1
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
        elif self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()


# ── Providers ─────────────────────────────────────────────────────


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
            provider_name=self.name,
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
        *,
        provider_name: str | None = None,
    ):
        self.name = provider_name or "openai_compatible"
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
            logger.warning(
                "LLM API 错误: status=%d, provider=%s",
                exc.response.status_code, self.name,
            )
            return ModelResponse(
                content=f"[LLM API error] {exc.response.status_code}",
                finish_reason="error",
                model=model or self._default_model,
                provider_name=self.name,
            )
        except httpx.RequestError as exc:
            logger.warning(
                "LLM 连接错误: %s, provider=%s",
                type(exc).__name__, self.name,
            )
            return ModelResponse(
                content=f"[LLM connection error] {type(exc).__name__}",
                finish_reason="error",
                model=model or self._default_model,
                provider_name=self.name,
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

        _used_model = data.get("model", model or self._default_model)
        _cost_usd = estimate_cost(_used_model, prompt_tokens, completion_tokens)

        return ModelResponse(
            content=message.get("content", ""),
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason", "stop"),
            model=_used_model,
            usage=usage,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=usage.get("total_tokens", prompt_tokens + completion_tokens),
            estimated_cost_usd=_cost_usd,
            provider_name=self.name,
        )

    async def close(self) -> None:
        await self._client.aclose()


class AnthropicProvider:
    """Provider that calls the Anthropic Messages API directly."""

    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        default_model: str = "claude-sonnet-4-6",
        timeout: float = 60.0,
        *,
        provider_name: str | None = None,
    ):
        self.name = provider_name or "anthropic"
        self._client = httpx.AsyncClient(
            base_url="https://api.anthropic.com",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
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
        system_content = ""
        api_messages: list[dict[str, str]] = []
        for m in messages:
            if m.role == "system":
                system_content += m.content + "\n"
            else:
                api_messages.append({"role": m.role, "content": m.content})

        if not api_messages:
            api_messages = [{"role": "user", "content": ""}]

        body: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_content.strip():
            body["system"] = system_content.strip()
        if tools:
            body["tools"] = [
                {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "input_schema": t.get("parameters", t.get("input_schema", {})),
                }
                for t in tools
            ]
        if stop:
            body["stop_sequences"] = stop

        try:
            resp = await self._client.post("/v1/messages", json=body)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Anthropic API 错误: status=%d, provider=%s",
                exc.response.status_code, self.name,
            )
            return ModelResponse(
                content=f"[Anthropic API error] {exc.response.status_code}",
                finish_reason="error",
                model=model or self._default_model,
                provider_name=self.name,
            )
        except httpx.RequestError as exc:
            logger.warning(
                "Anthropic 连接错误: %s, provider=%s",
                type(exc).__name__, self.name,
            )
            return ModelResponse(
                content=f"[Anthropic connection error] {type(exc).__name__}",
                finish_reason="error",
                model=model or self._default_model,
                provider_name=self.name,
            )

        data = resp.json()
        content_blocks = data.get("content", [])
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in content_blocks:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                        arguments=block.get("input", {}),
                    )
                )

        usage = data.get("usage", {})
        prompt_tokens = usage.get("input_tokens", 0)
        completion_tokens = usage.get("output_tokens", 0)

        _used_model = data.get("model", model or self._default_model)
        _cost_usd = estimate_cost(_used_model, prompt_tokens, completion_tokens)

        finish_reason = data.get("stop_reason", "end_turn")
        if finish_reason == "end_turn":
            finish_reason = "stop"
        elif finish_reason == "tool_use":
            finish_reason = "tool_calls"

        return ModelResponse(
            content="\n".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            model=_used_model,
            usage={"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            estimated_cost_usd=_cost_usd,
            provider_name=self.name,
        )

    async def close(self) -> None:
        await self._client.aclose()


# ── Gateway ───────────────────────────────────────────────────────


class ModelGateway:
    """Routes model calls to providers with fallback and circuit breaking."""

    def __init__(
        self,
        *,
        default_provider: str | None = None,
        fallback_chain: list[str] | None = None,
        routing_strategy: RoutingStrategy = RoutingStrategy.PRIORITY,
        metrics_collector: MetricsCollector | None = None,
    ) -> None:
        self._providers: dict[str, ModelProvider] = {}
        self._default_provider = default_provider
        self._fallback_chain = fallback_chain or []
        self._routing_strategy = routing_strategy
        self._metrics: MetricsCollector | None = metrics_collector
        self._breakers: dict[str, CircuitBreaker] = {}
        self._rr_index = 0

    @classmethod
    def create_default(
        cls,
        *,
        metrics_collector: MetricsCollector | None = None,
    ) -> ModelGateway:
        """创建网关并根据环境变量自动注册可用的 LLM provider。"""
        import os

        gateway = cls(default_provider="stub", metrics_collector=metrics_collector)
        gateway.register(StubModelProvider())

        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            openai_base = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
            provider = OpenAICompatibleProvider(
                base_url=openai_base,
                api_key=openai_key,
                provider_name="openai",
            )
            gateway.register(provider)
            gateway._default_provider = "openai"
            logger.info("已注册 OpenAI provider 为默认 LLM 提供商")

        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        if anthropic_key:
            provider = AnthropicProvider(api_key=anthropic_key)
            gateway.register(provider)
            if not openai_key:
                gateway._default_provider = "anthropic"
            logger.info("已注册 Anthropic provider")

        return gateway

    def register(
        self,
        provider: ModelProvider,
        *,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
    ) -> None:
        self._providers[provider.name] = provider
        self._breakers[provider.name] = CircuitBreaker(
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
        )

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

    def _build_attempt_order(self, provider_name: str | None) -> list[str]:
        """Build ordered list of providers to attempt (primary + fallbacks)."""
        primary = provider_name or self._default_provider
        if primary is None:
            raise LookupError(
                "no provider_name supplied and no default_provider configured"
            )
        if primary not in self._providers:
            raise LookupError(f"model provider not found: {primary}")

        chain = [primary]
        for fb in self._fallback_chain:
            if fb != primary and fb in self._providers:
                chain.append(fb)
        return chain

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
        attempt_order = self._build_attempt_order(provider_name)
        last_error: Exception | None = None

        for name in attempt_order:
            provider = self._providers[name]
            breaker = self._breakers[name]

            if not breaker.allow_request():
                logger.warning("circuit open for provider %s, skipping", name)
                continue

            try:
                resp = await provider.chat(
                    messages or [],
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=tools,
                )
                if resp.finish_reason == "error":
                    breaker.record_failure()
                    logger.warning(
                        "provider %s returned error, trying fallback: %s",
                        name,
                        resp.content[:100],
                    )
                    last_error = RuntimeError(resp.content)
                    continue

                breaker.record_success()
                result = ChatResult.from_model_response(resp)

                if self._metrics:
                    labels = {"provider": name, "model": result.model}
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

            except Exception as exc:
                breaker.record_failure()
                last_error = exc
                logger.warning("provider %s failed: %s", name, exc)
                continue

        if last_error:
            raise last_error
        raise LookupError("no available provider in fallback chain")

    def list_providers(self) -> list[str]:
        return list(self._providers.keys())

    def get_circuit_status(self) -> dict[str, str]:
        return {name: breaker.state.value for name, breaker in self._breakers.items()}
