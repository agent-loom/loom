"""Hermes 错误/重试事件映射，将 Hermes SDK 错误转换为平台 AgentError 和流式事件。

本模块提供三层抽象：
- HermesErrorMapper：将 Hermes SDK 异常分类映射为平台 AgentError + AgentStreamEvent
- HermesRetryPolicy：根据错误类型决定重试策略（指数退避）
- HermesErrorHandler：组合 ErrorMapper + RetryPolicy，提供带重试的执行器
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from agent_platform.api.stream_events import AgentStreamEvent, error_event
from agent_platform.domain.models import AgentError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hermes SDK 错误类型名称常量（基于类名匹配，不依赖真实 SDK 导入）
# ---------------------------------------------------------------------------
_TIMEOUT_NAMES = frozenset({"HermesTimeoutError", "TimeoutError", "asyncio.TimeoutError"})
_RATE_LIMIT_NAMES = frozenset({"HermesRateLimitError", "RateLimitError"})
_MODEL_ERROR_NAMES = frozenset({"HermesModelError", "ModelError"})
_TOOL_ERROR_NAMES = frozenset({"HermesToolError", "ToolError", "ToolExecutionError"})
_INTERRUPT_NAMES = frozenset({"HermesInterruptError", "InterruptError", "KeyboardInterrupt"})


class HermesErrorMapper:
    """将 Hermes SDK 错误映射为平台 AgentError + AgentStreamEvent。

    通过异常类名进行 duck typing 匹配，不依赖真实 Hermes SDK 导入。
    """

    def map_error(self, exc: Exception) -> AgentError:
        """将 Hermes SDK 异常分类并返回平台 AgentError。

        错误分类规则：
        - TimeoutError 类 → HERMES_TIMEOUT (retryable)
        - RateLimitError 类 → HERMES_RATE_LIMITED (retryable)
        - ModelError 类 → HERMES_MODEL_ERROR (不可重试)
        - ToolError 类 → HERMES_TOOL_ERROR (retryable)
        - InterruptError 类 → HERMES_INTERRUPTED (不可重试)
        - 未知错误 → HERMES_UNKNOWN (不可重试)

        Args:
            exc: Hermes SDK 抛出的异常

        Returns:
            平台 AgentError 实例
        """
        exc_name = type(exc).__name__
        exc_mro_names = frozenset(t.__name__ for t in type(exc).__mro__)

        if exc_mro_names & _TIMEOUT_NAMES:
            return AgentError(
                code="HERMES_TIMEOUT",
                message=f"Hermes 执行超时: {exc}",
                retryable=True,
                details={"original_error": exc_name},
            )

        if exc_mro_names & _RATE_LIMIT_NAMES:
            return AgentError(
                code="HERMES_RATE_LIMITED",
                message=f"Hermes 触发速率限制: {exc}",
                retryable=True,
                details={"original_error": exc_name},
            )

        if exc_mro_names & _MODEL_ERROR_NAMES:
            return AgentError(
                code="HERMES_MODEL_ERROR",
                message=f"Hermes 模型调用失败: {exc}",
                retryable=False,
                details={"original_error": exc_name},
            )

        if exc_mro_names & _TOOL_ERROR_NAMES:
            return AgentError(
                code="HERMES_TOOL_ERROR",
                message=f"Hermes 工具执行失败: {exc}",
                retryable=True,
                details={"original_error": exc_name},
            )

        if exc_mro_names & _INTERRUPT_NAMES:
            return AgentError(
                code="HERMES_INTERRUPTED",
                message=f"Hermes 执行被中断: {exc}",
                retryable=False,
                details={"original_error": exc_name},
            )

        return AgentError(
            code="HERMES_UNKNOWN",
            message=f"Hermes 未知错误: {exc}",
            retryable=False,
            details={"original_error": exc_name},
        )

    def to_stream_event(self, error: AgentError) -> AgentStreamEvent:
        """将 AgentError 转换为流式事件。

        Args:
            error: 平台 AgentError 实例

        Returns:
            AgentStreamEvent 错误事件
        """
        return error_event(code=error.code, message=error.message)


class HermesRetryPolicy:
    """根据错误类型决定重试策略，使用指数退避算法。

    仅对 retryable=True 的错误进行重试，
    退避公式: delay = min(base * factor^attempt, max_delay)
    """

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        backoff_factor: float = 2.0,
        max_delay: float = 30.0,
    ) -> None:
        """初始化重试策略。

        Args:
            max_retries: 最大重试次数，默认 3
            base_delay: 基础延迟秒数，默认 1.0
            backoff_factor: 退避因子，默认 2.0
            max_delay: 最大延迟秒数，默认 30.0
        """
        self.max_retries = max_retries
        self._base_delay = base_delay
        self._backoff_factor = backoff_factor
        self._max_delay = max_delay

    def should_retry(self, error: AgentError, attempt: int) -> bool:
        """判断是否应该重试。

        仅当错误可重试且未超过最大重试次数时返回 True。

        Args:
            error: 平台 AgentError 实例
            attempt: 当前重试次数（从 0 开始）

        Returns:
            是否应该重试
        """
        if not error.retryable:
            return False
        return attempt < self.max_retries

    def get_delay(self, attempt: int) -> float:
        """计算指数退避延迟。

        公式: delay = min(base * factor^attempt, max_delay)

        Args:
            attempt: 当前重试次数（从 0 开始）

        Returns:
            延迟秒数
        """
        delay = self._base_delay * (self._backoff_factor ** attempt)
        return min(delay, self._max_delay)


class HermesErrorHandler:
    """组合 ErrorMapper + RetryPolicy，提供带重试的异步执行器。

    在 HermesRuntimeBackend.run() 中使用，自动处理错误分类和重试逻辑。
    """

    def __init__(
        self,
        mapper: HermesErrorMapper | None = None,
        retry_policy: HermesRetryPolicy | None = None,
    ) -> None:
        """初始化错误处理器。

        Args:
            mapper: 错误映射器，默认创建新实例
            retry_policy: 重试策略，默认创建新实例
        """
        self._mapper = mapper or HermesErrorMapper()
        self._retry_policy = retry_policy or HermesRetryPolicy()

    @property
    def mapper(self) -> HermesErrorMapper:
        """获取错误映射器。"""
        return self._mapper

    @property
    def retry_policy(self) -> HermesRetryPolicy:
        """获取重试策略。"""
        return self._retry_policy

    async def handle_with_retry(
        self,
        fn: Callable[..., Any],
        *args: Any,
        max_retries: int | None = None,
        **kwargs: Any,
    ) -> Any:
        """带重试的异步执行器。

        根据错误类型自动决定是否重试，使用指数退避延迟。
        每次重试都会记录日志。

        Args:
            fn: 要执行的异步函数
            *args: 传递给 fn 的位置参数
            max_retries: 覆盖默认最大重试次数
            **kwargs: 传递给 fn 的关键字参数

        Returns:
            fn 的返回值

        Raises:
            最后一次尝试的异常（包装为日志记录后重新抛出）
        """
        effective_max = (
            max_retries if max_retries is not None
            else self._retry_policy.max_retries
        )
        last_error: AgentError | None = None
        last_exc: Exception | None = None

        for attempt in range(effective_max + 1):
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                last_error = self._mapper.map_error(exc)

                if not self._retry_policy.should_retry(last_error, attempt):
                    logger.error(
                        "Hermes 执行失败（不可重试）: code=%s, message=%s",
                        last_error.code,
                        last_error.message,
                    )
                    raise

                delay = self._retry_policy.get_delay(attempt)
                logger.warning(
                    "Hermes 执行失败，第 %d 次重试（共 %d 次），"
                    "延迟 %.1f 秒: code=%s, message=%s",
                    attempt + 1,
                    effective_max,
                    delay,
                    last_error.code,
                    last_error.message,
                )
                await asyncio.sleep(delay)

        # 所有重试耗尽后抛出最后的异常
        if last_exc is not None:
            logger.error(
                "Hermes 执行失败（重试耗尽）: code=%s, message=%s",
                last_error.code if last_error else "UNKNOWN",
                last_error.message if last_error else str(last_exc),
            )
            raise last_exc

        # 理论上不会执行到这里
        msg = "handle_with_retry 逻辑异常：无异常但也无返回值"
        raise RuntimeError(msg)
