"""S8 Phase 2 — Runner 端到端联调测试。

覆盖执行日志接入、MR 元数据嵌入、Orchestrator state_sync 接线、Admin Job API。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_platform.devflow.orchestrator import DevFlowOrchestrator
from agent_platform.devflow.runner.execution_log import (
    ExecutionLogEntry,
    InMemoryExecutionLogRepository,
    LogStream,
)
from agent_platform.devflow.runner.models import JobState, ValidationResult
from agent_platform.devflow.runner.protocol import RunnerAdapterResult
from agent_platform.devflow.runner.runner import CodingAgentRunner
from agent_platform.devflow.state_sync import DevFlowStateSync
from agent_platform.devflow.task_pack import (
    DevelopmentTask,
    RepositoryTarget,
    RequirementSpec,
    TaskMetadata,
)


def _make_task(**overrides) -> DevelopmentTask:
    defaults = dict(
        metadata=TaskMetadata(title="测试任务", task_id="T-100", type="feature"),
        repository=RepositoryTarget(project_id="proj-1", work_branch="feat/test"),
        requirement=RequirementSpec(background="修复 XSS"),
        scope={"write_allowed": ["src/**"], "write_denied": []},
        validation={"commands": []},
    )
    defaults.update(overrides)
    return DevelopmentTask(**defaults)


class TestExecutionLogWiring:
    """验证 CodingAgentRunner 正确将 adapter 输出写入日志仓库。"""

    @pytest.mark.asyncio
    async def test_adapter_output_recorded_to_log_repo(self):
        log_repo = InMemoryExecutionLogRepository()
        mock_adapter = MagicMock()
        mock_adapter.adapter_type = "mock"
        mock_adapter.execute = AsyncMock(return_value=RunnerAdapterResult(
            exit_code=0, stdout="代码已生成", stderr="警告信息",
        ))

        mock_ws = MagicMock()
        mock_ws.create = AsyncMock(return_value="/tmp/ws")
        mock_ws.get_changed_files = AsyncMock(return_value=["src/main.py"])
        mock_ws.run_validation = AsyncMock(return_value=ValidationResult(
            all_passed=True, commands_executed=[],
        ))
        mock_ws.commit_and_push = AsyncMock(return_value="abc123")
        mock_ws.cleanup = AsyncMock()
        mock_ws.cleanup_on_failure = True
        mock_ws.cleanup_on_success = True

        mock_gitlab = MagicMock()
        mock_gitlab.comment_merge_request = AsyncMock()

        runner = CodingAgentRunner(
            adapter=mock_adapter,
            workspace_manager=mock_ws,
            gitlab=mock_gitlab,
            gitlab_project_id="proj-1",
            repo_url="https://git.example.com/repo.git",
            log_repo=log_repo,
        )

        task = _make_task()
        job = await runner.run(task)

        assert job.state == JobState.SUCCEEDED
        stdout_logs = await log_repo.get_logs(job.job_id, stream=LogStream.STDOUT)
        stderr_logs = await log_repo.get_logs(job.job_id, stream=LogStream.STDERR)
        assert len(stdout_logs) == 1
        assert stdout_logs[0].content == "代码已生成"
        assert stdout_logs[0].adapter_name == "mock"
        assert len(stderr_logs) == 1
        assert stderr_logs[0].content == "警告信息"

    @pytest.mark.asyncio
    async def test_no_log_when_repo_is_none(self):
        """log_repo 为 None 时不报错。"""
        mock_adapter = MagicMock()
        mock_adapter.adapter_type = "mock"
        mock_adapter.execute = AsyncMock(return_value=RunnerAdapterResult(
            exit_code=0, stdout="ok", stderr="",
        ))
        mock_ws = MagicMock()
        mock_ws.create = AsyncMock(return_value="/tmp/ws")
        mock_ws.get_changed_files = AsyncMock(return_value=[])
        mock_ws.run_validation = AsyncMock(return_value=ValidationResult(
            all_passed=True, commands_executed=[],
        ))
        mock_ws.cleanup = AsyncMock()
        mock_ws.cleanup_on_failure = True
        mock_ws.cleanup_on_success = True

        runner = CodingAgentRunner(
            adapter=mock_adapter,
            workspace_manager=mock_ws,
            gitlab=MagicMock(),
            gitlab_project_id="proj-1",
            repo_url="https://git.example.com/repo.git",
            log_repo=None,
        )
        job = await runner.run(_make_task())
        assert job.state == JobState.SUCCEEDED


class TestMRMetadataEmbed:
    """验证 Orchestrator 在 MR 描述中嵌入 Plane 元数据。"""

    @pytest.mark.asyncio
    async def test_mr_description_includes_plane_ids(self):
        mock_plane = AsyncMock()
        mock_plane.get_work_item = AsyncMock(return_value={
            "name": "测试需求",
            "description_stripped": "修复登录 XSS",
        })
        mock_plane.add_comment = AsyncMock()
        mock_plane.update_work_item_state = AsyncMock()
        mock_plane.update_custom_properties = AsyncMock()

        mock_gitlab = AsyncMock()
        mock_gitlab.create_branch = AsyncMock()
        mock_gitlab.create_merge_request = AsyncMock(return_value=MagicMock(
            url="https://gitlab.com/mr/1", mr_id=1,
        ))

        orch = DevFlowOrchestrator(
            plane=mock_plane,
            gitlab=mock_gitlab,
            gitlab_project_id="gp-1",
            ai_developing_state_id="state-ai-dev",
        )

        result = await orch.handle_webhook_event("work_item.updated", {
            "data": {
                "id": "wi-99",
                "project": "pp-1",
                "name": "测试需求",
                "state_detail": {"name": "Ready for AI Dev"},
            },
        })

        assert result is not None
        call_args = mock_gitlab.create_merge_request.call_args
        description = call_args.kwargs.get("description", "")
        assert "plane_project_id=pp-1" in description
        assert "plane_work_item_id=wi-99" in description


class TestOrchestratorStateSyncWiring:
    """验证 Orchestrator 的 state_sync 接线。"""

    @pytest.mark.asyncio
    async def test_state_sync_called_when_wired(self):
        mock_state_sync = AsyncMock(spec=DevFlowStateSync)
        mock_state_sync.sync_to_plane = AsyncMock()

        mock_plane = AsyncMock()
        mock_plane.get_work_item = AsyncMock(return_value={
            "name": "任务", "description_stripped": "描述",
        })
        mock_plane.add_comment = AsyncMock()
        mock_plane.update_work_item_state = AsyncMock()
        mock_plane.update_custom_properties = AsyncMock()

        mock_gitlab = AsyncMock()
        mock_gitlab.create_branch = AsyncMock()
        mock_gitlab.create_merge_request = AsyncMock(return_value=MagicMock(
            url="https://gitlab.com/mr/2", mr_id=2,
        ))

        orch = DevFlowOrchestrator(
            plane=mock_plane,
            gitlab=mock_gitlab,
            gitlab_project_id="gp-1",
            state_sync=mock_state_sync,
        )

        await orch.handle_webhook_event("work_item.updated", {
            "data": {
                "id": "wi-200",
                "project": "pp-2",
                "name": "任务",
                "state_detail": {"name": "Ready for AI Dev"},
            },
        })

        assert mock_state_sync.sync_to_plane.call_count >= 1


@pytest.mark.asyncio
class TestExecutionLogRepository:
    """InMemoryExecutionLogRepository 扩展测试。"""

    async def test_list_jobs_ordering(self):
        repo = InMemoryExecutionLogRepository()
        await repo.record(ExecutionLogEntry(
            job_id="job-a", stream=LogStream.STDOUT, content="first",
        ))
        await repo.record(ExecutionLogEntry(
            job_id="job-b", stream=LogStream.STDOUT, content="second",
        ))
        await repo.record(ExecutionLogEntry(
            job_id="job-a", stream=LogStream.STDERR, content="third",
        ))

        jobs = await repo.list_jobs_with_logs()
        assert jobs[0] == "job-a"

    async def test_filter_by_stream(self):
        repo = InMemoryExecutionLogRepository()
        await repo.record(ExecutionLogEntry(
            job_id="job-x", stream=LogStream.STDOUT, content="out",
        ))
        await repo.record(ExecutionLogEntry(
            job_id="job-x", stream=LogStream.STDERR, content="err",
        ))

        stdout = await repo.get_logs("job-x", stream=LogStream.STDOUT)
        assert len(stdout) == 1
        assert stdout[0].content == "out"
