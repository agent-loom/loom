"""Dead Letter Queue 与 WebhookRetryService 单元测试。"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from agent_platform.webhooks.dead_letter import (
    DeadLetterEntry,
    InMemoryDeadLetterQueue,
    WebhookRetryService,
    _calculate_backoff,
    _utc_now,
)

# ---------------------------------------------------------------------------
# InMemoryDeadLetterQueue 测试
# ---------------------------------------------------------------------------


class TestInMemoryDeadLetterQueue:
    """内存 DLQ 基本操作测试。"""

    @pytest.mark.asyncio
    async def test_enqueue_and_list(self):
        """入队后应能通过 list_entries 查到。"""
        dlq = InMemoryDeadLetterQueue()
        entry = DeadLetterEntry(
            source="plane",
            event_type="issue.updated",
            payload={"key": "value"},
            error_message="connection timeout",
        )
        await dlq.enqueue(entry)
        entries = await dlq.list_entries()
        assert len(entries) == 1
        assert entries[0].id == entry.id
        assert entries[0].source == "plane"

    @pytest.mark.asyncio
    async def test_dequeue_ready_with_past_retry(self):
        """next_retry_at 已过期的条目应被 dequeue_ready 返回。"""
        dlq = InMemoryDeadLetterQueue()
        entry = DeadLetterEntry(
            source="gitlab",
            event_type="merge_request",
            payload={},
            error_message="500 error",
            next_retry_at=_utc_now() - timedelta(minutes=1),
        )
        await dlq.enqueue(entry)
        ready = await dlq.dequeue_ready()
        assert len(ready) == 1

    @pytest.mark.asyncio
    async def test_dequeue_ready_skips_future_retry(self):
        """next_retry_at 在未来的条目不应被 dequeue_ready 返回。"""
        dlq = InMemoryDeadLetterQueue()
        entry = DeadLetterEntry(
            source="plane",
            event_type="issue.created",
            payload={},
            error_message="error",
            next_retry_at=_utc_now() + timedelta(hours=1),
        )
        await dlq.enqueue(entry)
        ready = await dlq.dequeue_ready()
        assert len(ready) == 0

    @pytest.mark.asyncio
    async def test_mark_resolved(self):
        """标记为 resolved 后状态应更新。"""
        dlq = InMemoryDeadLetterQueue()
        entry = DeadLetterEntry(
            source="plane",
            event_type="issue.updated",
            payload={},
            error_message="error",
        )
        await dlq.enqueue(entry)
        await dlq.mark_resolved(entry.id)
        entries = await dlq.list_entries(status="resolved")
        assert len(entries) == 1
        assert entries[0].status == "resolved"

    @pytest.mark.asyncio
    async def test_mark_exhausted(self):
        """标记为 exhausted 后状态应更新。"""
        dlq = InMemoryDeadLetterQueue()
        entry = DeadLetterEntry(
            source="gitlab",
            event_type="push",
            payload={},
            error_message="error",
        )
        await dlq.enqueue(entry)
        await dlq.mark_exhausted(entry.id)
        entries = await dlq.list_entries(status="exhausted")
        assert len(entries) == 1

    @pytest.mark.asyncio
    async def test_update_retry(self):
        """更新重试信息后字段应正确更新。"""
        dlq = InMemoryDeadLetterQueue()
        entry = DeadLetterEntry(
            source="plane",
            event_type="issue.updated",
            payload={},
            error_message="error",
        )
        await dlq.enqueue(entry)
        next_at = _utc_now() + timedelta(minutes=5)
        await dlq.update_retry(entry.id, next_at, 2)
        entries = await dlq.list_entries()
        assert entries[0].retry_count == 2
        assert entries[0].status == "retrying"
        assert entries[0].next_retry_at == next_at

    @pytest.mark.asyncio
    async def test_list_entries_filter_by_status(self):
        """按状态过滤应只返回匹配条目。"""
        dlq = InMemoryDeadLetterQueue()
        e1 = DeadLetterEntry(
            source="plane", event_type="a", payload={}, error_message="e1",
        )
        e2 = DeadLetterEntry(
            source="gitlab", event_type="b", payload={}, error_message="e2",
        )
        await dlq.enqueue(e1)
        await dlq.enqueue(e2)
        await dlq.mark_resolved(e1.id)

        pending = await dlq.list_entries(status="pending")
        resolved = await dlq.list_entries(status="resolved")
        assert len(pending) == 1
        assert len(resolved) == 1

    @pytest.mark.asyncio
    async def test_purge_resolved(self):
        """清除已解决的旧条目。"""
        dlq = InMemoryDeadLetterQueue()
        entry = DeadLetterEntry(
            source="plane",
            event_type="issue.updated",
            payload={},
            error_message="error",
        )
        await dlq.enqueue(entry)
        await dlq.mark_resolved(entry.id)
        # 手动设置 updated_at 为 10 天前
        dlq._entries[entry.id].updated_at = _utc_now() - timedelta(days=10)
        purged = await dlq.purge_resolved(older_than_days=7)
        assert purged == 1
        entries = await dlq.list_entries()
        assert len(entries) == 0

    @pytest.mark.asyncio
    async def test_purge_resolved_keeps_recent(self):
        """不应清除最近的已解决条目。"""
        dlq = InMemoryDeadLetterQueue()
        entry = DeadLetterEntry(
            source="plane",
            event_type="issue.updated",
            payload={},
            error_message="error",
        )
        await dlq.enqueue(entry)
        await dlq.mark_resolved(entry.id)
        purged = await dlq.purge_resolved(older_than_days=7)
        assert purged == 0
        entries = await dlq.list_entries()
        assert len(entries) == 1

    @pytest.mark.asyncio
    async def test_dequeue_ready_skips_resolved(self):
        """已解决的条目不应出现在 dequeue_ready 结果中。"""
        dlq = InMemoryDeadLetterQueue()
        entry = DeadLetterEntry(
            source="plane",
            event_type="issue.updated",
            payload={},
            error_message="error",
            next_retry_at=_utc_now() - timedelta(minutes=1),
        )
        await dlq.enqueue(entry)
        await dlq.mark_resolved(entry.id)
        ready = await dlq.dequeue_ready()
        assert len(ready) == 0


# ---------------------------------------------------------------------------
# 指数退避计算测试
# ---------------------------------------------------------------------------


class TestCalculateBackoff:
    """指数退避延迟计算测试。"""

    def test_first_retry(self):
        """第一次重试延迟应为 base_seconds。"""
        delay = _calculate_backoff(0, base_seconds=60)
        assert delay == timedelta(seconds=60)

    def test_second_retry(self):
        """第二次重试延迟应为 base * 2。"""
        delay = _calculate_backoff(1, base_seconds=60)
        assert delay == timedelta(seconds=120)

    def test_third_retry(self):
        """第三次重试延迟应为 base * 4。"""
        delay = _calculate_backoff(2, base_seconds=60)
        assert delay == timedelta(seconds=240)

    def test_exponential_growth(self):
        """延迟应按指数增长。"""
        delays = [_calculate_backoff(i, base_seconds=10) for i in range(5)]
        expected = [
            timedelta(seconds=10),
            timedelta(seconds=20),
            timedelta(seconds=40),
            timedelta(seconds=80),
            timedelta(seconds=160),
        ]
        assert delays == expected


# ---------------------------------------------------------------------------
# WebhookRetryService 测试
# ---------------------------------------------------------------------------


class TestWebhookRetryService:
    """Webhook 重试服务测试。"""

    @pytest.mark.asyncio
    async def test_handle_failure_creates_entry(self):
        """handle_failure 应创建一个 DLQ 条目。"""
        dlq = InMemoryDeadLetterQueue()
        service = WebhookRetryService(dlq=dlq)
        await service.handle_failure(
            source="plane",
            event_type="issue.updated",
            payload={"issue_id": "123"},
            error="connection refused",
        )
        entries = await dlq.list_entries()
        assert len(entries) == 1
        assert entries[0].source == "plane"
        assert entries[0].error_message == "connection refused"
        assert entries[0].next_retry_at is not None

    @pytest.mark.asyncio
    async def test_process_retries_success(self):
        """成功处理后条目应标记为 resolved。"""
        dlq = InMemoryDeadLetterQueue()
        service = WebhookRetryService(dlq=dlq)
        entry = DeadLetterEntry(
            source="plane",
            event_type="issue.updated",
            payload={"key": "val"},
            error_message="error",
            next_retry_at=_utc_now() - timedelta(minutes=1),
        )
        await dlq.enqueue(entry)

        handler = AsyncMock()
        count = await service.process_retries(handler)
        assert count == 1
        handler.assert_called_once_with("plane", "issue.updated", {"key": "val"})
        entries = await dlq.list_entries(status="resolved")
        assert len(entries) == 1

    @pytest.mark.asyncio
    async def test_process_retries_failure_updates_retry(self):
        """处理失败后应更新重试计数。"""
        dlq = InMemoryDeadLetterQueue()
        service = WebhookRetryService(dlq=dlq, max_retries=5)
        entry = DeadLetterEntry(
            source="gitlab",
            event_type="push",
            payload={},
            error_message="error",
            retry_count=0,
            max_retries=5,
            next_retry_at=_utc_now() - timedelta(minutes=1),
        )
        await dlq.enqueue(entry)

        handler = AsyncMock(side_effect=Exception("still failing"))
        count = await service.process_retries(handler)
        assert count == 1
        entries = await dlq.list_entries(status="retrying")
        assert len(entries) == 1
        assert entries[0].retry_count == 1

    @pytest.mark.asyncio
    async def test_process_retries_exhausted(self):
        """达到最大重试次数后应标记为 exhausted。"""
        dlq = InMemoryDeadLetterQueue()
        service = WebhookRetryService(dlq=dlq, max_retries=3)
        entry = DeadLetterEntry(
            source="plane",
            event_type="issue.updated",
            payload={},
            error_message="error",
            retry_count=2,
            max_retries=3,
            next_retry_at=_utc_now() - timedelta(minutes=1),
        )
        await dlq.enqueue(entry)

        handler = AsyncMock(side_effect=Exception("permanent failure"))
        count = await service.process_retries(handler)
        assert count == 1
        entries = await dlq.list_entries(status="exhausted")
        assert len(entries) == 1

    @pytest.mark.asyncio
    async def test_process_retries_no_ready_entries(self):
        """没有就绪条目时应返回 0。"""
        dlq = InMemoryDeadLetterQueue()
        service = WebhookRetryService(dlq=dlq)
        handler = AsyncMock()
        count = await service.process_retries(handler)
        assert count == 0
        handler.assert_not_called()
