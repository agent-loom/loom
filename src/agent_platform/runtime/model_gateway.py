"""模型网关层，统一管理多种 LLM 提供商的调用与路由。

架构概览：
  ModelGateway（核心网关）
    ├─ 多 Provider 注册：OpenAI / Anthropic / Stub
    ├─ 优先级 Fallback 链：primary → fallback₁ → fallback₂
    ├─ Per-provider CircuitBreaker：CLOSED ↔ OPEN ↔ HALF_OPEN
    └─ 统一 tool_choice 参数传递（各 Provider 内部自行转换格式）

Provider 差异化处理：
  - OpenAICompatibleProvider: tool_choice 直传（兼容 OpenAI 格式）
  - AnthropicProvider: "required"→{"type":"any"}, "none"→移除 tools,
                       {"type":"function","function":{"name":"X"}}→{"type":"tool","name":"X"}
"""

from __future__ import annotations

import json
import logging
import os
import time
import asyncio
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
    usage: dict[str, Any] = Field(default_factory=dict)
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
        tool_choice: str | dict[str, Any] | None = None,
    ) -> ModelResponse: ...


# ── Cost table ────────────────────────────────────────────────────

# ── 成本估算表 ── 格式: model_prefix → (input_$/M_tokens, output_$/M_tokens)
# estimate_cost() 按最长前缀匹配查找对应模型的价格

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


