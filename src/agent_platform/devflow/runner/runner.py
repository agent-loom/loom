from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from agent_platform.devflow.runner.models import (
    CodingJob,
    JobState,
    ResultStatus,
    RunnerInvocation,
    RunnerResult,
)
from agent_platform.devflow.runner.path_guard import PathGuard
from agent_platform.devflow.runner.protocol import RunnerAdapter, RunnerAdapterResult
from agent_platform.devflow.runner.workspace import WorkspaceManager
from agent_platform.devflow.task_pack import DevelopmentTask
from agent_platform.integrations.gitlab.adapter import GitLabAdapter
from agent_platform.integrations.plane.adapter import PlaneAdapter

logger = logging.getLogger(__name__)


class CodingAgentRunner:

    def __init__(
        self,
        *,
        adapter: RunnerAdapter,
        workspace_manager: WorkspaceManager,
        gitlab: GitLabAdapter,
        plane: PlaneAdapter | None = None,
        gitlab_project_id: str,
    ):
        self.adapter = adapter
        self.workspace_manager = workspace_manager
        self.gitlab = gitlab
        self.plane = plane
        self.gitlab_project_id = gitlab_project_id

    async def run(self, task: DevelopmentTask, *, mr_iid: int | None = None) -> CodingJob:
        job = self._create_job(task, mr_iid=mr_iid)

        try:
            job.state = JobState.WORKSPACE_CREATING
            workspace_dir = await self.workspace_manager.create(
                branch=task.repository.work_branch,
                repo_url=self._repo_url(),
            )
            job.workspace_dir = str(workspace_dir)

            job.state = JobState.RUNNING
            path_guard = PathGuard.from_task(task)
            await self._execute_with_retry(job, task)

            changed_files = self.workspace_manager.get_changed_files(workspace_dir)
            violations = path_guard.check(changed_files)
            if violations:
                job.result = RunnerResult(
                    status=ResultStatus.PATH_VIOLATION,
                    changed_files=changed_files,
                    error_message=f"Path guard violation: {violations}",
                )
                job.state = JobState.FAILED
                await self._report_failure(job)
                return job

            job.state = JobState.VALIDATING
            validation = await self.workspace_manager.run_validation(
                workspace_dir,
                task.validation.get("commands", []),
            )

            job.state = JobState.COMMITTING
            commit_sha = None
            if changed_files and validation.all_passed:
                commit_sha = await self.workspace_manager.commit_and_push(
                    workspace_dir,
                    message=f"feat: {task.metadata.title}\n\nTask: {task.metadata.task_id}",
                    branch=task.repository.work_branch,
                )

            status = (
                ResultStatus.SUCCESS if validation.all_passed
                else ResultStatus.VALIDATION_FAILED
            )
            job.result = RunnerResult(
                status=status,
                changed_files=changed_files,
                validation=validation,
                commit_sha=commit_sha,
            )
            job.state = JobState.SUCCEEDED if validation.all_passed else JobState.FAILED

            await self._report_result(job)

        except TimeoutError:
            job.state = JobState.TIMED_OUT
            job.result = RunnerResult(status=ResultStatus.TIMEOUT, error_message="Job timed out")
            await self._report_failure(job)

        except Exception as exc:
            job.state = JobState.FAILED
            job.result = RunnerResult(status=ResultStatus.RUNNER_ERROR, error_message=str(exc))
            await self._report_failure(job)
            logger.exception("CodingAgentRunner failed for job %s", job.job_id)

        finally:
            job.updated_at = datetime.now(UTC)
            if job.workspace_dir:
                await self.workspace_manager.cleanup(
                    Path(job.workspace_dir),
                    keep_on_failure=(job.state == JobState.FAILED),
                )

        return job

    def _create_job(self, task: DevelopmentTask, *, mr_iid: int | None = None) -> CodingJob:
        return CodingJob(
            job_id=f"job-{uuid.uuid4().hex[:12]}",
            task_id=task.metadata.task_id,
            branch=task.repository.work_branch,
            mr_iid=mr_iid,
        )

    async def _execute_with_retry(
        self, job: CodingJob, task: DevelopmentTask,
    ) -> RunnerAdapterResult:
        last_result: RunnerAdapterResult | None = None
        for attempt in range(1, job.max_retries + 1):
            invocation = RunnerInvocation(
                invocation_id=str(uuid.uuid4()),
                attempt=attempt,
                adapter_type=self.adapter.adapter_type,
                started_at=datetime.now(UTC),
            )

            adapter_result = await self.adapter.execute(
                workspace_dir=job.workspace_dir,
                task=task,
                timeout_seconds=job.timeout_seconds,
            )

            invocation.finished_at = datetime.now(UTC)
            invocation.exit_code = adapter_result.exit_code
            job.invocations.append(invocation)
            last_result = adapter_result

            if adapter_result.success:
                return adapter_result

            logger.warning(
                "Attempt %d/%d failed for job %s: %s",
                attempt, job.max_retries, job.job_id,
                adapter_result.error_message,
            )

        return last_result

    def _repo_url(self) -> str:
        return f"https://gitlab.example.com/{self.gitlab_project_id}.git"

    async def _report_result(self, job: CodingJob) -> None:
        if job.mr_iid:
            comment = self._build_mr_comment(job)
            try:
                await self.gitlab.comment_merge_request(
                    self.gitlab_project_id, job.mr_iid, comment,
                )
            except Exception:
                logger.warning("Failed to comment on MR %s", job.mr_iid)

        if self.plane and job.plane_project_id and job.plane_work_item_id:
            summary = self._build_plane_comment(job)
            try:
                await self.plane.add_comment(
                    job.plane_project_id, job.plane_work_item_id, summary,
                )
            except Exception:
                logger.warning("Failed to comment on Plane work item %s", job.plane_work_item_id)

    async def _report_failure(self, job: CodingJob) -> None:
        await self._report_result(job)

    def _build_mr_comment(self, job: CodingJob) -> str:
        result = job.result
        if result is None:
            return "## DevFlow Runner 执行报告\n\n**状态**: 未完成"

        lines = [
            "## DevFlow Runner 执行报告",
            "",
            f"**状态**: {result.status.value}",
            f"**Job ID**: {job.job_id}",
        ]

        if job.invocations:
            last = job.invocations[-1]
            lines.append(f"**Adapter**: {last.adapter_type}")
            lines.append(f"**尝试次数**: {last.attempt}")

        if result.commit_sha:
            lines.append(f"**Commit**: {result.commit_sha[:8]}")

        if result.error_message:
            lines.extend(["", f"**错误**: {result.error_message}"])

        if result.changed_files:
            lines.extend(["", "### 变更文件", ""])
            for f in result.changed_files:
                lines.append(f"- {f}")

        if result.validation.commands_executed:
            lines.extend(["", "### 验证结果", ""])
            lines.append("| 命令 | 状态 | 耗时 |")
            lines.append("| --- | --- | --- |")
            for cmd in result.validation.commands_executed:
                status = "PASS" if cmd.exit_code == 0 else "FAIL"
                lines.append(f"| `{cmd.command}` | {status} | {cmd.duration_ms}ms |")

        return "\n".join(lines)

    def _build_plane_comment(self, job: CodingJob) -> str:
        result = job.result
        status = result.status.value if result else "unknown"
        parts = [f"<p><strong>DevFlow Runner</strong>: {status}</p>"]

        if result and result.commit_sha:
            parts.append(f"<p>Commit: <code>{result.commit_sha[:8]}</code></p>")

        if result and result.changed_files:
            parts.append(f"<p>变更文件: {len(result.changed_files)} 个</p>")

        if result and result.error_message:
            parts.append(f"<p>错误: {result.error_message[:200]}</p>")

        if result and result.validation.commands_executed:
            passed = sum(1 for c in result.validation.commands_executed if c.exit_code == 0)
            total = len(result.validation.commands_executed)
            parts.append(f"<p>验证: {passed}/{total} 通过</p>")

        return "\n".join(parts)
