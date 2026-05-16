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
    """
    AI 编码代理运行器。
    用于管理整个代码生成的生命周期：包括工作区分配、代理调用、验证检查以及变更提交和结果上报。
    """

    def __init__(
        self,
        *,
        adapter: RunnerAdapter,
        workspace_manager: WorkspaceManager,
        gitlab: GitLabAdapter,
        plane: PlaneAdapter | None = None,
        gitlab_project_id: str,
        repo_url: str | None = None,
        testing_state_id: str | None = None,
    ):
        """
        初始化。

        :param adapter: 具体的 AI 编码适配器（如 Codex, Claude）。
        :param workspace_manager: 工作区管理工具，负责环境的克隆与清理。
        :param gitlab: GitLab API 适配器。
        :param plane: 关联的项目管理平台适配器。
        :param gitlab_project_id: 关联的代码项目 ID。
        :param repo_url: 指定拉取代码的 git URL。
        :param testing_state_id: 代码成功提交后在项目平台上所需要流转到的测试状态。
        """
        self.adapter = adapter
        self.workspace_manager = workspace_manager
        self.gitlab = gitlab
        self.plane = plane
        self.gitlab_project_id = gitlab_project_id
        self._repo_url_override = repo_url
        self._testing_state_id = testing_state_id

    async def run(
        self,
        task: DevelopmentTask,
        *,
        mr_iid: int | None = None,
        plane_project_id: str | None = None,
        plane_work_item_id: str | None = None,
    ) -> CodingJob:
        """
        执行编码任务的核心流程。
        """
        job = self._create_job(
            task, mr_iid=mr_iid,
            plane_project_id=plane_project_id,
            plane_work_item_id=plane_work_item_id,
        )

        try:
            job.state = JobState.WORKSPACE_CREATING
            # 创建本地代码工作区
            workspace_dir = await self.workspace_manager.create(
                branch=task.repository.work_branch,
                repo_url=self._repo_url(),
            )
            job.workspace_dir = str(workspace_dir)

            job.state = JobState.RUNNING
            path_guard = PathGuard.from_task(task)
            # 执行 AI 代理编写代码，包含重试机制
            adapter_result = await self._execute_with_retry(job, task)
            
            if not adapter_result or not adapter_result.success:
                job.state = JobState.FAILED
                err = adapter_result.error_message if adapter_result else "Agent failed"
                job.result = RunnerResult(
                    status=ResultStatus.RUNNER_ERROR,
                    error_message=err,
                )
                await self._report_failure(job)
                return job

            # 获取所有变更的文件
            changed_files = await self.workspace_manager.get_changed_files(workspace_dir)
            
            # 使用 PathGuard 检查代理是否修改了越界或被保护的文件
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
            # 运行任务包要求的验证命令 (如 pytest 等)
            validation = await self.workspace_manager.run_validation(
                workspace_dir,
                task.validation.get("commands", []),
            )

            job.state = JobState.COMMITTING
            commit_sha = None
            if changed_files and validation.all_passed:
                # 只有验证完全通过才将变更提交并推送
                commit_sha = await self.workspace_manager.commit_and_push(
                    workspace_dir,
                    message=f"feat: {task.metadata.title}\n\nTask: {task.metadata.task_id}",
                    branch=task.repository.work_branch,
                    changed_files=changed_files,
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
            # 根据配置清理工作区目录
            if job.workspace_dir:
                wm = self.workspace_manager
                keep = (
                    (job.state == JobState.FAILED and not wm.cleanup_on_failure)
                    or (job.state == JobState.SUCCEEDED and not wm.cleanup_on_success)
                )
                await wm.cleanup(
                    Path(job.workspace_dir),
                    keep_on_failure=keep,
                )

        return job

    def _create_job(
        self,
        task: DevelopmentTask,
        *,
        mr_iid: int | None = None,
        plane_project_id: str | None = None,
        plane_work_item_id: str | None = None,
    ) -> CodingJob:
        """
        构造一个新的编码作业记录。
        """
        return CodingJob(
            job_id=f"job-{uuid.uuid4().hex[:12]}",
            task_id=task.metadata.task_id,
            branch=task.repository.work_branch,
            mr_iid=mr_iid,
            plane_project_id=plane_project_id,
            plane_work_item_id=plane_work_item_id,
        )

    async def _execute_with_retry(
        self, job: CodingJob, task: DevelopmentTask,
    ) -> RunnerAdapterResult:
        """
        在出现失败时，依据最大重试次数配置重新调度 Adapter 执行。
        """
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
        """
        获取拉取代码库所用的 Git URL。
        """
        if self._repo_url_override:
            return self._repo_url_override
        return f"https://gitlab.example.com/{self.gitlab_project_id}.git"

    async def _report_result(self, job: CodingJob) -> None:
        """
        将执行结果通过评论等形式回传至 GitLab 和项目管理平台。
        若配置了测试状态，还将触发状态扭转。
        """
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

            if job.result and job.result.status == ResultStatus.SUCCESS:
                if self._testing_state_id:
                    try:
                        await self.plane.update_work_item_state(
                            job.plane_project_id,
                            job.plane_work_item_id,
                            self._testing_state_id,
                        )
                    except Exception:
                        logger.warning(
                            "Failed to update Plane state for %s",
                            job.plane_work_item_id,
                        )

    async def _report_failure(self, job: CodingJob) -> None:
        """
        报告运行失败。目前复用了 _report_result。
        """
        await self._report_result(job)

    def _build_mr_comment(self, job: CodingJob) -> str:
        """
        构建用于 GitLab 合并请求的报告评论模板。
        """
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
        """
        构建用于项目管理平台（Plane）的汇总评论模板。
        """
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
