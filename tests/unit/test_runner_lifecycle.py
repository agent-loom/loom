from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_platform.devflow.runner.models import (
    CodingJob,
    JobState,
    ResultStatus,
    ValidationResult,
)
from agent_platform.devflow.runner.protocol import RunnerAdapterResult
from agent_platform.devflow.runner.runner import CodingAgentRunner
from agent_platform.devflow.runner.workspace import WorkspaceManager
from agent_platform.devflow.task_pack import (
    DevelopmentTask,
    RepositoryTarget,
    RequirementSpec,
    TaskMetadata,
)


def _make_task() -> DevelopmentTask:
    return DevelopmentTask(
        metadata=TaskMetadata(
            task_id="t-1", title="Test Task", type="platform:change", source={},
        ),
        repository=RepositoryTarget(
            project_id="proj-1",
            work_branch="feat/t-1",
            base_branch="main",
        ),
        requirement=RequirementSpec(background="bg"),
        scope={"write_allowed": ["src/**"], "write_denied": []},
    )


def _make_task_with_required_output() -> DevelopmentTask:
    task = _make_task()
    task.implementation["required_outputs"] = ["src/main.py"]
    return task


def _make_runner(
    *,
    adapter_result: RunnerAdapterResult | None = None,
    changed_files: list[str] | None = None,
    validation_passed: bool = True,
    job_repo: AsyncMock | None = None,
) -> CodingAgentRunner:
    adapter = MagicMock()
    adapter.adapter_type = "mock"
    adapter.execute = AsyncMock(
        return_value=adapter_result or RunnerAdapterResult(exit_code=0)
    )

    workspace_mgr = MagicMock()
    workspace_mgr.create = AsyncMock(return_value=Path("/tmp/ws"))
    workspace_mgr.get_changed_files = AsyncMock(
        return_value=["src/main.py"] if changed_files is None else changed_files
    )
    workspace_mgr.run_validation = AsyncMock(
        return_value=ValidationResult(all_passed=validation_passed)
    )
    workspace_mgr.commit_and_push = AsyncMock(return_value="abc123")
    workspace_mgr.cleanup = AsyncMock()
    workspace_mgr.cleanup_on_failure = True
    workspace_mgr.cleanup_on_success = True

    gitlab = AsyncMock()
    gitlab.comment_merge_request = AsyncMock()

    return CodingAgentRunner(
        adapter=adapter,
        workspace_manager=workspace_mgr,
        gitlab=gitlab,
        gitlab_project_id="proj-gl",
        repo_url="https://gitlab.test/repo.git",
        job_repo=job_repo,
    )


class TestRunnerJobPersistence:
    @pytest.mark.asyncio
    async def test_persist_job_called_on_success(self):
        job_repo = AsyncMock()
        runner = _make_runner(job_repo=job_repo)
        job = await runner.run(_make_task())

        assert job.state == JobState.SUCCEEDED
        assert job_repo.save.await_count >= 3

    @pytest.mark.asyncio
    async def test_persist_job_called_on_failure(self):
        job_repo = AsyncMock()
        runner = _make_runner(
            adapter_result=RunnerAdapterResult(exit_code=1, error_message="boom"),
            job_repo=job_repo,
        )
        job = await runner.run(_make_task())

        assert job.state == JobState.FAILED
        assert job_repo.save.await_count >= 3

    @pytest.mark.asyncio
    async def test_persist_job_not_called_without_repo(self):
        runner = _make_runner(job_repo=None)
        job = await runner.run(_make_task())
        assert job.state == JobState.SUCCEEDED

    @pytest.mark.asyncio
    async def test_persist_job_failure_does_not_break_run(self):
        job_repo = AsyncMock()
        job_repo.save.side_effect = RuntimeError("DB down")
        runner = _make_runner(job_repo=job_repo)
        job = await runner.run(_make_task())
        assert job.state == JobState.SUCCEEDED


