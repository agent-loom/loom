from __future__ import annotations

import html
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_platform.devflow.runner.checkpoint import CheckpointManager
from agent_platform.devflow.runner.execution_log import (
    ExecutionLogEntry,
    ExecutionLogRepository,
    LogStream,
)
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
from agent_platform.integrations.errors import ScmError
from agent_platform.integrations.plane.adapter import PlaneAdapter
from agent_platform.integrations.scm.protocol import ScmAdapter
from agent_platform.persistence.repositories import CodingJobRepository

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
        gitlab: ScmAdapter,
        plane: PlaneAdapter | None = None,
        gitlab_project_id: str,
        repo_url: str | None = None,
        testing_state_id: str | None = None,
        ai_developing_state_id: str | None = None,
        gitlab_base_url: str | None = None,
        job_repo: CodingJobRepository | None = None,
        log_repo: ExecutionLogRepository | None = None,
    ):
        self.adapter = adapter
        self.workspace_manager = workspace_manager
        self.gitlab = gitlab
        self.plane = plane
        self.gitlab_project_id = gitlab_project_id
        self._repo_url_override = repo_url
        self._testing_state_id = testing_state_id
        self._ai_developing_state_id = ai_developing_state_id
        self._gitlab_base_url = gitlab_base_url
        self._job_repo = job_repo
        self._log_repo = log_repo
        self._checkpoint_mgr = CheckpointManager()

    async def run(
        self,
        task: DevelopmentTask,
        *,
        plane_project_id: str | None = None,
        plane_work_item_id: str | None = None,
    ) -> CodingJob:
        """
        执行编码任务的核心流程。
        """
        job = self._create_job(
            task,
            plane_project_id=plane_project_id,
            plane_work_item_id=plane_work_item_id,
        )
        await self._persist_job(job)

        try:
            job.state = JobState.WORKSPACE_CREATING
            await self._persist_job(job)
            # 创建本地代码工作区
            workspace_dir = await self.workspace_manager.create(
                branch=task.repository.work_branch,
                repo_url=self._repo_url(),
            )
            job.workspace_dir = str(workspace_dir)

            job.state = JobState.RUNNING
            await self._persist_job(job)
            path_guard = PathGuard.from_task(task, workspace_root=workspace_dir)
            await self._checkpoint(workspace_dir, job, "before_runner")
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
            required_outputs = task.implementation.get("required_outputs", [])
            if not changed_files and required_outputs:
                job.result = RunnerResult(
                    status=ResultStatus.NO_CHANGES,
                    changed_files=[],
                    error_message=(
                        "Runner completed without file changes, but task requires "
                        f"outputs: {required_outputs}"
                    ),
                )
                job.state = JobState.FAILED
                await self._report_failure(job)
                return job
            
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
            await self._checkpoint(workspace_dir, job, "before_validation")
            # 运行任务包要求的验证命令 (如 pytest 等)
            validation = await self.workspace_manager.run_validation(
                workspace_dir,
                task.validation.get("commands", []),
            )

            job.state = JobState.COMMITTING
            commit_sha = None
            if changed_files and validation.all_passed:
                await self._checkpoint(workspace_dir, job, "before_commit")
                # 只有验证完全通过才将变更提交并推送
                commit_sha = await self.workspace_manager.commit_and_push(
                    workspace_dir,
                    message=f"feat: {task.metadata.title}\n\nTask: {task.metadata.task_id}",
                    branch=task.repository.work_branch,
                    changed_files=changed_files,
                )

            # commit 成功后创建 MR（MR 天然包含 commit）
            if commit_sha:
                await self._checkpoint(workspace_dir, job, "after_commit")
                mr_result = await self._create_mr_safe(
                    task=task,
                    plane_project_id=plane_project_id,
                    plane_work_item_id=plane_work_item_id,
                )
                if mr_result:
                    job.mr_iid = mr_result.mr_id
                    job.mr_url = mr_result.url
                    await self._post_mr_creation_hooks(job)

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
            # Persist terminal state regardless of exit path
            await self._persist_job(job)
            # 根据配置清理工作区目录
            if job.workspace_dir:
                wm = self.workspace_manager
                keep = (
                    (job.state == JobState.FAILED and not wm.cleanup_on_failure)
                    or (job.state == JobState.TIMED_OUT and not wm.cleanup_on_failure)
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
        plane_project_id: str | None = None,
        plane_work_item_id: str | None = None,
    ) -> CodingJob:
        """构造一个新的编码作业记录。"""
        return CodingJob(
            job_id=f"job-{uuid.uuid4().hex[:12]}",
            task_id=task.metadata.task_id,
            branch=task.repository.work_branch,
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

            await self._record_adapter_output(job.job_id, invocation, adapter_result)

            if adapter_result.success:
                return adapter_result

            logger.warning(
                "Attempt %d/%d failed for job %s: %s",
                attempt, job.max_retries, job.job_id,
                adapter_result.error_message,
            )

        return last_result

    def _repo_url(self) -> str:
        """获取拉取代码库所用的 Git URL。"""
        if self._repo_url_override:
            return self._repo_url_override
        raise RuntimeError(
            "DEVFLOW_REPO_URL is not configured. "
            "Set the environment variable or pass repo_url to CodingAgentRunner."
        )

    async def _persist_job(self, job: CodingJob) -> None:
        """Best-effort persistence — failures are logged but never block the pipeline."""
        if self._job_repo is None:
            return
        try:
            await self._job_repo.save(job.model_dump(mode="json"))
        except Exception:
            logger.warning("Failed to persist job %s", job.job_id)

    async def _checkpoint(
        self, workspace_dir: Path, job: CodingJob, checkpoint_type: str,
    ) -> None:
        try:
            cp = await self._checkpoint_mgr.create(workspace_dir, checkpoint_type)
            job.checkpoints.append({
                "id": cp.checkpoint_id,
                "type": cp.checkpoint_type,
                "head_sha": cp.head_sha,
                "changed_files_count": len(cp.changed_files),
                "created_at": cp.created_at.isoformat(),
            })
        except Exception:
            logger.warning("Checkpoint [%s] 创建失败，不阻塞流程", checkpoint_type)

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
                        logger.info("Plane: work item %s → Testing", job.plane_work_item_id)
                    except Exception:
                        logger.warning(
                            "Failed to update Plane state to Testing for %s",
                            job.plane_work_item_id,
                        )
            else:
                # 失败时回退到 AI Developing
                if self._ai_developing_state_id:
                    try:
                        await self.plane.update_work_item_state(
                            job.plane_project_id,
                            job.plane_work_item_id,
                            self._ai_developing_state_id,
                        )
                        logger.info(
                            "Plane: work item %s → AI Developing (failure rollback)",
                            job.plane_work_item_id,
                        )
                    except Exception:
                        logger.warning(
                            "Failed to rollback Plane state for %s",
                            job.plane_work_item_id,
                        )

    async def _report_failure(self, job: CodingJob) -> None:
        """报告运行失败，复用 _report_result（内含失败回退状态逻辑）。"""
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

        if job.checkpoints:
            lines.extend(["", "### Checkpoints", ""])
            for cp in job.checkpoints:
                lines.append(
                    f"- **{cp['type']}** `{cp['id']}` "
                    f"head=`{cp['head_sha'][:8]}` "
                    f"files={cp['changed_files_count']}",
                )

        return "\n".join(lines)

    def _build_plane_comment(self, job: CodingJob) -> str:
        """构建用于项目管理平台（Plane）的汇总评论模板。"""
        result = job.result
        is_success = result and result.status == ResultStatus.SUCCESS
        icon = "✅" if is_success else "❌"
        status_label = result.status.value if result else "unknown"
        parts = [f"<p>{icon} <strong>DevFlow Runner</strong>: {status_label}</p>"]

        # MR 链接：优先用 job.mr_url（orchestrator 传入的完整 web_url）
        mr_url = job.mr_url
        if not mr_url and job.mr_iid and self._gitlab_base_url:
            mr_url = (
                f"{self._gitlab_base_url.rstrip('/')}/"
                f"{self.gitlab_project_id}/-/merge_requests/{job.mr_iid}"
            )
        if mr_url and job.mr_iid:
            parts.append(f"<p>MR: <a href='{mr_url}'>!{job.mr_iid}</a></p>")

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

    async def _record_adapter_output(
        self,
        job_id: str,
        invocation: RunnerInvocation,
        result: RunnerAdapterResult,
    ) -> None:
        """将 adapter 执行输出写入日志仓库。"""
        if self._log_repo is None:
            return
        try:
            if result.stdout:
                await self._log_repo.record(ExecutionLogEntry(
                    job_id=job_id,
                    stream=LogStream.STDOUT,
                    content=result.stdout,
                    adapter_name=invocation.adapter_type,
                ))
            if result.stderr:
                await self._log_repo.record(ExecutionLogEntry(
                    job_id=job_id,
                    stream=LogStream.STDERR,
                    content=result.stderr,
                    adapter_name=invocation.adapter_type,
                ))
        except Exception:
            logger.warning("日志记录失败: job=%s", job_id)

    async def _create_mr_safe(
        self,
        *,
        task: DevelopmentTask,
        plane_project_id: str | None = None,
        plane_work_item_id: str | None = None,
    ) -> Any:
        """commit+push 后创建 MR，失败不阻塞主流程。返回 MergeRequestResult 或 None。"""
        branch = task.repository.work_branch
        mr_spec = task.repository.merge_request
        description = mr_spec.description or ""
        if plane_project_id and plane_work_item_id:
            description += (
                f"\n\n<!-- devflow:plane_project_id={plane_project_id} "
                f"plane_work_item_id={plane_work_item_id} -->\n"
            )
        try:
            return await self.gitlab.create_merge_request(
                project_id=self.gitlab_project_id,
                source_branch=branch,
                target_branch=task.repository.default_branch,
                title=mr_spec.title,
                description=description,
                labels=mr_spec.labels,
            )
        except ScmError as exc:
            if exc.status_code != 409:
                logger.warning("MR 创建失败: %s", exc)
                return None
            finder = getattr(self.gitlab, "find_open_merge_request", None)
            if finder:
                try:
                    existing = await finder(self.gitlab_project_id, branch)
                    if existing:
                        logger.info("复用已有 MR for %s: !%s", branch, existing.mr_id)
                        return existing
                except Exception:
                    pass
            logger.warning("MR 409 但无法复用: %s", branch)
            return None
        except Exception:
            logger.warning("MR 创建异常: branch=%s", branch, exc_info=True)
            return None

    async def _post_mr_creation_hooks(self, job: CodingJob) -> None:
        """MR 创建成功后：通知 Plane 并更新 custom properties。"""
        if not job.mr_iid or not job.mr_url:
            return
        if self.plane and job.plane_project_id and job.plane_work_item_id:
            safe_mr_url = html.escape(job.mr_url)
            safe_branch = html.escape(job.branch)
            try:
                await self.plane.add_comment(
                    job.plane_project_id,
                    job.plane_work_item_id,
                    f"<p>DevFlow: MR created — <a href='{safe_mr_url}'>{safe_branch}</a></p>",
                )
            except Exception:
                logger.warning("Plane MR 评论失败: %s", job.plane_work_item_id)
            try:
                await self.plane.update_custom_properties(
                    job.plane_project_id,
                    job.plane_work_item_id,
                    {
                        "gitlab_mr_url": job.mr_url,
                        "gitlab_mr_iid": str(job.mr_iid),
                    },
                )
            except Exception:
                logger.warning("Plane MR 属性更新失败: %s", job.plane_work_item_id)
