"""编码任务运行器的数据模型定义。"""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(UTC)


class JobState(enum.StrEnum):
    """编码任务的生命周期状态。"""
    PENDING = "pending"
    WORKSPACE_CREATING = "workspace_creating"
    RUNNING = "running"
    VALIDATING = "validating"
    COMMITTING = "committing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class ResultStatus(enum.StrEnum):
    """运行结果的状态枚举。"""
    SUCCESS = "success"
    VALIDATION_FAILED = "validation_failed"
    RUNNER_ERROR = "runner_error"
    PATH_VIOLATION = "path_violation"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class CommandResult(BaseModel):
    """单条验证命令的执行结果。"""
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0


class ValidationResult(BaseModel):
    """验证阶段的汇总结果。"""
    commands_executed: list[CommandResult] = Field(default_factory=list)
    all_passed: bool = False
    report_paths: list[str] = Field(default_factory=list)


class RunnerInvocation(BaseModel):
    """Runner 的单次调用记录。"""
    invocation_id: str
    attempt: int = 1
    adapter_type: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None


class RunnerResult(BaseModel):
    """Runner 的最终执行结果。"""
    status: ResultStatus
    changed_files: list[str] = Field(default_factory=list)
    validation: ValidationResult = Field(default_factory=ValidationResult)
    commit_sha: str | None = None
    error_message: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)


class CodingJob(BaseModel):
    """编码任务的完整状态，包含调用记录和结果。"""
    job_id: str
    task_id: str
    state: JobState = JobState.PENDING
    workspace_dir: str | None = None
    branch: str = ""
    invocations: list[RunnerInvocation] = Field(default_factory=list)
    result: RunnerResult | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    mr_iid: int | None = None
    mr_url: str | None = None
    plane_project_id: str | None = None
    plane_work_item_id: str | None = None
    retry_count: int = 0
    max_retries: int = 1
    timeout_seconds: int = 600