class TestRunnerLifecycle:
    @pytest.mark.asyncio
    async def test_successful_run(self):
        runner = _make_runner()
        job = await runner.run(_make_task())

        assert job.state == JobState.SUCCEEDED
        assert job.result is not None
        assert job.result.status == ResultStatus.SUCCESS
        assert job.result.commit_sha == "abc123"
        assert job.result.changed_files == ["src/main.py"]

    @pytest.mark.asyncio
    async def test_adapter_failure(self):
        runner = _make_runner(
            adapter_result=RunnerAdapterResult(exit_code=1, error_message="crash"),
        )
        job = await runner.run(_make_task())

        assert job.state == JobState.FAILED
        assert job.result.status == ResultStatus.RUNNER_ERROR
        assert "crash" in job.result.error_message

    @pytest.mark.asyncio
    async def test_validation_failure(self):
        runner = _make_runner(validation_passed=False)
        job = await runner.run(_make_task())

        assert job.state == JobState.FAILED
        assert job.result.status == ResultStatus.VALIDATION_FAILED
        assert job.result.commit_sha is None

    @pytest.mark.asyncio
    async def test_no_changes_with_required_outputs_fails(self):
        runner = _make_runner(changed_files=[])
        job = await runner.run(_make_task_with_required_output())

        assert job.state == JobState.FAILED
        assert job.result.status == ResultStatus.NO_CHANGES
        assert "without file changes" in job.result.error_message

    @pytest.mark.asyncio
    async def test_path_violation(self):
        runner = _make_runner(changed_files=[".env"])
        task = _make_task()
        task.scope["write_denied"] = [".env"]
        job = await runner.run(task)

        assert job.state == JobState.FAILED
        assert job.result.status == ResultStatus.PATH_VIOLATION

    @pytest.mark.asyncio
    async def test_timeout_handling(self):
        adapter = MagicMock()
        adapter.adapter_type = "mock"
        adapter.execute = AsyncMock(side_effect=TimeoutError("timed out"))

        workspace_mgr = MagicMock()
        workspace_mgr.create = AsyncMock(return_value=Path("/tmp/ws"))
        workspace_mgr.cleanup = AsyncMock()
        workspace_mgr.cleanup_on_failure = True
        workspace_mgr.cleanup_on_success = True

        runner = CodingAgentRunner(
            adapter=adapter,
            workspace_manager=workspace_mgr,
            gitlab=AsyncMock(),
            gitlab_project_id="proj-gl",
            repo_url="https://gitlab.test/repo.git",
        )
        job = await runner.run(_make_task())

        assert job.state == JobState.TIMED_OUT
        assert job.result.status == ResultStatus.TIMEOUT

    @pytest.mark.asyncio
    async def test_exception_handling(self):
        adapter = MagicMock()
        adapter.adapter_type = "mock"
        adapter.execute = AsyncMock(side_effect=ValueError("unexpected"))

        workspace_mgr = MagicMock()
        workspace_mgr.create = AsyncMock(return_value=Path("/tmp/ws"))
        workspace_mgr.cleanup = AsyncMock()
        workspace_mgr.cleanup_on_failure = True
        workspace_mgr.cleanup_on_success = True

        runner = CodingAgentRunner(
            adapter=adapter,
            workspace_manager=workspace_mgr,
            gitlab=AsyncMock(),
            gitlab_project_id="proj-gl",
            repo_url="https://gitlab.test/repo.git",
        )
        job = await runner.run(_make_task())

        assert job.state == JobState.FAILED
        assert job.result.status == ResultStatus.RUNNER_ERROR


class TestWorkspaceValidationCommandResolution:
    def test_resolves_pytest_to_current_interpreter(self):
        resolved = WorkspaceManager._resolve_validation_command("pytest tests/unit -q")

        assert resolved[1:3] == ["-m", "pytest"]
        assert resolved[3:] == ["tests/unit", "-q"]

    def test_resolves_python_to_current_interpreter(self):
        resolved = WorkspaceManager._resolve_validation_command("python scripts/check.py")

        assert resolved[1:] == ["scripts/check.py"]


class TestRepoUrl:
    def test_repo_url_override(self):
        runner = _make_runner()
        assert runner._repo_url() == "https://gitlab.test/repo.git"

    def test_repo_url_missing_raises(self):
        runner = CodingAgentRunner(
            adapter=MagicMock(),
            workspace_manager=MagicMock(),
            gitlab=MagicMock(),
            gitlab_project_id="proj",
        )
        with pytest.raises(RuntimeError, match="DEVFLOW_REPO_URL"):
            runner._repo_url()


class TestPlaneComment:
    def test_plane_comment_success(self):
        runner = CodingAgentRunner.__new__(CodingAgentRunner)
        from agent_platform.devflow.runner.models import RunnerResult
        job = CodingJob(
            job_id="j-1", task_id="t-1",
            result=RunnerResult(
                status=ResultStatus.SUCCESS,
                commit_sha="deadbeef12345",
                changed_files=["a.py", "b.py"],
            ),
        )
        comment = runner._build_plane_comment(job)
        assert "success" in comment
        assert "deadbeef" in comment
        assert "2" in comment

    def test_plane_comment_no_result(self):
        runner = CodingAgentRunner.__new__(CodingAgentRunner)
        job = CodingJob(job_id="j-2", task_id="t-2")
        comment = runner._build_plane_comment(job)
        assert "unknown" in comment
