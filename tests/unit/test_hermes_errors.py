"""HermesErrorMapper、HermesRetryPolicy、HermesErrorHandler 单元测试。"""

from __future__ import annotations

import pytest

from agent_platform.api.stream_events import StreamEventType
from agent_platform.domain.models import AgentError
from agent_platform.runtime.hermes_errors import (
    HermesErrorHandler,
    HermesErrorMapper,
    HermesRetryPolicy,
)

# ── 模拟 Hermes SDK 错误类型（不依赖真实 SDK） ─────────────


class HermesTimeoutError(TimeoutError):
    """模拟 Hermes 超时错误。"""


class HermesRateLimitError(Exception):
    """模拟 Hermes 速率限制错误。"""


class HermesModelError(Exception):
    """模拟 Hermes 模型错误。"""


class HermesToolError(Exception):
    """模拟 Hermes 工具执行错误。"""


class HermesInterruptError(Exception):
    """模拟 Hermes 中断错误。"""


class SomeUnknownError(Exception):
    """模拟未知错误。"""


# ── HermesErrorMapper.map_error ───────────────────────────


class TestHermesErrorMapper:
    """HermesErrorMapper 错误映射测试。"""

    def setup_method(self) -> None:
        self.mapper = HermesErrorMapper()

    def test_map_timeout_error(self):
        """超时错误应映射为 HERMES_TIMEOUT（可重试）。"""
        error = self.mapper.map_error(HermesTimeoutError("timed out"))

        assert error.code == "HERMES_TIMEOUT"
        assert error.retryable is True
        assert "超时" in error.message

    def test_map_rate_limit_error(self):
        """速率限制错误应映射为 HERMES_RATE_LIMITED（可重试）。"""
        error = self.mapper.map_error(HermesRateLimitError("rate limited"))

        assert error.code == "HERMES_RATE_LIMITED"
        assert error.retryable is True

    def test_map_model_error(self):
        """模型错误应映射为 HERMES_MODEL_ERROR（不可重试）。"""
        error = self.mapper.map_error(HermesModelError("model failed"))

        assert error.code == "HERMES_MODEL_ERROR"
        assert error.retryable is False

    def test_map_tool_error(self):
        """工具错误应映射为 HERMES_TOOL_ERROR（可重试）。"""
        error = self.mapper.map_error(HermesToolError("tool failed"))

        assert error.code == "HERMES_TOOL_ERROR"
        assert error.retryable is True

    def test_map_interrupt_error(self):
        """中断错误应映射为 HERMES_INTERRUPTED（不可重试）。"""
        error = self.mapper.map_error(HermesInterruptError("interrupted"))

        assert error.code == "HERMES_INTERRUPTED"
        assert error.retryable is False

    def test_map_unknown_error(self):
        """未知错误应映射为 HERMES_UNKNOWN（不可重试）。"""
        error = self.mapper.map_error(SomeUnknownError("something went wrong"))

        assert error.code == "HERMES_UNKNOWN"
        assert error.retryable is False

    def test_to_stream_event(self):
        """AgentError 应正确转换为流式错误事件。"""
        agent_error = AgentError(
            code="HERMES_TIMEOUT",
            message="执行超时",
            retryable=True,
        )
        event = self.mapper.to_stream_event(agent_error)

        assert event.type == StreamEventType.ERROR
        assert event.data["code"] == "HERMES_TIMEOUT"
        assert event.data["message"] == "执行超时"


# ── HermesRetryPolicy ─────────────────────────────────────


