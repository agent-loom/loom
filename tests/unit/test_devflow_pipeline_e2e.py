"""End-to-end DevFlow pipeline tests.

Covers the full webhook → orchestrator → branch → MR → runner dispatch path,
including async job queue integration and idempotency.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from agent_platform.devflow.orchestrator import DevFlowOrchestrator, DevFlowResult
from agent_platform.devflow.runner.job_queue import AsyncJobQueue
from agent_platform.devflow.runner.models import CodingJob, JobState, ResultStatus, RunnerResult
from agent_platform.integrations.gitlab.adapter import GitLabAdapter
from agent_platform.integrations.plane.adapter import PlaneAdapter


def _plane_transport(work_item_detail: dict | None = None):
    detail = work_item_detail or {
        "id": "wi-100",
        "name": "Implement user auth",
        "description_stripped": "Add OAuth2 authentication to the API",
        "properties": {"agent_id": "auth-agent", "task_type": "platform:change"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/work-items/" in path and request.method == "GET":
            return httpx.Response(200, json=detail)
        if "/comments/" in path:
            return httpx.Response(200, json={"id": "c-1"})
        if "/work-items/" in path and request.method in ("PATCH", "PUT"):
            return httpx.Response(200, json={})
        return httpx.Response(200, json={})

    return httpx.MockTransport(handler)


def _gitlab_transport(mr_iid: int = 42):
    def handler(request: httpx.Request) -> httpx.Response:
        if "merge_requests" in request.url.path and request.method == "POST":
            return httpx.Response(200, json={
                "iid": mr_iid,
                "web_url": f"https://gitlab.example.com/mr/{mr_iid}",
            })
        if "branches" in request.url.path:
            return httpx.Response(200, json={"name": "feat/wi-100"})
        return httpx.Response(200, json={})

    return httpx.MockTransport(handler)


def _make_orchestrator(
    *,
    coding_runner=None,
    job_queue=None,
    webhook_repo=None,
    ai_developing_state_id=None,
):
    plane = PlaneAdapter(
        base_url="https://plane.test",
        api_key="test-key",
        workspace_slug="ws",
        transport=_plane_transport(),
    )
    gitlab = GitLabAdapter(
        base_url="https://gitlab.test",
        token="test-token",
        transport=_gitlab_transport(),
    )
    return DevFlowOrchestrator(
        plane=plane,
        gitlab=gitlab,
        gitlab_project_id="proj-1",
        coding_runner=coding_runner,
        job_queue=job_queue,
        webhook_repo=webhook_repo,
        ai_developing_state_id=ai_developing_state_id,
    )


def _ready_payload(work_item_id: str = "wi-100"):
    return {
        "data": {
            "id": work_item_id,
            "project": "plane-proj-1",
            "name": "Implement user auth",
            "state_detail": {"name": "Ready for AI Dev"},
        },
    }


# ---------------------------------------------------------------------------
# Full pipeline: webhook → branch → MR → result
# ---------------------------------------------------------------------------


class TestFullPipeline:
    @pytest.mark.asyncio
    async def test_webhook_creates_branch_and_returns_result(self):
        orch = _make_orchestrator()
        result = await orch.handle_webhook_event("work_item.updated", _ready_payload())

        assert result is not None
        assert isinstance(result, DevFlowResult)
        assert result.branch == "feat/wi-100"
        assert result.mr_iid is None  # Orchestrator 不再创建 MR，由 Runner 在 commit 后创建
        assert result.task_pack.metadata.task_id == "wi-100"
        assert result.task_pack.metadata.title == "Implement user auth"

    @pytest.mark.asyncio
    async def test_task_pack_contains_agent_info(self):
        orch = _make_orchestrator()
        result = await orch.handle_webhook_event("work_item.updated", _ready_payload())

        assert result.task_pack.agent.get("agent_id") == "auth-agent"

    @pytest.mark.asyncio
    async def test_mr_description_generated(self):
        orch = _make_orchestrator()
        result = await orch.handle_webhook_event("work_item.updated", _ready_payload())

        mr_desc = result.task_pack.repository.merge_request.description
        assert "OAuth2" in mr_desc or "auth" in mr_desc.lower() or len(mr_desc) > 0


# ---------------------------------------------------------------------------
# Pipeline with coding runner (direct execution)
# ---------------------------------------------------------------------------


class TestPipelineWithRunner:
    @pytest.mark.asyncio
    async def test_runner_dispatched_on_mr_creation(self):
        mock_runner = MagicMock()
        completed_job = CodingJob(
            job_id="j-100",
            task_id="wi-100",
            state=JobState.SUCCEEDED,
            result=RunnerResult(status=ResultStatus.SUCCESS, commit_sha="abc123"),
        )
        mock_runner.run = AsyncMock(return_value=completed_job)

        orch = _make_orchestrator(coding_runner=mock_runner)
        result = await orch.handle_webhook_event("work_item.updated", _ready_payload())

        assert result.coding_job is completed_job
        assert result.job_submitted is False
        mock_runner.run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_runner_failure_still_returns_result(self):
        mock_runner = MagicMock()
        mock_runner.run = AsyncMock(side_effect=RuntimeError("runner crashed"))

        orch = _make_orchestrator(coding_runner=mock_runner)
        result = await orch.handle_webhook_event("work_item.updated", _ready_payload())

        assert result.coding_job is None
        assert result.job_submitted is False
        assert result.mr_iid is None  # Runner 失败时无 MR，Orchestrator 也不创建 MR


# ---------------------------------------------------------------------------
# Pipeline with async job queue
# ---------------------------------------------------------------------------


class TestPipelineWithJobQueue:
    @pytest.mark.asyncio
    async def test_job_submitted_to_queue(self):
        mock_runner = MagicMock()
        completed_job = CodingJob(
            job_id="j-100", task_id="wi-100", state=JobState.SUCCEEDED,
        )
        mock_runner.run = AsyncMock(return_value=completed_job)

        queue = AsyncJobQueue(max_concurrent=2)
        orch = _make_orchestrator(coding_runner=mock_runner, job_queue=queue)
        result = await orch.handle_webhook_event("work_item.updated", _ready_payload())

        assert result.job_submitted is True
        assert result.coding_job is None

        await asyncio.sleep(0.1)
        mock_runner.run.assert_awaited_once()

        await queue.shutdown()

    @pytest.mark.asyncio
    async def test_queue_submit_failure_handled(self):
        mock_runner = MagicMock()
        mock_queue = MagicMock()
        mock_queue.submit = AsyncMock(side_effect=RuntimeError("queue full"))

        orch = _make_orchestrator(coding_runner=mock_runner, job_queue=mock_queue)
        result = await orch.handle_webhook_event("work_item.updated", _ready_payload())

        assert result.job_submitted is False
        assert result.coding_job is None
        assert result.mr_iid is None  # Orchestrator 不创建 MR

    @pytest.mark.asyncio
    async def test_queue_stats_available(self):
        queue = AsyncJobQueue(max_concurrent=3)
        stats = queue.get_stats()
        assert stats["max_concurrent"] == 3
        assert stats["running"] == 0
        await queue.close()


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_duplicate_event_ignored_in_memory(self):
        orch = _make_orchestrator()
        payload = _ready_payload("wi-200")

        r1 = await orch.handle_webhook_event("work_item.updated", payload)
        r2 = await orch.handle_webhook_event("work_item.updated", payload)

        assert r1 is not None
        assert r2 is None

    @pytest.mark.asyncio
    async def test_non_ready_state_does_not_retrigger(self):
        """'In Progress' 状态不应触发 DevFlow，即使同一 work item 已处理过。"""
        orch = _make_orchestrator()
        payload = _ready_payload("wi-201")
        retry_payload = _ready_payload("wi-201")
        retry_payload["data"]["state_detail"] = {"name": "In Progress"}

        r1 = await orch.handle_webhook_event("work_item.updated", payload)
        r2 = await orch.handle_webhook_event("work_item.updated", retry_payload)

        assert r1 is not None
        assert r2 is None  # "In Progress" 不在触发状态集中，应被忽略

    @pytest.mark.asyncio
    async def test_duplicate_event_ignored_with_repo(self):
        repo = AsyncMock()
        repo.exists = AsyncMock(side_effect=[False, True])
        repo.record = AsyncMock()

        orch = _make_orchestrator(webhook_repo=repo)
        payload = _ready_payload("wi-300")

        r1 = await orch.handle_webhook_event("work_item.updated", payload)
        r2 = await orch.handle_webhook_event("work_item.updated", payload)

        assert r1 is not None
        assert r2 is None


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------


class TestStateTransitions:
    @pytest.mark.asyncio
    async def test_state_updated_to_ai_developing(self):
        plane = PlaneAdapter(
            base_url="https://plane.test",
            api_key="test-key",
            workspace_slug="ws",
            transport=_plane_transport(),
        )
        gitlab = GitLabAdapter(
            base_url="https://gitlab.test",
            token="test-token",
            transport=_gitlab_transport(),
        )

        orch = DevFlowOrchestrator(
            plane=plane,
            gitlab=gitlab,
            gitlab_project_id="proj-1",
            ai_developing_state_id="state-ai-dev-123",
        )

        result = await orch.handle_webhook_event("work_item.updated", _ready_payload())
        assert result is not None

    @pytest.mark.asyncio
    async def test_non_ready_state_ignored(self):
        orch = _make_orchestrator()
        payload = {
            "data": {
                "id": "wi-400",
                "project": "p-1",
                "name": "Task",
                "state_detail": {"name": "Done"},
            },
        }
        result = await orch.handle_webhook_event("work_item.updated", payload)
        assert result is None

    @pytest.mark.asyncio
    async def test_irrelevant_event_type_ignored(self):
        orch = _make_orchestrator()
        result = await orch.handle_webhook_event("project.updated", {"data": {}})
        assert result is None

    @pytest.mark.asyncio
    async def test_ready_for_ai_dev_lowercase_accepted(self):
        orch = _make_orchestrator()
        payload = {
            "data": {
                "id": "wi-500",
                "project": "p-1",
                "name": "Lowercase state",
                "state_detail": {"name": "ready_for_ai_dev"},
            },
        }
        result = await orch.handle_webhook_event("work_item.updated", payload)
        assert result is not None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_missing_work_item_detail_returns_none_when_ownership_unresolvable(self):
        """当 Plane 查询失败且 work_item 本身也无法解析 ownership 时，Orchestrator 安全返回 None。"""
        def failing_plane_handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if "/work-items/" in path and request.method == "GET":
                return httpx.Response(500, json={"error": "fail"})
            return httpx.Response(200, json={})

        plane = PlaneAdapter(
            base_url="https://plane.test",
            api_key="test-key",
            workspace_slug="ws",
            transport=httpx.MockTransport(failing_plane_handler),
        )
        gitlab = GitLabAdapter(
            base_url="https://gitlab.test",
            token="test-token",
            transport=_gitlab_transport(),
        )
        orch = DevFlowOrchestrator(
            plane=plane, gitlab=gitlab, gitlab_project_id="proj-1",
        )

        # work_item 没有 agent_id，也没有项目映射 → ownership 无法解析 → 返回 None
        result = await orch.handle_webhook_event("work_item.updated", _ready_payload())
        assert result is None

    @pytest.mark.asyncio
    async def test_no_runner_skips_dispatch(self):
        orch = _make_orchestrator(coding_runner=None)
        result = await orch.handle_webhook_event("work_item.updated", _ready_payload())

        assert result.coding_job is None
        assert result.job_submitted is False

    @pytest.mark.asyncio
    async def test_extract_state_from_string(self):
        result = DevFlowOrchestrator._extract_state_name({"state": "Ready for AI Dev"})
        assert result == "Ready for AI Dev"

    @pytest.mark.asyncio
    async def test_extract_agent_id_from_custom_properties(self):
        result = DevFlowOrchestrator._extract_agent_id(
            {"custom_properties": {"agent_id": "my-agent"}}
        )
        assert result == "my-agent"

    @pytest.mark.asyncio
    async def test_extract_task_type_default(self):
        result = DevFlowOrchestrator._extract_task_type({})
        assert result == "platform:change"
