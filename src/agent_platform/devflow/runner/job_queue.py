"""Async job queue for coding runner execution.

Manages concurrent runner executions with configurable limits,
proper state tracking, and graceful shutdown support.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from agent_platform.devflow.runner.models import CodingJob

logger = logging.getLogger(__name__)


@dataclass
class _QueueEntry:
    job_id: str
    coro_factory: Callable[[], Coroutine[Any, Any, CodingJob]]
    future: asyncio.Future[CodingJob]


class AsyncJobQueue:
    """Bounded async queue for coding runner jobs.

    Limits concurrency via a semaphore and supports graceful shutdown
    (waits for running jobs to finish, cancels queued ones).
    """

    def __init__(self, *, max_concurrent: int = 3):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._running: dict[str, asyncio.Task[CodingJob]] = {}
        self._shutdown = False
        self._on_complete: Callable[[CodingJob], Any] | None = None

    @property
    def running_count(self) -> int:
        return len(self._running)

    @property
    def is_shutdown(self) -> bool:
        return self._shutdown

    def set_on_complete(self, callback: Callable[[CodingJob], Any]) -> None:
        self._on_complete = callback

    async def submit(
        self,
        job_id: str,
        coro_factory: Callable[[], Coroutine[Any, Any, CodingJob]],
    ) -> asyncio.Task[CodingJob]:
        """Submit a job for async execution. Returns an asyncio.Task."""
        if self._shutdown:
            raise RuntimeError("Job queue is shutting down, cannot accept new jobs")

        task = asyncio.create_task(
            self._run_with_semaphore(job_id, coro_factory),
            name=f"devflow-job-{job_id}",
        )
        self._running[job_id] = task
        task.add_done_callback(lambda t: self._running.pop(job_id, None))
        logger.info(
            "Job %s submitted (running=%d, max=%d)",
            job_id, len(self._running), self._max_concurrent,
        )
        return task

    async def _run_with_semaphore(
        self,
        job_id: str,
        coro_factory: Callable[[], Coroutine[Any, Any, CodingJob]],
    ) -> CodingJob:
        async with self._semaphore:
            logger.info("Job %s acquired slot, starting execution", job_id)
            try:
                job = await coro_factory()
                if self._on_complete:
                    try:
                        result = self._on_complete(job)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception:
                        logger.warning("on_complete callback failed for %s", job_id)
                return job
            except Exception:
                logger.exception("Job %s failed with unhandled exception", job_id)
                raise

    async def shutdown(self, *, timeout: float = 30.0) -> None:
        """Graceful shutdown: wait for running jobs, cancel if timeout exceeded."""
        self._shutdown = True
        if not self._running:
            logger.info("Job queue shutdown: no running jobs")
            return

        logger.info("Job queue shutdown: waiting for %d running jobs", len(self._running))
        tasks = list(self._running.values())
        done, pending = await asyncio.wait(tasks, timeout=timeout)

        if pending:
            logger.warning(
                "Job queue shutdown: %d jobs did not finish in %.0fs, cancelling",
                len(pending), timeout,
            )
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

        logger.info("Job queue shutdown complete")

    async def close(self) -> None:
        """Alias for shutdown — compatible with app.state._closeables pattern."""
        await self.shutdown()

    def get_stats(self) -> dict[str, Any]:
        return {
            "running": self.running_count,
            "max_concurrent": self._max_concurrent,
            "shutdown": self._shutdown,
            "job_ids": list(self._running.keys()),
        }