class TestHermesRetryPolicy:
    """HermesRetryPolicy 重试策略测试。"""

    def setup_method(self) -> None:
        self.policy = HermesRetryPolicy(max_retries=3)

    def test_should_retry_retryable_error(self):
        """可重试错误在未超过最大次数时应返回 True。"""
        error = AgentError(code="HERMES_TIMEOUT", message="timeout", retryable=True)

        assert self.policy.should_retry(error, attempt=0) is True
        assert self.policy.should_retry(error, attempt=1) is True
        assert self.policy.should_retry(error, attempt=2) is True

    def test_should_not_retry_non_retryable(self):
        """不可重试错误应始终返回 False。"""
        error = AgentError(
            code="HERMES_MODEL_ERROR", message="model error", retryable=False,
        )

        assert self.policy.should_retry(error, attempt=0) is False

    def test_should_not_retry_exceeded_max(self):
        """超过最大重试次数应返回 False。"""
        error = AgentError(code="HERMES_TIMEOUT", message="timeout", retryable=True)

        assert self.policy.should_retry(error, attempt=3) is False
        assert self.policy.should_retry(error, attempt=4) is False

    def test_get_delay_exponential_backoff(self):
        """延迟应按指数退避增长。"""
        policy = HermesRetryPolicy(
            base_delay=1.0, backoff_factor=2.0, max_delay=30.0,
        )

        assert policy.get_delay(0) == 1.0
        assert policy.get_delay(1) == 2.0
        assert policy.get_delay(2) == 4.0
        assert policy.get_delay(3) == 8.0

    def test_get_delay_respects_max(self):
        """延迟不应超过 max_delay。"""
        policy = HermesRetryPolicy(
            base_delay=1.0, backoff_factor=2.0, max_delay=5.0,
        )

        assert policy.get_delay(0) == 1.0
        assert policy.get_delay(10) == 5.0  # 2^10 = 1024，但限制为 5.0


# ── HermesErrorHandler.handle_with_retry ──────────────────


class TestHermesErrorHandler:
    """HermesErrorHandler 带重试执行器测试。"""

    @pytest.mark.asyncio
    async def test_handle_success_no_retry(self):
        """成功执行不应触发重试。"""
        handler = HermesErrorHandler()
        call_count = 0

        async def success_fn():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await handler.handle_with_retry(success_fn)

        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_handle_non_retryable_fails_immediately(self):
        """不可重试错误应立即失败，不进行重试。"""
        handler = HermesErrorHandler()
        call_count = 0

        async def fail_fn():
            nonlocal call_count
            call_count += 1
            raise HermesModelError("模型错误")

        with pytest.raises(HermesModelError):
            await handler.handle_with_retry(fail_fn)

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_handle_retry_then_success(self):
        """可重试错误在重试后成功应返回结果。"""
        retry_policy = HermesRetryPolicy(
            max_retries=3, base_delay=0.01, backoff_factor=1.0, max_delay=0.01,
        )
        handler = HermesErrorHandler(retry_policy=retry_policy)
        call_count = 0

        async def flaky_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise HermesTimeoutError("超时")
            return "recovered"

        result = await handler.handle_with_retry(flaky_fn)

        assert result == "recovered"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_handle_exhausts_retries(self):
        """重试耗尽后应抛出最后的异常。"""
        retry_policy = HermesRetryPolicy(
            max_retries=2, base_delay=0.01, backoff_factor=1.0, max_delay=0.01,
        )
        handler = HermesErrorHandler(retry_policy=retry_policy)
        call_count = 0

        async def always_fail_fn():
            nonlocal call_count
            call_count += 1
            raise HermesTimeoutError("始终超时")

        with pytest.raises(HermesTimeoutError):
            await handler.handle_with_retry(always_fail_fn)

        # 初始尝试 + 2 次重试 = 3 次调用
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_handle_custom_max_retries(self):
        """自定义 max_retries 应覆盖默认值。"""
        retry_policy = HermesRetryPolicy(
            max_retries=5, base_delay=0.01, backoff_factor=1.0, max_delay=0.01,
        )
        handler = HermesErrorHandler(retry_policy=retry_policy)
        call_count = 0

        async def always_fail_fn():
            nonlocal call_count
            call_count += 1
            raise HermesTimeoutError("超时")

        with pytest.raises(HermesTimeoutError):
            await handler.handle_with_retry(always_fail_fn, max_retries=1)

        # 初始尝试 + 1 次重试 = 2 次调用
        assert call_count == 2