# ── 熔断器 ── 三态状态机: CLOSED(正常) → OPEN(熔断) → HALF_OPEN(探测恢复)


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
        tool_choice: str | dict[str, Any] | None = None,
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
        tool_choice: str | dict[str, Any] | None = None,
    ) -> ModelResponse:
        effective_model = model or self._default_model
        body: dict[str, Any] = {
            "model": effective_model,
            "messages": [
                self._format_message_payload(m, effective_model)
                for m in messages
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
        if stop:
            body["stop"] = stop
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
        if effective_model.startswith("openai/gpt-5"):
            body["reasoning_effort"] = os.getenv(
                "OPENAI_REASONING_EFFORT",
                "medium",
            )
        logger.info(
            "LLM request: provider=%s model=%s url=%s tools=%d stop=%d",
            self.name,
            effective_model,
            str(self._client.base_url.join(self._chat_completions_path())),
            len(body.get("tools", [])),
            len(stop or []),
        )

        try:
            resp = await self._client.post(self._chat_completions_path(), json=body)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            response_snippet = exc.response.text[:500]
            logger.warning(
                "LLM API 错误: status=%d, provider=%s, url=%s, response=%s",
                exc.response.status_code, self.name,
                exc.request.url,
                response_snippet,
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

    @staticmethod
    def _dump_tool_call(tool_call: Any) -> dict[str, Any]:
        if isinstance(tool_call, ToolCall):
            return {
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.name,
                    "arguments": json.dumps(tool_call.arguments, ensure_ascii=False),
                },
            }
        if isinstance(tool_call, BaseModel):
            return tool_call.model_dump(mode="json")
        if isinstance(tool_call, dict):
            return tool_call
        return {
            "id": getattr(tool_call, "id", ""),
            "type": "function",
            "function": {
                "name": getattr(tool_call, "name", ""),
                "arguments": json.dumps(
                    getattr(tool_call, "arguments", {}) or {},
                    ensure_ascii=False,
                ),
            },
        }

    @staticmethod
    def _format_message_payload(
        message: ModelMessage,
        model: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "role": message.role,
            "content": message.content,
        }
        if (
            model.startswith("openai/gpt-5")
            and message.role in {"system", "user", "assistant"}
            and isinstance(message.content, str)
        ):
            payload["content"] = [{"type": "text", "text": message.content}]
        if message.tool_call_id:
            payload["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            payload["tool_calls"] = [
                OpenAICompatibleProvider._dump_tool_call(tc)
                for tc in message.tool_calls
            ]
        return payload

    def _chat_completions_path(self) -> str:
        base_path = self._client.base_url.path.rstrip("/")
        if base_path.endswith("/v1"):
            return "chat/completions"
        return "/v1/chat/completions"


class AnthropicProvider:
    """Provider that calls the Anthropic Messages API directly."""

    name = "anthropic"

    def __init__(
        self,
        api_key: str = "",
        default_model: str = "claude-sonnet-4-6",
        timeout: float = 60.0,
        *,
        auth_token: str = "",
        base_url: str = "https://api.anthropic.com",
        provider_name: str | None = None,
    ):
        if not api_key and not auth_token:
            raise ValueError("AnthropicProvider requires api_key or auth_token")

        headers = {
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        if api_key:
            headers["x-api-key"] = api_key
        else:
            headers["authorization"] = f"Bearer {auth_token}"

        self.name = provider_name or "anthropic"
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
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
        tool_choice: str | dict[str, Any] | None = None,
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
        # Anthropic tool_choice 格式转换：OpenAI → Anthropic Messages API
        if tool_choice is not None:
            if isinstance(tool_choice, str):
                if tool_choice == "required":
                    body["tool_choice"] = {"type": "any"}   # 强制调用任意工具
                elif tool_choice == "auto":
                    body["tool_choice"] = {"type": "auto"}  # 模型自主决定
                elif tool_choice == "none":
                    body.pop("tools", None)                 # 禁用工具：移除 tools 字段
            elif isinstance(tool_choice, dict):
                # {"type":"function","function":{"name":"X"}} → {"type":"tool","name":"X"}
                func_name = None
                if tool_choice.get("type") == "function":
                    func_name = tool_choice.get("function", {}).get("name")
                elif "name" in tool_choice:
                    func_name = tool_choice.get("name")
                if func_name:
                    body["tool_choice"] = {"type": "tool", "name": func_name}
                else:
                    body["tool_choice"] = tool_choice
        logger.info(
            "LLM request: provider=%s model=%s url=%s tools=%d stop=%d",
            self.name,
            body["model"],
            str(self._client.base_url.join("/v1/messages")),
            len(body.get("tools", [])),
            len(stop or []),
        )

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
    """模型调用网关 (Model Gateway)

    能力层 (Capability Layer) 的核心网络适配与降级守卫组件。
    负责将上层 Agent 运行时（如 Native / Hermes 等）发出的抽象模型请求，统一代理并分发给底层具体的服务商（Providers）。

    核心设计职责与规范 (docs/02-architecture/agent-platform-core-design.md §3.7)：
      1. Provider 统一注册：通过实现 `ModelProvider` 协议接入新大模型。
      2. 弹性路由降级 (Fallback)：当主模型调用失败（服务商挂掉/API限流）时，按路由链顺序尝试备用模型。
      3. 熔断隔离守护 (CircuitBreaker)：限制各 Provider 的错误扩散，故障达到阈值后自动开路，探测期成功后自动闭合。
      4. 统一工具调用选择 (tool_choice)：在上游网关层统一接口，向下游模型端做针对性协议翻译。

    设计差距说明 (TODO)：
      1. 架构文档 §3.7 明确指出需具备 "per-agent model call rate limit" (每 Agent 模型的调用速率限制)，
         防止某个恶性循环的 Agent 迅速烧光全站 API 额度。目前该能力暂未接入，属于关键缺失。
         实现思路：在 chat() 入口处加锁或通过 Redis 计数器做 per-agent 限流拦截。
    """

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
        hermes_openai_base = os.getenv("HERMES_OPENAI_BASE_URL")
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        openai_default_model = (
            os.getenv("OPENAI_DEFAULT_MODEL")
            or os.getenv("OPENAI_MODEL")
            or (
                "openai/gpt-5.2-codex"
                if hermes_openai_base and anthropic_key and not openai_key
                else "gpt-4o-mini"
            )
        )
        openai_base = (
            os.getenv("OPENAI_API_BASE")
            or hermes_openai_base
            or "https://api.openai.com/v1"
        )
        # Hermes runtime commonly uses an OpenAI-compatible gateway with
        # HERMES_OPENAI_BASE_URL + ANTHROPIC_API_KEY. Reuse that pair for the
        # platform ModelGateway so Review Fork and other internal flows can
        # reach the same gateway without duplicating env vars.
        openai_compat_key = openai_key or (
            anthropic_key if hermes_openai_base else None
        )
        if openai_compat_key:
            provider = OpenAICompatibleProvider(
                base_url=openai_base,
                api_key=openai_compat_key,
                default_model=openai_default_model,
                provider_name="openai",
            )
            gateway.register(provider)
            gateway._default_provider = "openai"
            if openai_key:
                logger.info("已注册 OpenAI provider 为默认 LLM 提供商")
            else:
                logger.info(
                    "已注册 OpenAI-compatible provider 为默认 LLM 提供商 "
                    "(source=HERMES_OPENAI_BASE_URL+ANTHROPIC_API_KEY)"
                )

        anthropic_auth_token = os.getenv("ANTHROPIC_AUTH_TOKEN")
        anthropic_base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        if anthropic_key or anthropic_auth_token:
            provider = AnthropicProvider(
                api_key=anthropic_key or "",
                auth_token=anthropic_auth_token or "",
                base_url=anthropic_base_url,
            )
            gateway.register(provider)
            if not openai_compat_key:
                gateway._default_provider = "anthropic"
            logger.info("已注册 Anthropic provider")

        configured_default = os.getenv("MODEL_GATEWAY_DEFAULT_PROVIDER")
        if configured_default:
            if configured_default not in gateway._providers:
                logger.warning(
                    "MODEL_GATEWAY_DEFAULT_PROVIDER=%s 未注册，保持当前默认 provider=%s",
                    configured_default,
                    gateway._default_provider,
                )
            else:
                gateway._default_provider = configured_default
                logger.info(
                    "ModelGateway 默认 provider 已由环境变量覆盖: %s",
                    configured_default,
                )

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
        tool_choice: str | dict[str, Any] | None = None,
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
                    tool_choice=tool_choice,
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
