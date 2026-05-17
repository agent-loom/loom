"""Dead Letter Queue：Webhook 失败事件的重试队列管理。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(UTC)


class DeadLetterEntry(BaseModel):
    """Dead Letter Queue 中的单条条目。"""

    id: str = Field(default_factory=lambda: uuid4().hex)
    source: str  # "plane" | "gitlab"
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    error_message: str
    retry_count: int = 0
    max_retries: int = 5
    next_retry_at: datetime | None = None
    status: str = "pending"  # "pending" | "retrying" | "exhausted" | "resolved"
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


@runtime_checkable
class DeadLetterQueue(Protocol):
    """Dead Letter Queue 协议定义。"""

    async def enqueue(self, entry: DeadLetterEntry) -> None:
        """将失败条目入队。"""
        ...

    async def dequeue_ready(self) -> list[DeadLetterEntry]:
        """获取已到达重试时间的条目。"""
        ...

    async def mark_resolved(self, entry_id: str) -> None:
        """标记条目为已解决。"""
        ...

    async def mark_exhausted(self, entry_id: str) -> None:
        """标记条目为已耗尽重试次数。"""
        ...

    async def update_retry(
        self, entry_id: str, next_retry_at: datetime, retry_count: int,
    ) -> None:
        """更新条目的重试信息。"""
        ...

    async def list_entries(
        self, status: str | None = None, limit: int = 100,
    ) -> list[DeadLetterEntry]:
        """列出条目，可按状态过滤。"""
        ...

    async def purge_resolved(self, older_than_days: int = 7) -> int:
        """清除已解决的旧条目，返回清除数量。"""
        ...


class InMemoryDeadLetterQueue:
    """基于内存的 Dead Letter Queue 实现。"""

    def __init__(self) -> None:
        self._entries: dict[str, DeadLetterEntry] = {}

    async def enqueue(self, entry: DeadLetterEntry) -> None:
        """将失败条目入队。"""
        self._entries[entry.id] = entry
        logger.info(
            "DLQ 入队: id=%s source=%s event=%s",
            entry.id, entry.source, entry.event_type,
        )

    async def dequeue_ready(self) -> list[DeadLetterEntry]:
        """获取已到达重试时间的条目（状态为 pending 或 retrying）。"""
        now = _utc_now()
        ready: list[DeadLetterEntry] = []
        for entry in self._entries.values():
            if entry.status not in ("pending", "retrying"):
                continue
            if entry.next_retry_at is None or entry.next_retry_at <= now:
                ready.append(entry)
        return ready

    async def mark_resolved(self, entry_id: str) -> None:
        """标记条目为已解决。"""
        if entry_id in self._entries:
            self._entries[entry_id].status = "resolved"
            self._entries[entry_id].updated_at = _utc_now()

    async def mark_exhausted(self, entry_id: str) -> None:
        """标记条目为已耗尽重试次数。"""
        if entry_id in self._entries:
            self._entries[entry_id].status = "exhausted"
            self._entries[entry_id].updated_at = _utc_now()

    async def update_retry(
        self, entry_id: str, next_retry_at: datetime, retry_count: int,
    ) -> None:
        """更新条目的重试信息。"""
        if entry_id in self._entries:
            entry = self._entries[entry_id]
            entry.next_retry_at = next_retry_at
            entry.retry_count = retry_count
            entry.status = "retrying"
            entry.updated_at = _utc_now()

    async def list_entries(
        self, status: str | None = None, limit: int = 100,
    ) -> list[DeadLetterEntry]:
        """列出条目，可按状态过滤。"""
        entries = list(self._entries.values())
        if status is not None:
            entries = [e for e in entries if e.status == status]
        # 按创建时间倒序
        entries.sort(key=lambda e: e.created_at, reverse=True)
        return entries[:limit]

    async def purge_resolved(self, older_than_days: int = 7) -> int:
        """清除已解决的旧条目，返回清除数量。"""
        cutoff = _utc_now() - timedelta(days=older_than_days)
        to_remove: list[str] = []
        for entry_id, entry in self._entries.items():
            if entry.status == "resolved" and entry.updated_at < cutoff:
                to_remove.append(entry_id)
        for entry_id in to_remove:
            del self._entries[entry_id]
        return len(to_remove)


def _calculate_backoff(retry_count: int, base_seconds: int = 60) -> timedelta:
    """计算指数退避延迟：base_seconds * 2^retry_count。"""
    delay = base_seconds * (2 ** retry_count)
    return timedelta(seconds=delay)


class WebhookRetryService:
    """Webhook 重试服务：管理失败事件的入队与指数退避重试。"""

    def __init__(
        self,
        dlq: DeadLetterQueue,
        max_retries: int = 5,
    ) -> None:
        self._dlq = dlq
        self._max_retries = max_retries

    @property
    def dlq(self) -> DeadLetterQueue:
        """返回底层的 Dead Letter Queue 实例。"""
        return self._dlq

    async def handle_failure(
        self,
        source: str,
        event_type: str,
        payload: dict[str, Any],
        error: str,
    ) -> None:
        """将失败的 webhook 事件入队并设置首次重试时间。"""
        entry = DeadLetterEntry(
            source=source,
            event_type=event_type,
            payload=payload,
            error_message=error,
            max_retries=self._max_retries,
            next_retry_at=_utc_now() + _calculate_backoff(0),
        )
        await self._dlq.enqueue(entry)

    async def process_retries(
        self,
        handler: Callable[..., Any],
    ) -> int:
        """处理到期的重试项，返回处理数量。

        handler 签名: async handler(source, event_type, payload) -> None
        成功则标记为 resolved，失败则更新重试计数或标记为 exhausted。
        """
        ready = await self._dlq.dequeue_ready()
        processed = 0

        for entry in ready:
            try:
                await handler(entry.source, entry.event_type, entry.payload)
                await self._dlq.mark_resolved(entry.id)
                logger.info("DLQ 重试成功: id=%s", entry.id)
            except Exception as exc:
                new_count = entry.retry_count + 1
                if new_count >= entry.max_retries:
                    await self._dlq.mark_exhausted(entry.id)
                    logger.warning(
                        "DLQ 重试耗尽: id=%s, error=%s", entry.id, exc,
                    )
                else:
                    next_at = _utc_now() + _calculate_backoff(new_count)
                    await self._dlq.update_retry(entry.id, next_at, new_count)
                    logger.info(
                        "DLQ 重试失败, 下次重试: id=%s count=%d next=%s",
                        entry.id, new_count, next_at,
                    )
            processed += 1

        return processed
