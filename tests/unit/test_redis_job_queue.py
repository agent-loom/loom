"""Tests for RedisJobQueue — src/agent_platform/devflow/runner/redis_queue.py"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from agent_platform.devflow.runner.models import CodingJob
from agent_platform.devflow.runner.redis_queue import RedisJobQueue


def _make_job(job_id: str = "j-1") -> CodingJob:
    return CodingJob(job_id=job_id, task_id=f"task-{job_id}")


class TestConstruction:
    def test_defaults(self):
        q = RedisJobQueue()
        assert q.running_count == 0
        assert q.is_shutdown is False
        assert q._max_concurrent == 3

    def test_custom_params(self):
        q = RedisJobQueue(
            redis_url="redis://custom:6380/1",
            max_concurrent=10,
            instance_id="node-7",
            key_prefix="test:jobs",
        )
        assert q._redis_url == "redis://custom:6380/1"
        assert q._max_concurrent == 10
        assert q._instance_id == "node-7"
        assert q._key_prefix == "test:jobs"


class TestSubmitAndExecution:
    @pytest.mark.asyncio
    async def test_submit_executes_and_returns_result(self):
        q = RedisJobQueue(max_concurrent=2)
        q._redis = None
        job = _make_job()

        async def factory():
            return job

        with patch.object(q, "_ensure_redis", new_callable=AsyncMock, return_value=None):
            task = await q.submit("j-1", factory)
            result = await task
        assert result is job

    @pytest.mark.asyncio
    async def test_submit_rejected_after_shutdown(self):
        q = RedisJobQueue()
        q._shutdown = True
        with pytest.raises(RuntimeError, match="shutting down"):
            await q.submit("j-1", AsyncMock())

    @pytest.mark.asyncio
    async def test_running_count_tracks_active_jobs(self):
        q = RedisJobQueue(max_concurrent=5)
        barrier = asyncio.Event()
        job = _make_job()

        async def slow_factory():
            await barrier.wait()
            return job

        with patch.object(q, "_ensure_redis", new_callable=AsyncMock, return_value=None):
            task = await q.submit("j-slow", slow_factory)
            await asyncio.sleep(0.01)
            assert q.running_count >= 1
            barrier.set()
            await task
        assert q.running_count == 0


class TestConcurrencyLimiting:
    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        q = RedisJobQueue(max_concurrent=1)
        order = []
        job = _make_job()

        async def factory_a():
            order.append("a-start")
            await asyncio.sleep(0.05)
            order.append("a-end")
            return job

        async def factory_b():
            order.append("b-start")
            return _make_job("j-2")

        with patch.object(q, "_ensure_redis", new_callable=AsyncMock, return_value=None):
            t1 = await q.submit("j-a", factory_a)
            t2 = await q.submit("j-b", factory_b)
            await asyncio.gather(t1, t2)

        assert order.index("a-end") < order.index("b-start")


class TestOnCompleteCallback:
    @pytest.mark.asyncio
    async def test_on_complete_called(self):
        q = RedisJobQueue(max_concurrent=2)
        received = []
        q.set_on_complete(lambda j: received.append(j.job_id))

        job = _make_job("cb-1")

        async def factory():
            return job

        with patch.object(q, "_ensure_redis", new_callable=AsyncMock, return_value=None):
            task = await q.submit("cb-1", factory)
            await task

        assert "cb-1" in received

    @pytest.mark.asyncio
    async def test_on_complete_async_callback(self):
        q = RedisJobQueue(max_concurrent=2)
        received = []

        async def async_cb(j):
            received.append(j.job_id)

        q.set_on_complete(async_cb)
        job = _make_job("async-cb")

        async def factory():
            return job

        with patch.object(q, "_ensure_redis", new_callable=AsyncMock, return_value=None):
            task = await q.submit("async-cb", factory)
            await task

        assert "async-cb" in received


class TestStats:
    def test_get_stats_structure(self):
        q = RedisJobQueue(
            redis_url="redis://localhost:6379/0",
            max_concurrent=5,
            instance_id="test-node",
        )
        stats = q.get_stats()
        assert stats["type"] == "redis"
        assert stats["running"] == 0
        assert stats["max_concurrent"] == 5
        assert stats["instance_id"] == "test-node"
        assert stats["shutdown"] is False
        assert stats["job_ids"] == []


class TestShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_with_no_jobs(self):
        q = RedisJobQueue()
        await q.shutdown()
        assert q.is_shutdown is True

    @pytest.mark.asyncio
    async def test_shutdown_waits_for_running_jobs(self):
        q = RedisJobQueue(max_concurrent=2)
        completed = []

        async def factory():
            await asyncio.sleep(0.05)
            completed.append(True)
            return _make_job()

        with patch.object(q, "_ensure_redis", new_callable=AsyncMock, return_value=None):
            await q.submit("j-1", factory)
            await q.shutdown(timeout=5.0)

        assert len(completed) == 1
        assert q.is_shutdown is True

    @pytest.mark.asyncio
    async def test_shutdown_cancels_on_timeout(self):
        q = RedisJobQueue(max_concurrent=1)

        async def slow_factory():
            await asyncio.sleep(100)
            return _make_job()

        with patch.object(q, "_ensure_redis", new_callable=AsyncMock, return_value=None):
            await q.submit("j-forever", slow_factory)
            await q.shutdown(timeout=0.1)

        assert q.is_shutdown is True

    @pytest.mark.asyncio
    async def test_close_calls_shutdown(self):
        q = RedisJobQueue()
        await q.close()
        assert q.is_shutdown is True


class TestGracefulRedisFallback:
    @pytest.mark.asyncio
    async def test_works_without_redis(self):
        q = RedisJobQueue(redis_url="redis://nonexistent:9999/0")
        job = _make_job()

        async def factory():
            return job

        with patch.object(q, "_ensure_redis", new_callable=AsyncMock, return_value=None):
            task = await q.submit("j-nored", factory)
            result = await task

        assert result is job

    @pytest.mark.asyncio
    async def test_list_jobs_without_redis(self):
        q = RedisJobQueue()
        q._redis = None
        barrier = asyncio.Event()

        async def factory():
            await barrier.wait()
            return _make_job()

        with patch.object(q, "_ensure_redis", new_callable=AsyncMock, return_value=None):
            await q.submit("j-list", factory)
            jobs = await q.list_jobs()
            assert len(jobs) >= 1
            assert jobs[0]["state"] == "running"
            barrier.set()

    @pytest.mark.asyncio
    async def test_get_job_state_without_redis(self):
        q = RedisJobQueue()
        q._redis = None
        with patch.object(q, "_ensure_redis", new_callable=AsyncMock, return_value=None):
            state = await q.get_job_state("j-nope")
        assert state is None
