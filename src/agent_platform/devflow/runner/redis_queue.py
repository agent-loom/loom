"""Redis-backed distributed job queue for coding runner execution.

Provides multi-instance horizontal scaling by using Redis as a shared
job registry and coordination layer. Falls back to AsyncJobQueue when
Redis is not available.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from agent_platform.devflow.runner.models import CodingJob

logger = logging.getLogger(__name__)


class RedisJobQueue:
    """Redis-backed distributed job queue.

    Uses Redis for job state persistence and distributed locking,
    while actual execution still happens in-process via asyncio tasks.
    This enables multi-instance deployments where each instance picks
    up jobs and the state is visible across all instances.
    """

    def __init__(
        self,
        *,
        redis_url: str = "redis://localhost:6379/0",
        max_concurrent: int = 3,
        instance_id: str | None = None,
        key_prefix: str = "devflow:jobs",
    ):
        self._redis_url = redis_url
        self._max_concurrent = max_concurrent
        self._instance_id = instance_id or uuid.uuid4().hex[:8]
        self._key_prefix = key_prefix
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._running: dict[str, asyncio.Task[CodingJob]] = {}
        self._shutdown = False
        self._redis = None
        self._on_complete: Callable[[CodingJob], Any] | None = None

    async def _ensure_redis(self):
        if self._redis is not None:
            return self._redis
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
            )
            await self._redis.ping()
            logger.info(
                "RedisJobQueue connected (url=%s, instance=%s)",
                self._redis_url, self._instance_id,
            )
            return self._redis
        except Exception:
            logger.warning(
                "Redis not available at %s, job state will be local only",
                self._redis_url,
            )
            self._redis = None
            return None

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
        if self._shutdown:
            raise RuntimeError("Job queue is shutting down, cannot accept new jobs")

        redis = await self._ensure_redis()
        if redis is not None:
            await self._record_job_state(redis, job_id, "queued")

        task = asyncio.create_task(
            self._run_with_semaphore(job_id, coro_factory),
            name=f"devflow-redis-job-{job_id}",
        )
        self._running[job_id] = task
        task.add_done_callback(lambda t: self._running.pop(job_id, None))
        logger.info(
            "Job %s submitted to RedisJobQueue (instance=%s, running=%d, max=%d)",
            job_id, self._instance_id, len(self._running), self._max_concurrent,
        )
        return task

    async def _run_with_semaphore(
        self,
        job_id: str,
        coro_factory: Callable[[], Coroutine[Any, Any, CodingJob]],
    ) -> CodingJob:
        redis = await self._ensure_redis()
        async with self._semaphore:
            if redis is not None:
                await self._record_job_state(redis, job_id, "running")
            logger.info("Job %s acquired slot on instance %s", job_id, self._instance_id)
            try:
                job = await coro_factory()
                if redis is not None:
                    await self._record_job_state(
                        redis, job_id, "completed",
                        extra={"result_status": job.state.value if job.state else "unknown"},
                    )
                if self._on_complete:
                    try:
                        result = self._on_complete(job)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception:
                        logger.warning("on_complete callback failed for %s", job_id)
                return job
            except Exception:
                if redis is not None:
                    await self._record_job_state(redis, job_id, "failed")
                logger.exception("Job %s failed with unhandled exception", job_id)
                raise

    async def _record_job_state(
        self,
        redis,
        job_id: str,
        state: str,
        *,
        extra: dict[str, str] | None = None,
    ) -> None:
        try:
            key = f"{self._key_prefix}:{job_id}"
            data = {
                "job_id": job_id,
                "state": state,
                "instance_id": self._instance_id,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            if extra:
                data.update(extra)
            await redis.hset(key, mapping=data)
            await redis.expire(key, 86400)
        except Exception:
            logger.debug("Failed to record job state in Redis for %s", job_id)

    async def get_job_state(self, job_id: str) -> dict[str, str] | None:
        redis = await self._ensure_redis()
        if redis is None:
            return None
        try:
            key = f"{self._key_prefix}:{job_id}"
            data = await redis.hgetall(key)
            return data if data else None
        except Exception:
            return None

    async def list_jobs(self) -> list[dict[str, str]]:
        redis = await self._ensure_redis()
        if redis is None:
            return [
                {"job_id": jid, "state": "running", "instance_id": self._instance_id}
                for jid in self._running
            ]
        try:
            pattern = f"{self._key_prefix}:*"
            jobs = []
            async for key in redis.scan_iter(match=pattern, count=100):
                data = await redis.hgetall(key)
                if data:
                    jobs.append(data)
            return jobs
        except Exception:
            return []

    async def shutdown(self, *, timeout: float = 30.0) -> None:
        self._shutdown = True
        if not self._running:
            logger.info("RedisJobQueue shutdown: no running jobs (instance=%s)", self._instance_id)
            return

        logger.info(
            "RedisJobQueue shutdown: waiting for %d jobs (instance=%s)",
            len(self._running), self._instance_id,
        )
        tasks = list(self._running.values())
        done, pending = await asyncio.wait(tasks, timeout=timeout)

        if pending:
            logger.warning(
                "RedisJobQueue shutdown: %d jobs timed out, cancelling",
                len(pending),
            )
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

        if self._redis is not None:
            await self._redis.aclose()
        logger.info("RedisJobQueue shutdown complete (instance=%s)", self._instance_id)

    async def close(self) -> None:
        await self.shutdown()

    def get_stats(self) -> dict[str, Any]:
        return {
            "type": "redis",
            "running": self.running_count,
            "max_concurrent": self._max_concurrent,
            "shutdown": self._shutdown,
            "instance_id": self._instance_id,
            "redis_url": self._redis_url,
            "job_ids": list(self._running.keys()),
        }
