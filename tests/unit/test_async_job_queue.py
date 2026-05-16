"""Tests for AsyncJobQueue: concurrency, shutdown, stats, callbacks."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from agent_platform.devflow.runner.job_queue import AsyncJobQueue
from agent_platform.devflow.runner.models import CodingJob


def _make_job(job_id: str = "j-1") -> CodingJob:
    return CodingJob(job_id=job_id, task_id=f"task-{job_id}")


class TestSubmitAndExecution:
    @pytest.mark.asyncio
    async def test_submit_returns_task_and_executes(self):
        queue = AsyncJobQueue(max_concurrent=2)
        job = _make_job()

        async def factory():
            return job

        task = await queue.submit("j-1", factory)
        result = await task
        assert result is job
        assert queue.running_count == 0

    @pytest.mark.asyncio
    async def test_submit_multiple_jobs(self):
        queue = AsyncJobQueue(max_concurrent=5)
        results = []

        for i in range(3):
            j = _make_job(f"j-{i}")

            async def factory(jj=j):
                return jj

            task = await queue.submit(f"j-{i}", factory)
            results.append(task)

        completed = await asyncio.gather(*results)
        assert len(completed) == 3
        assert {c.job_id for c in completed} == {"j-0", "j-1", "j-2"}

    @pytest.mark.asyncio
    async def test_submit_rejected_after_shutdown(self):
        queue = AsyncJobQueue()
        await queue.shutdown()

        with pytest.raises(RuntimeError, match="shutting down"):
            await queue.submit("j-1", AsyncMock())


class TestConcurrencyLimiting:
    @pytest.mark.asyncio
    async def test_concurrency_bounded_by_semaphore(self):
        max_concurrent = 2
        queue = AsyncJobQueue(max_concurrent=max_concurrent)
        peak_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def slow_factory(jid: str):
            nonlocal peak_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                peak_concurrent = max(peak_concurrent, current_concurrent)
            await asyncio.sleep(0.05)
            async with lock:
                current_concurrent -= 1
            return _make_job(jid)

        tasks = []
        for i in range(5):
            t = await queue.submit(f"j-{i}", lambda i=i: slow_factory(f"j-{i}"))
            tasks.append(t)

        await asyncio.gather(*tasks)
        assert peak_concurrent <= max_concurrent


class TestShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_waits_for_running_jobs(self):
        queue = AsyncJobQueue(max_concurrent=2)
        completed = []

        async def factory(jid: str):
            await asyncio.sleep(0.05)
            completed.append(jid)
            return _make_job(jid)

        await queue.submit("j-1", lambda: factory("j-1"))
        await queue.submit("j-2", lambda: factory("j-2"))
        await queue.shutdown(timeout=5.0)

        assert queue.is_shutdown
        assert set(completed) == {"j-1", "j-2"}

    @pytest.mark.asyncio
    async def test_shutdown_cancels_on_timeout(self):
        queue = AsyncJobQueue(max_concurrent=1)

        async def hanging_factory():
            await asyncio.sleep(100)
            return _make_job()

        task = await queue.submit("j-hang", hanging_factory)
        await queue.shutdown(timeout=0.1)

        assert queue.is_shutdown
        assert task.done()

    @pytest.mark.asyncio
    async def test_shutdown_no_jobs_is_noop(self):
        queue = AsyncJobQueue()
        await queue.shutdown()
        assert queue.is_shutdown

    @pytest.mark.asyncio
    async def test_close_aliases_shutdown(self):
        queue = AsyncJobQueue()
        await queue.close()
        assert queue.is_shutdown


class TestOnCompleteCallback:
    @pytest.mark.asyncio
    async def test_sync_callback_invoked(self):
        queue = AsyncJobQueue()
        received = []
        queue.set_on_complete(lambda job: received.append(job.job_id))

        job = _make_job("cb-1")
        task = await queue.submit("cb-1", lambda: _async_return(job))
        await task

        assert received == ["cb-1"]

    @pytest.mark.asyncio
    async def test_async_callback_invoked(self):
        queue = AsyncJobQueue()
        received = []

        async def on_complete(job: CodingJob):
            received.append(job.job_id)

        queue.set_on_complete(on_complete)

        job = _make_job("cb-2")
        task = await queue.submit("cb-2", lambda: _async_return(job))
        await task

        assert received == ["cb-2"]

    @pytest.mark.asyncio
    async def test_callback_failure_does_not_break_job(self):
        queue = AsyncJobQueue()
        queue.set_on_complete(lambda _: (_ for _ in ()).throw(ValueError("boom")))

        job = _make_job("cb-3")
        task = await queue.submit("cb-3", lambda: _async_return(job))
        result = await task
        assert result.job_id == "cb-3"


class TestGetStats:
    @pytest.mark.asyncio
    async def test_stats_reflect_state(self):
        queue = AsyncJobQueue(max_concurrent=4)
        stats = queue.get_stats()
        assert stats["running"] == 0
        assert stats["max_concurrent"] == 4
        assert stats["shutdown"] is False
        assert stats["job_ids"] == []

    @pytest.mark.asyncio
    async def test_stats_during_execution(self):
        queue = AsyncJobQueue(max_concurrent=2)
        started = asyncio.Event()
        release = asyncio.Event()

        async def blocking_factory():
            started.set()
            await release.wait()
            return _make_job("s-1")

        task = await queue.submit("s-1", blocking_factory)
        await started.wait()

        stats = queue.get_stats()
        assert stats["running"] == 1
        assert "s-1" in stats["job_ids"]

        release.set()
        await task

    @pytest.mark.asyncio
    async def test_stats_after_shutdown(self):
        queue = AsyncJobQueue()
        await queue.shutdown()
        assert queue.get_stats()["shutdown"] is True


class TestJobFailurePropagation:
    @pytest.mark.asyncio
    async def test_factory_exception_propagates(self):
        queue = AsyncJobQueue()

        async def failing_factory():
            raise ValueError("task exploded")

        task = await queue.submit("j-fail", failing_factory)

        with pytest.raises(ValueError, match="task exploded"):
            await task

        assert queue.running_count == 0


async def _async_return(val):
    return val
