"""Runner MR 创建时序测试。

验证 "Code First, MR Later" 的核心行为：
- commit+push 成功后才创建 MR
- commit 失败时不创建 MR
- MR 创建失败（非 409）不阻塞 job 成功
- 409 冲突时复用已有 MR
- MR 创建成功后触发 Plane 评论和 custom_properties 更新
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_platform.devflow.runner.execution_log import InMemoryExecutionLogRepository
from agent_platform.devflow.runner.models import CodingJob, JobState, ResultStatus, ValidationResult
from agent_platform.devflow.runner.runner import CodingAgentRunner
from agent_platform.devflow.runner.workspace import WorkspaceManager
from agent_platform.devflow.task_pack import (
    DevelopmentTask,
    MergeRequestSpec,
    RequirementSpec,
    RepositoryTarget,
    TaskMetadata,
)
from agent_platform.integrations.errors import ScmError
from agent_platform.integrations.scm.protocol import MergeRequestResult


# ---------------------------------------------------------------------------
# 公共测试用工具
# ---------------------------------------------------------------------------


def _make_task(branch: str = "feat/test-task") -> DevelopmentTask:
    return DevelopmentTask(
        metadata=TaskMetadata(
            title="测试任务",
            task_id="T-001",
            type="agent:change",
        ),
        repository=RepositoryTarget(
            project_id="proj-1",
            work_branch=branch,
            default_branch="master",
            merge_request=MergeRequestSpec(
                title="feat: 测试任务",
                description="自动生成的 MR",
                labels=[],
            ),
        ),
        agent={"agent_id": "echo"},
        requirement=RequirementSpec(background="测试需求背景"),
        scope={"write_allowed": ["**"]},
        implementation={},
        validation={"commands": []},
    )


def _make_runner(
    *,
    commit_sha: str | None = "abc123def456",
    adapter_success: bool = True,
    gitlab: object | None = None,
    plane: object | None = None,
) -> CodingAgentRunner:
    mock_adapter = MagicMock()
    mock_adapter.adapter_type = "mock"
    mock_adapter.execute = AsyncMock(return_value=MagicMock(
        success=adapter_success,
        exit_code=0 if adapter_success else 1,
        error_message=None if adapter_success else "adapter error",
    ))

    mock_workspace = MagicMock(spec=WorkspaceManager)
    mock_workspace.cleanup_on_success = True
    mock_workspace.cleanup_on_failure = False
    mock_workspace.create = AsyncMock(return_value=Path("/tmp/ws/test"))
    mock_workspace.get_changed_files = AsyncMock(return_value=["README.md"])
    mock_workspace.run_validation = AsyncMock(return_value=ValidationResult(
        all_passed=True, commands_executed=[],
    ))
    mock_workspace.commit_and_push = AsyncMock(return_value=commit_sha)
    mock_workspace.cleanup = AsyncMock()

    if gitlab is None:
        gitlab = _make_gitlab(mr_iid=10)

    return CodingAgentRunner(
        adapter=mock_adapter,
        workspace_manager=mock_workspace,
        gitlab=gitlab,
        plane=plane,
        gitlab_project_id="proj-1",
        repo_url="https://git.test/repo.git",
        log_repo=InMemoryExecutionLogRepository(),
    )


def _make_gitlab(
    *,
    mr_iid: int = 10,
    conflict: bool = False,
    error: bool = False,
) -> MagicMock:
    mock = MagicMock()

    if error:
        mock.create_merge_request = AsyncMock(side_effect=Exception("网络错误"))
    elif conflict:
        exc = ScmError("409 conflict")
        exc.status_code = 409
        mock.create_merge_request = AsyncMock(side_effect=exc)
    else:
        mock.create_merge_request = AsyncMock(return_value=MergeRequestResult(
            mr_id=mr_iid,
            url=f"https://gitlab.test/mr/{mr_iid}",
            source_branch="feat/test-task",
            target_branch="master",
            raw={},
        ))

    mock.find_open_merge_request = AsyncMock(return_value=MergeRequestResult(
        mr_id=mr_iid,
        url=f"https://gitlab.test/mr/{mr_iid}",
        source_branch="feat/test-task",
        target_branch="master",
        raw={},
    ))
    mock.comment_merge_request = AsyncMock()
    return mock


def _make_plane() -> MagicMock:
    mock = MagicMock()
    mock.add_comment = AsyncMock()
    mock.update_custom_properties = AsyncMock()
    mock.update_work_item_state = AsyncMock()
    return mock


# ---------------------------------------------------------------------------
# MR 在 commit 成功后才创建
# ---------------------------------------------------------------------------


class TestMRCreatedAfterCommit:
    @pytest.mark.asyncio
    async def test_mr_created_after_successful_commit(self):
        """commit+push 成功后调用 create_merge_request。"""
        gitlab = _make_gitlab(mr_iid=42)
        runner = _make_runner(commit_sha="sha111", gitlab=gitlab)

        job = await runner.run(_make_task(), plane_project_id="pp-1", plane_work_item_id="wi-1")

        assert job.state == JobState.SUCCEEDED
        assert job.mr_iid == 42
        assert "gitlab.test/mr/42" in job.mr_url
        gitlab.create_merge_request.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mr_not_created_when_no_commit(self):
        """没有 commit（无文件变更）时不创建 MR。"""
        gitlab = _make_gitlab(mr_iid=99)
        runner = _make_runner(commit_sha=None, gitlab=gitlab)

        job = await runner.run(_make_task())

        gitlab.create_merge_request.assert_not_awaited()
        assert job.mr_iid is None
        assert job.mr_url is None

    @pytest.mark.asyncio
    async def test_mr_not_created_when_adapter_fails(self):
        """adapter 执行失败时不创建 MR。"""
        gitlab = _make_gitlab(mr_iid=77)
        runner = _make_runner(adapter_success=False, gitlab=gitlab)

        job = await runner.run(_make_task())

        assert job.state == JobState.FAILED
        gitlab.create_merge_request.assert_not_awaited()
        assert job.mr_iid is None

    @pytest.mark.asyncio
    async def test_mr_description_embeds_plane_ids(self):
        """MR 描述中嵌入 Plane 元数据注释。"""
        captured: dict[str, str] = {}

        async def capture_create(**kwargs: object) -> MergeRequestResult:
            captured["description"] = kwargs.get("description", "")
            return MergeRequestResult(
                mr_id=5, url="https://gitlab.test/mr/5",
                source_branch="feat/test-task", target_branch="master", raw={},
            )

        gitlab = MagicMock()
        gitlab.create_merge_request = capture_create
        gitlab.comment_merge_request = AsyncMock()

        runner = _make_runner(commit_sha="sha999", gitlab=gitlab)

        await runner.run(
            _make_task(),
            plane_project_id="pp-embed",
            plane_work_item_id="wi-embed",
        )

        desc = captured.get("description", "")
        assert "plane_project_id=pp-embed" in desc
        assert "plane_work_item_id=wi-embed" in desc


# ---------------------------------------------------------------------------
# MR 创建失败不阻塞
# ---------------------------------------------------------------------------


class TestMRCreationFailureNonBlocking:
    @pytest.mark.asyncio
    async def test_mr_creation_error_does_not_fail_job(self):
        """MR 创建抛出通用异常时，job 仍然 SUCCEEDED。"""
        gitlab = _make_gitlab(error=True)
        runner = _make_runner(commit_sha="sha222", gitlab=gitlab)

        job = await runner.run(_make_task())

        assert job.state == JobState.SUCCEEDED
        assert job.mr_iid is None

    @pytest.mark.asyncio
    async def test_mr_creation_non_409_scm_error_returns_none(self):
        """非 409 的 ScmError 导致 MR 创建返回 None（不抛出）。"""
        exc = ScmError("500 server error")
        exc.status_code = 500

        gitlab = MagicMock()
        gitlab.create_merge_request = AsyncMock(side_effect=exc)
        gitlab.comment_merge_request = AsyncMock()

        runner = _make_runner(commit_sha="sha333", gitlab=gitlab)

        job = await runner.run(_make_task())

        assert job.state == JobState.SUCCEEDED
        assert job.mr_iid is None


# ---------------------------------------------------------------------------
# 409 冲突复用已有 MR
# ---------------------------------------------------------------------------


class TestMRConflictReuse:
    @pytest.mark.asyncio
    async def test_409_reuses_existing_mr(self):
        """409 冲突时调用 find_open_merge_request 复用已有 MR。"""
        exc = ScmError("409 conflict")
        exc.status_code = 409

        gitlab = MagicMock()
        gitlab.create_merge_request = AsyncMock(side_effect=exc)
        gitlab.find_open_merge_request = AsyncMock(return_value=MergeRequestResult(
            mr_id=55,
            url="https://gitlab.test/mr/55",
            source_branch="feat/test-task",
            target_branch="master",
            raw={},
        ))
        gitlab.comment_merge_request = AsyncMock()

        runner = _make_runner(commit_sha="sha555", gitlab=gitlab)

        job = await runner.run(_make_task())

        assert job.state == JobState.SUCCEEDED
        assert job.mr_iid == 55
        gitlab.find_open_merge_request.assert_awaited_once_with("proj-1", "feat/test-task")

    @pytest.mark.asyncio
    async def test_409_with_no_existing_mr_returns_none(self):
        """409 且 find_open_merge_request 也找不到时，返回 None 不阻塞。"""
        exc = ScmError("409 conflict")
        exc.status_code = 409

        gitlab = MagicMock()
        gitlab.create_merge_request = AsyncMock(side_effect=exc)
        gitlab.find_open_merge_request = AsyncMock(return_value=None)
        gitlab.comment_merge_request = AsyncMock()

        runner = _make_runner(commit_sha="sha666", gitlab=gitlab)

        job = await runner.run(_make_task())

        assert job.state == JobState.SUCCEEDED
        assert job.mr_iid is None


# ---------------------------------------------------------------------------
# MR 创建后触发 Plane 钩子
# ---------------------------------------------------------------------------


class TestPostMRCreationHooks:
    @pytest.mark.asyncio
    async def test_plane_comment_written_after_mr_creation(self):
        """MR 创建成功后向 Plane 写入评论。"""
        gitlab = _make_gitlab(mr_iid=20)
        plane = _make_plane()

        runner = _make_runner(commit_sha="sha020", gitlab=gitlab, plane=plane)

        job = await runner.run(
            _make_task(),
            plane_project_id="pp-hook",
            plane_work_item_id="wi-hook",
        )

        assert job.mr_iid == 20
        plane.add_comment.assert_awaited()
        comment_args = plane.add_comment.call_args
        body = comment_args.args[2] if len(comment_args.args) >= 3 else comment_args.kwargs.get("body", "")
        assert "gitlab.test/mr/20" in body

    @pytest.mark.asyncio
    async def test_plane_custom_properties_updated_after_mr_creation(self):
        """MR 创建成功后更新 Plane custom_properties（mr_url 和 mr_iid）。"""
        gitlab = _make_gitlab(mr_iid=30)
        plane = _make_plane()

        runner = _make_runner(commit_sha="sha030", gitlab=gitlab, plane=plane)

        job = await runner.run(
            _make_task(),
            plane_project_id="pp-prop",
            plane_work_item_id="wi-prop",
        )

        assert job.mr_iid == 30
        plane.update_custom_properties.assert_awaited()
        props_args = plane.update_custom_properties.call_args
        props = props_args.args[2] if len(props_args.args) >= 3 else props_args.kwargs.get("properties", {})
        assert "gitlab_mr_url" in props
        assert "gitlab_mr_iid" in props
        assert props["gitlab_mr_iid"] == "30"

    @pytest.mark.asyncio
    async def test_plane_hooks_skipped_when_no_plane(self):
        """没有配置 Plane 时，MR 创建成功但不调用任何 Plane API。"""
        gitlab = _make_gitlab(mr_iid=40)
        runner = _make_runner(commit_sha="sha040", gitlab=gitlab, plane=None)

        job = await runner.run(
            _make_task(),
            plane_project_id="pp-x",
            plane_work_item_id="wi-x",
        )

        assert job.mr_iid == 40
        # plane 为 None，不应有任何调用（只要不抛出即可）

    @pytest.mark.asyncio
    async def test_plane_hook_failure_does_not_break_job(self):
        """Plane 评论失败不影响 job 最终状态。"""
        gitlab = _make_gitlab(mr_iid=50)
        plane = _make_plane()
        plane.add_comment = AsyncMock(side_effect=RuntimeError("Plane 挂了"))

        runner = _make_runner(commit_sha="sha050", gitlab=gitlab, plane=plane)

        job = await runner.run(
            _make_task(),
            plane_project_id="pp-fail",
            plane_work_item_id="wi-fail",
        )

        assert job.state == JobState.SUCCEEDED
        assert job.mr_iid == 50
