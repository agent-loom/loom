from __future__ import annotations

import pytest

from agent_platform.persistence.memory import InMemoryCodingJobRepository


class TestInMemoryCodingJobRepository:
    @pytest.mark.asyncio
    async def test_save_and_get(self):
        repo = InMemoryCodingJobRepository()
        job = {"job_id": "j-1", "state": "pending", "task_id": "t-1"}
        await repo.save(job)
        result = await repo.get("j-1")
        assert result == job

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self):
        repo = InMemoryCodingJobRepository()
        assert await repo.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_list_jobs_all(self):
        repo = InMemoryCodingJobRepository()
        await repo.save({"job_id": "j-1", "state": "pending"})
        await repo.save({"job_id": "j-2", "state": "succeeded"})
        jobs = await repo.list_jobs()
        assert len(jobs) == 2

    @pytest.mark.asyncio
    async def test_list_jobs_by_status(self):
        repo = InMemoryCodingJobRepository()
        await repo.save({"job_id": "j-1", "state": "pending"})
        await repo.save({"job_id": "j-2", "state": "succeeded"})
        await repo.save({"job_id": "j-3", "state": "pending"})
        jobs = await repo.list_jobs(status="pending")
        assert len(jobs) == 2
        assert all(j["state"] == "pending" for j in jobs)

    @pytest.mark.asyncio
    async def test_list_jobs_limit(self):
        repo = InMemoryCodingJobRepository()
        for i in range(10):
            await repo.save({"job_id": f"j-{i}", "state": "pending"})
        jobs = await repo.list_jobs(limit=3)
        assert len(jobs) == 3

    @pytest.mark.asyncio
    async def test_save_overwrites(self):
        repo = InMemoryCodingJobRepository()
        await repo.save({"job_id": "j-1", "state": "pending"})
        await repo.save({"job_id": "j-1", "state": "succeeded"})
        result = await repo.get("j-1")
        assert result["state"] == "succeeded"
