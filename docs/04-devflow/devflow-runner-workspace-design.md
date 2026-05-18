# DevFlow Runner / Workspace 设计

> Status: Implemented (真实 Codex Runner E2E 已跑通)
> Stage: S4
> Owner: platform
> Last verified against code: 2026-05-18

本文档定义 CodingAgentRunner、Workspace、PathGuard 以及 Job 生命周期的完整设计。目标是让 DevFlow 能够从 task pack 出发，自动调用 Codex / Claude Code / OpenHands 完成编码，并将结果提交到 GitLab MR、回写 Plane。

本文档的读者是 AI coding agent。所有接口定义可直接用于实现。

## 1. 术语和上下文

### 1.1 当前 DevFlow 已有能力

```
DevFlowOrchestrator  -->  Plane webhook 触发
                     -->  TaskPackGenerator 生成 DevelopmentTask
                     -->  GitLabAdapter 创建 branch + MR
                     -->  PlaneAdapter 回写 comment / 状态 / custom properties
                     -->  CodingAgentRunner 调用 mock / codex / claude_code
                     -->  WorkspaceManager clone / validate / commit / push
                     -->  GitLab MR comment + Plane comment 回写
```

### 1.2 当前仍缺失的部分

当前代码已经跑通真实 Codex Runner 的 "代码修改 -> 测试 -> 提交 -> 回写" 闭环，但仍有以下生产化缺口：

1. Claude Code 真实 E2E 尚未完成稳定验证。
2. Runner job 日志仍主要依赖进程输出和内存 execution log，缺少可长期查询的 stdout/stderr 文件或 DB 存储。
3. workspace 隔离仍是本地目录级隔离，未引入容器、网络策略、资源限制或 secret 隔离。
4. Plane/GitLab 强状态机、DLQ、失败重试和 reconciliation 仍以设计为主，未完全生产化。

### 1.3 整体流程

```
Plane webhook
  |
  v
DevFlowOrchestrator
  |-- TaskPackGenerator.from_requirement() --> DevelopmentTask
  |-- GitLabAdapter.create_branch() + create_merge_request()
  |-- PlaneAdapter.update_work_item_state("AI Developing")
  |
  v
CodingAgentRunner  <-- 本文档核心
  |-- Workspace.create()
  |-- PathGuard.enforce()
  |-- RunnerAdapter.execute() (Claude Code / Codex / OpenHands)
  |-- Workspace.validate()
  |-- Workspace.commit_and_push()
  |-- GitLabAdapter.comment_merge_request()
  |-- PlaneAdapter.add_comment() + update_work_item_state()
  |
  v
GitLab CI pipeline (已有，不在本文档范围)
```

## 2. 核心对象

### 2.1 对象关系

```
DevelopmentTask (from task_pack.py, 已存在)
       |
       v
CodingJob
  |-- job_id: str
  |-- task: DevelopmentTask
  |-- workspace: Workspace
  |-- state: JobState
  |-- invocations: list[RunnerInvocation]
  |-- result: RunnerResult | None
  |-- created_at / updated_at
       |
       v
Workspace
  |-- workspace_id: str
  |-- base_dir: Path
  |-- repo_url: str
  |-- branch: str
  |-- path_guard: PathGuard
       |
       v
PathGuard
  |-- write_allowed: list[str]   (glob patterns from task pack)
  |-- write_denied: list[str]    (glob patterns from task pack)
       |
RunnerInvocation
  |-- invocation_id: str
  |-- attempt: int
  |-- adapter_type: str
  |-- started_at / finished_at
  |-- exit_code: int | None
  |-- stdout_path: Path | None
  |-- stderr_path: Path | None
       |
RunnerResult
  |-- status: ResultStatus
  |-- changed_files: list[str]
  |-- validation: ValidationResult
  |-- commit_sha: str | None
  |-- error_message: str | None
  |-- artifacts: dict[str, Path]
       |
ValidationResult
  |-- commands_executed: list[CommandResult]
  |-- all_passed: bool
  |-- report_paths: list[Path]
```

### 2.2 Python 模型定义

```python
# src/agent_platform/devflow/runner/models.py

from __future__ import annotations

import enum
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class JobState(str, enum.Enum):
    PENDING = "pending"
    WORKSPACE_CREATING = "workspace_creating"
    RUNNING = "running"
    VALIDATING = "validating"
    COMMITTING = "committing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class ResultStatus(str, enum.Enum):
    SUCCESS = "success"
    VALIDATION_FAILED = "validation_failed"
    RUNNER_ERROR = "runner_error"
    PATH_VIOLATION = "path_violation"
    NO_CHANGES = "no_changes"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class CommandResult(BaseModel):
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0


class ValidationResult(BaseModel):
    commands_executed: list[CommandResult] = Field(default_factory=list)
    all_passed: bool = False
    report_paths: list[str] = Field(default_factory=list)


class RunnerInvocation(BaseModel):
    invocation_id: str
    attempt: int = 1
    adapter_type: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None


class RunnerResult(BaseModel):
    status: ResultStatus
    changed_files: list[str] = Field(default_factory=list)
    validation: ValidationResult = Field(default_factory=ValidationResult)
    commit_sha: str | None = None
    error_message: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)


class CodingJob(BaseModel):
    job_id: str
    task_id: str
    state: JobState = JobState.PENDING
    workspace_dir: str | None = None
    branch: str = ""
    invocations: list[RunnerInvocation] = Field(default_factory=list)
    result: RunnerResult | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    mr_iid: int | None = None
    mr_url: str | None = None
    plane_project_id: str | None = None
    plane_work_item_id: str | None = None
    retry_count: int = 0
    max_retries: int = 1
    timeout_seconds: int = 600
```

## 3. CodingAgentRunner Protocol

```python
# src/agent_platform/devflow/runner/protocol.py

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_platform.devflow.runner.models import (
    CodingJob,
    RunnerResult,
)
from agent_platform.devflow.task_pack import DevelopmentTask


@runtime_checkable
class RunnerAdapter(Protocol):
    """统一的 coding agent 适配器接口。

    每个适配器（Claude Code、Codex、OpenHands）必须实现此接口。
    适配器只负责"在给定 workspace 目录中执行编码任务"，
    不负责 workspace 创建、git 操作、结果回写。
    """

    @property
    def adapter_type(self) -> str:
        """返回适配器类型标识，如 'claude_code', 'codex', 'openhands'。"""
        ...

    async def execute(
        self,
        *,
        workspace_dir: str,
        task: DevelopmentTask,
        timeout_seconds: int = 600,
    ) -> RunnerAdapterResult:
        """在 workspace_dir 中执行编码任务。

        适配器必须：
        1. 将 DevelopmentTask 转换为对应工具的输入格式。
        2. 启动工具进程或调用 API。
        3. 等待完成或超时。
        4. 返回 RunnerAdapterResult。

        适配器不得：
        1. 执行 git commit / push。
        2. 修改 workspace_dir 之外的文件。
        3. 访问网络（除调用 coding agent 自身 API 外）。
        """
        ...

    async def cancel(self) -> None:
        """取消正在执行的任务。"""
        ...

    async def health_check(self) -> bool:
        """检查适配器是否可用。"""
        ...


class RunnerAdapterResult:
    """适配器执行结果。与 RunnerResult 不同，
    RunnerAdapterResult 只包含适配器层面的信息，
    不包含 validation、commit 等后续步骤的结果。"""

    def __init__(
        self,
        *,
        exit_code: int,
        changed_files: list[str] | None = None,
        stdout: str = "",
        stderr: str = "",
        error_message: str | None = None,
    ):
        self.exit_code = exit_code
        self.changed_files = changed_files or []
        self.stdout = stdout
        self.stderr = stderr
        self.error_message = error_message

    @property
    def success(self) -> bool:
        return self.exit_code == 0
```

### 3.1 CodingAgentRunner 主控类

```python
# src/agent_platform/devflow/runner/runner.py

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from pathlib import Path

from agent_platform.devflow.runner.models import (
    CodingJob,
    JobState,
    ResultStatus,
    RunnerInvocation,
    RunnerResult,
    ValidationResult,
)
from agent_platform.devflow.runner.path_guard import PathGuard
from agent_platform.devflow.runner.protocol import RunnerAdapter
from agent_platform.devflow.runner.workspace import WorkspaceManager
from agent_platform.devflow.task_pack import DevelopmentTask
from agent_platform.integrations.gitlab.adapter import GitLabAdapter
from agent_platform.integrations.plane.adapter import PlaneAdapter

logger = logging.getLogger(__name__)


class CodingAgentRunner:
    """DevFlow 的核心执行器。

    职责：
    1. 从 DevelopmentTask 创建 CodingJob。
    2. 创建隔离 workspace。
    3. 调用 RunnerAdapter 执行编码。
    4. 通过 PathGuard 验证文件变更合规。
    5. 执行 validation commands。
    6. 提交 commit 并 push。
    7. 回写 GitLab MR comment 和 Plane 状态。
    8. 清理 workspace。
    """

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
        """完整执行一次 coding job。"""
        job = self._create_job(task, mr_iid=mr_iid)

        try:
            # 1. 创建 workspace
            job.state = JobState.WORKSPACE_CREATING
            workspace_dir = await self.workspace_manager.create(
                branch=task.repository.work_branch,
                repo_url=self._repo_url(),
            )
            job.workspace_dir = str(workspace_dir)

            # 2. 执行 coding agent
            job.state = JobState.RUNNING
            path_guard = PathGuard.from_task(task)
            adapter_result = await self._execute_with_retry(job, task)

            # 3. PathGuard 检查
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

            # 4. 验证
            job.state = JobState.VALIDATING
            validation = await self.workspace_manager.run_validation(
                workspace_dir,
                task.validation.get("commands", []),
            )

            # 5. 提交
            job.state = JobState.COMMITTING
            commit_sha = None
            if changed_files and validation.all_passed:
                commit_sha = await self.workspace_manager.commit_and_push(
                    workspace_dir,
                    message=f"feat: {task.metadata.title}\n\nTask: {task.metadata.task_id}",
                    branch=task.repository.work_branch,
                )

            # 6. 组装结果
            status = ResultStatus.SUCCESS if validation.all_passed else ResultStatus.VALIDATION_FAILED
            job.result = RunnerResult(
                status=status,
                changed_files=changed_files,
                validation=validation,
                commit_sha=commit_sha,
            )
            job.state = JobState.SUCCEEDED if validation.all_passed else JobState.FAILED

            # 7. 回写
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
            job.updated_at = datetime.utcnow()
            if job.workspace_dir:
                await self.workspace_manager.cleanup(
                    Path(job.workspace_dir),
                    keep_on_failure=(job.state == JobState.FAILED),
                )

        return job
```

## 4. Runner Adapter 架构

### 4.1 统一输入输出

所有适配器接收相同的输入（workspace_dir + DevelopmentTask），返回相同的 `RunnerAdapterResult`。不同 coding agent 工具的差异只存在于适配器内部。

```
                ┌─────────────────┐
                │  RunnerAdapter  │  (Protocol)
                │   .execute()    │
                └────────┬────────┘
           ┌─────────────┼──────────────┐
           v             v              v
   ClaudeCodeAdapter  CodexAdapter  OpenHandsAdapter
```

### 4.2 Claude Code Adapter

```python
# src/agent_platform/devflow/runner/adapters/claude_code.py

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from agent_platform.devflow.runner.protocol import RunnerAdapter, RunnerAdapterResult
from agent_platform.devflow.task_pack import DevelopmentTask

logger = logging.getLogger(__name__)


class ClaudeCodeAdapter:
    """通过 Claude Code CLI 执行编码任务。

    调用方式：claude --print --output-format json --permission-mode bypassPermissions --no-session-persistence --max-turns N
    工作目录设为 workspace_dir。
    """

    def __init__(
        self,
        *,
        cli_path: str = "claude",
        max_turns: int = 30,
        model: str | None = None,
    ):
        self.cli_path = cli_path
        self.max_turns = max_turns
        self.model = model
        self._process: asyncio.subprocess.Process | None = None

    @property
    def adapter_type(self) -> str:
        return "claude_code"

    async def execute(
        self,
        *,
        workspace_dir: str,
        task: DevelopmentTask,
        timeout_seconds: int = 600,
    ) -> RunnerAdapterResult:
        prompt = self._build_prompt(task)

        cmd = [
            self.cli_path,
            "--print",
            "--output-format", "json",
            "--permission-mode", "bypassPermissions",
            "--no-session-persistence",
            "--max-turns", str(self.max_turns),
        ]
        if self.model:
            cmd.extend(["--model", self.model])

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace_dir,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                self._process.communicate(input=prompt.encode()),
                timeout=timeout_seconds,
            )
            exit_code = self._process.returncode or 0
            stdout = stdout_bytes.decode(errors="replace")
            stderr = stderr_bytes.decode(errors="replace")

            return RunnerAdapterResult(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                error_message=stderr.strip()[-1000:] if exit_code != 0 else None,
            )

        except asyncio.TimeoutError:
            await self.cancel()
            return RunnerAdapterResult(
                exit_code=-1,
                error_message=f"Claude Code timed out after {timeout_seconds}s",
            )

    async def cancel(self) -> None:
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=10)
            except asyncio.TimeoutError:
                self._process.kill()

    async def health_check(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.cli_path, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.wait(), timeout=10)
            return proc.returncode == 0
        except Exception:
            return False

    def _build_prompt(self, task: DevelopmentTask) -> str:
        """将 DevelopmentTask 转换为 Claude Code 的自然语言 prompt。"""
        scope_allowed = task.scope.get("write_allowed", [])
        scope_denied = task.scope.get("write_denied", [])
        constraints = task.implementation.get("constraints", [])
        required_outputs = task.implementation.get("required_outputs", [])
        validation_commands = task.validation.get("commands", [])

        lines = [
            f"# 任务: {task.metadata.title}",
            f"Task ID: {task.metadata.task_id}",
            "",
            "## 需求背景",
            task.requirement.background,
            "",
        ]

        if task.requirement.user_scenarios:
            lines.append("## 用户场景")
            for s in task.requirement.user_scenarios:
                lines.append(f"- {s}")
            lines.append("")

        if task.requirement.acceptance:
            lines.append("## 验收标准")
            for a in task.requirement.acceptance:
                lines.append(f"- {a}")
            lines.append("")

        lines.append("## 允许修改的路径")
        for p in scope_allowed:
            lines.append(f"- {p}")
        lines.append("")

        lines.append("## 禁止修改的路径")
        for p in scope_denied:
            lines.append(f"- {p}")
        lines.append("")

        if constraints:
            lines.append("## 约束")
            for c in constraints:
                lines.append(f"- {c}")
            lines.append("")

        if required_outputs:
            lines.append("## 必须产出")
            for o in required_outputs:
                lines.append(f"- {o}")
            lines.append("")

        if validation_commands:
            lines.append("## 完成后请执行以下验证命令")
            for v in validation_commands:
                lines.append(f"- `{v}`")
            lines.append("")

        if task.requirement.non_goals:
            lines.append("## 非目标（不要做）")
            for n in task.requirement.non_goals:
                lines.append(f"- {n}")
            lines.append("")

        lines.append("## 要求")
        lines.append("1. 先阅读相关代码和文档，理解上下文。")
        lines.append("2. 输出简短执行计划。")
        lines.append("3. 实现需求。")
        lines.append("4. 不修改禁止路径中的文件。")
        lines.append("5. 不写入密钥、token 或生产地址。")
        lines.append("6. 完成后报告变更文件列表和风险点。")

        return "\n".join(lines)
```

### 4.3 Codex Adapter

```python
# src/agent_platform/devflow/runner/adapters/codex.py

from __future__ import annotations

import asyncio
import logging

from agent_platform.devflow.runner.protocol import RunnerAdapter, RunnerAdapterResult
from agent_platform.devflow.task_pack import DevelopmentTask

logger = logging.getLogger(__name__)


class CodexAdapter:
    """通过 OpenAI Codex CLI 执行编码任务。

    调用方式：codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check --ephemeral <prompt>
    """

    def __init__(self, *, cli_path: str = "codex", model: str | None = None):
        self.cli_path = cli_path
        self.model = model
        self._process: asyncio.subprocess.Process | None = None

    @property
    def adapter_type(self) -> str:
        return "codex"

    async def execute(
        self,
        *,
        workspace_dir: str,
        task: DevelopmentTask,
        timeout_seconds: int = 600,
    ) -> RunnerAdapterResult:
        prompt = self._build_prompt(task)
        cmd = [
            self.cli_path,
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "--ephemeral",
        ]
        if self.model:
            cmd.extend(["--model", self.model])
        cmd.append(prompt)

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace_dir,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                self._process.communicate(),
                timeout=timeout_seconds,
            )
            stdout = stdout_bytes.decode(errors="replace")
            stderr = stderr_bytes.decode(errors="replace")
            exit_code = self._process.returncode or 0
            return RunnerAdapterResult(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                error_message=stderr.strip()[-1000:] if exit_code != 0 else None,
            )
        except asyncio.TimeoutError:
            await self.cancel()
            return RunnerAdapterResult(
                exit_code=-1,
                error_message=f"Codex timed out after {timeout_seconds}s",
            )

    async def cancel(self) -> None:
        if self._process and self._process.returncode is None:
            self._process.terminate()

    async def health_check(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.cli_path, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.wait(), timeout=10)
            return proc.returncode == 0
        except Exception:
            return False

    def _build_prompt(self, task: DevelopmentTask) -> str:
        # Codex prompt 格式与 Claude Code 类似，但更简洁
        scope_allowed = task.scope.get("write_allowed", [])
        scope_denied = task.scope.get("write_denied", [])
        lines = [
            f"任务: {task.metadata.title}",
            f"背景: {task.requirement.background}",
            f"允许修改: {', '.join(scope_allowed)}",
            f"禁止修改: {', '.join(scope_denied)}",
        ]
        if task.requirement.acceptance:
            lines.append(f"验收: {'; '.join(task.requirement.acceptance)}")
        if task.requirement.non_goals:
            lines.append(f"非目标: {'; '.join(task.requirement.non_goals)}")
        return "\n".join(lines)
```

### 4.4 Mock Adapter（测试用）

```python
# src/agent_platform/devflow/runner/adapters/mock.py

from __future__ import annotations

from pathlib import Path

from agent_platform.devflow.runner.protocol import RunnerAdapter, RunnerAdapterResult
from agent_platform.devflow.task_pack import DevelopmentTask


class MockRunnerAdapter:
    """用于测试的 mock 适配器。

    在 workspace_dir 中创建 task pack 声明的 required_outputs 文件。
    """

    def __init__(self, *, should_fail: bool = False):
        self.should_fail = should_fail
        self._cancelled = False

    @property
    def adapter_type(self) -> str:
        return "mock"

    async def execute(
        self,
        *,
        workspace_dir: str,
        task: DevelopmentTask,
        timeout_seconds: int = 600,
    ) -> RunnerAdapterResult:
        if self.should_fail:
            return RunnerAdapterResult(
                exit_code=1,
                error_message="Mock adapter configured to fail",
            )

        # 模拟创建 required_outputs 中声明的文件
        ws = Path(workspace_dir)
        changed: list[str] = []
        for output in task.implementation.get("required_outputs", []):
            # 跳过非具体文件路径（如 "docs update if contract changes"）
            if " " in output or not output.strip():
                continue
            path = ws / output
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text(f"# Generated for {task.metadata.task_id}\n")
                changed.append(output)

        return RunnerAdapterResult(
            exit_code=0,
            changed_files=changed,
            stdout="Mock adapter completed successfully",
        )

    async def cancel(self) -> None:
        self._cancelled = True

    async def health_check(self) -> bool:
        return True
```

### 4.5 适配器选择

```python
# src/agent_platform/devflow/runner/factory.py

from __future__ import annotations

from agent_platform.devflow.runner.adapters.claude_code import ClaudeCodeAdapter
from agent_platform.devflow.runner.adapters.codex import CodexAdapter
from agent_platform.devflow.runner.adapters.mock import MockRunnerAdapter
from agent_platform.devflow.runner.protocol import RunnerAdapter


def create_adapter(adapter_type: str, **kwargs) -> RunnerAdapter:
    """根据类型创建 RunnerAdapter 实例。"""
    adapters: dict[str, type] = {
        "claude_code": ClaudeCodeAdapter,
        "codex": CodexAdapter,
        "mock": MockRunnerAdapter,
    }
    cls = adapters.get(adapter_type)
    if cls is None:
        raise ValueError(f"Unknown adapter type: {adapter_type}. Available: {list(adapters.keys())}")
    return cls(**kwargs)
```

## 5. Workspace 生命周期

### 5.1 生命周期阶段

```
create --> clone --> run --> validate --> commit --> push --> cleanup
  |                                                            |
  |         (失败时)                                            |
  +-- 保留 workspace 用于排查 ---------------------------------+
```

### 5.2 WorkspaceManager

```python
# src/agent_platform/devflow/runner/workspace.py

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import uuid
from pathlib import Path

from agent_platform.devflow.runner.models import CommandResult, ValidationResult

logger = logging.getLogger(__name__)


class WorkspaceManager:
    """管理 coding agent 的隔离工作目录。

    每个 CodingJob 获得独立的 workspace。workspace 是一个
    git clone，切换到 task pack 指定的 work_branch。
    """

    def __init__(
        self,
        *,
        base_dir: str | Path | None = None,
        cleanup_on_success: bool = True,
        cleanup_on_failure: bool = False,
    ):
        self.base_dir = Path(base_dir) if base_dir else Path(tempfile.gettempdir()) / "devflow-workspaces"
        self.cleanup_on_success = cleanup_on_success
        self.cleanup_on_failure = cleanup_on_failure
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def create(self, *, branch: str, repo_url: str) -> Path:
        """创建一个新的隔离 workspace。

        步骤：
        1. 生成唯一 workspace 目录。
        2. git clone --branch <branch> --single-branch --depth 1 <repo_url> <dir>。
        3. 返回 workspace 路径。
        """
        workspace_id = f"ws-{uuid.uuid4().hex[:12]}"
        workspace_dir = self.base_dir / workspace_id
        workspace_dir.mkdir(parents=True, exist_ok=True)

        clone_cmd = [
            "git", "clone",
            "--branch", branch,
            "--single-branch",
            "--depth", "1",
            repo_url,
            str(workspace_dir),
        ]
        proc = await asyncio.create_subprocess_exec(
            *clone_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"git clone failed (exit {proc.returncode}): {stderr.decode(errors='replace')}"
            )

        logger.info("Workspace created: %s (branch: %s)", workspace_dir, branch)
        return workspace_dir

    def get_changed_files(self, workspace_dir: Path) -> list[str]:
        """获取 workspace 中所有修改、新增、删除的文件路径（相对于 workspace root）。

        使用 git status + git diff 获取。
        """
        import subprocess

        # untracked + modified + staged
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(workspace_dir),
            capture_output=True,
            text=True,
        )
        files = []
        for line in result.stdout.strip().splitlines():
            if len(line) > 3:
                files.append(line[3:].strip())
        return files

    async def run_validation(
        self,
        workspace_dir: Path,
        commands: list[str],
    ) -> ValidationResult:
        """在 workspace 中依次执行验证命令。

        每个命令独立执行，记录 exit code、stdout、stderr。
        全部命令 exit_code == 0 视为 all_passed。
        """
        results: list[CommandResult] = []
        all_passed = True

        for cmd in commands:
            import time
            start = time.monotonic()

            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace_dir),
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=120,
            )
            duration_ms = int((time.monotonic() - start) * 1000)

            cmd_result = CommandResult(
                command=cmd,
                exit_code=proc.returncode or 0,
                stdout=stdout_bytes.decode(errors="replace")[-2000:],  # 截断
                stderr=stderr_bytes.decode(errors="replace")[-2000:],
                duration_ms=duration_ms,
            )
            results.append(cmd_result)

            if cmd_result.exit_code != 0:
                all_passed = False

        # 收集 required_reports
        report_paths: list[str] = []
        for pattern in ["eval-report.json", "test-report.xml"]:
            found = list(workspace_dir.rglob(pattern))
            report_paths.extend(str(p.relative_to(workspace_dir)) for p in found)

        return ValidationResult(
            commands_executed=results,
            all_passed=all_passed,
            report_paths=report_paths,
        )

    async def commit_and_push(
        self,
        workspace_dir: Path,
        *,
        message: str,
        branch: str,
    ) -> str | None:
        """在 workspace 中执行 git add、commit、push。

        返回 commit SHA；如果无变更则返回 None。
        """
        # git add -A
        await self._run_git(workspace_dir, ["git", "add", "-A"])

        # git commit
        proc = await asyncio.create_subprocess_exec(
            "git", "commit", "-m", message,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workspace_dir),
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            if b"nothing to commit" in stdout or b"nothing to commit" in stderr:
                logger.info("Nothing to commit in %s", workspace_dir)
                return None
            raise RuntimeError(f"git commit failed: {stderr.decode(errors='replace')}")

        # git rev-parse HEAD
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "HEAD",
            stdout=asyncio.subprocess.PIPE,
            cwd=str(workspace_dir),
        )
        stdout, _ = await proc.communicate()
        commit_sha = stdout.decode().strip()

        # git push
        await self._run_git(workspace_dir, ["git", "push", "origin", branch])

        logger.info("Committed and pushed %s to %s", commit_sha[:8], branch)
        return commit_sha

    async def cleanup(self, workspace_dir: Path, *, keep_on_failure: bool = False) -> None:
        """清理 workspace 目录。"""
        if keep_on_failure:
            logger.info("Keeping workspace for debugging: %s", workspace_dir)
            return
        if workspace_dir.exists():
            shutil.rmtree(workspace_dir, ignore_errors=True)
            logger.info("Cleaned up workspace: %s", workspace_dir)

    async def _run_git(self, cwd: Path, cmd: list[str]) -> None:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"{' '.join(cmd)} failed (exit {proc.returncode}): "
                f"{stderr.decode(errors='replace')}"
            )
```

当前实现与初始伪代码相比有两点生产化调整：

1. 验证命令不使用 shell 直接执行，而是通过白名单和 `shlex.split()` 拆分，降低注入风险。
2. `pytest ...` 会解析为当前平台解释器的 `sys.executable -m pytest ...`，`python ...` 会解析为当前平台解释器，避免 clean env 下找不到 `.venv/bin/pytest`。

### 5.3 Workspace 策略

| 策略 | 说明 |
| --- | --- |
| 创建 | 每个 CodingJob 创建独立 workspace，通过 shallow clone 减少时间和磁盘 |
| 复用 | MVP 不支持复用。后续可按 branch 复用（需要 git fetch + reset） |
| 清理 | 成功：立即清理；失败：保留 workspace 用于排查（可配置） |
| 磁盘限制 | base_dir 按照配置固定，建议在 CI/CD 环境中挂载 tmpfs 或限速卷 |
| 超时 | workspace 创建超时跟随 git clone 超时（默认 60s），不计入 job timeout |

## 6. PathGuard 实现

### 6.1 设计原则

PathGuard 从 DevelopmentTask 的 `scope.write_allowed` 和 `scope.write_denied` 提取 glob pattern。在 runner 执行完毕后，对所有 changed files 逐一校验。

规则：
1. 先匹配 `write_denied`，命中则拒绝。
2. 再匹配 `write_allowed`，命中则允许。
3. 两者都不命中，默认拒绝。

### 6.2 实现

```python
# src/agent_platform/devflow/runner/path_guard.py

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field

from agent_platform.devflow.task_pack import DevelopmentTask


@dataclass(frozen=True)
class PathViolation:
    path: str
    reason: str


@dataclass
class PathGuard:
    """基于 glob pattern 的路径写入控制。

    从 DevelopmentTask.scope 提取 write_allowed 和 write_denied，
    对 runner 产出的 changed_files 逐一检查。
    """

    write_allowed: list[str] = field(default_factory=list)
    write_denied: list[str] = field(default_factory=list)

    @classmethod
    def from_task(cls, task: DevelopmentTask) -> PathGuard:
        return cls(
            write_allowed=task.scope.get("write_allowed", []),
            write_denied=task.scope.get("write_denied", []),
        )

    def check(self, changed_files: list[str]) -> list[PathViolation]:
        """检查所有变更文件是否合规。返回违规列表，空列表表示全部通过。"""
        violations: list[PathViolation] = []
        for file_path in changed_files:
            violation = self._check_single(file_path)
            if violation:
                violations.append(violation)
        return violations

    def _check_single(self, file_path: str) -> PathViolation | None:
        # 1. 先检查 denied — denied 优先
        for pattern in self.write_denied:
            if fnmatch.fnmatch(file_path, pattern):
                return PathViolation(
                    path=file_path,
                    reason=f"matches write_denied pattern: {pattern}",
                )

        # 2. 再检查 allowed
        for pattern in self.write_allowed:
            if fnmatch.fnmatch(file_path, pattern):
                return None  # 允许

        # 3. 都不匹配，默认拒绝
        return PathViolation(
            path=file_path,
            reason="not in any write_allowed pattern",
        )

    def is_allowed(self, file_path: str) -> bool:
        return self._check_single(file_path) is None
```

### 6.3 PathGuard 与已有 Task Pack 的对齐

当前 `TaskPackGenerator` 已经生成的 scope：

```python
scope={
    "write_allowed": [
        "src/agent_platform/**",
        "agents/**",
        "tests/**",
        "docs/**",
        "pyproject.toml",
        "uv.lock",
        "eval-report.json",
    ],
    "write_denied": [".env", "secrets/**", "deploy/prod/**", "infra/prod/**"],
}
```

PathGuard 直接消费这些字段，无需额外转换。

注意：路径匹配必须使用面向仓库相对路径的 glob 语义。真实联调中验证过 `PurePosixPath.match("agents/**")` 不能按预期匹配多级路径，因此当前代码使用 `fnmatchcase()`，确保 `agents/echo/evals/golden.yaml`、`src/agent_platform/tools/schema_validator.py`、`tests/unit/test_echo_agent.py` 都能被正确允许。

## 7. Job 状态机

```
                   ┌──────────┐
                   │ PENDING  │
                   └────┬─────┘
                        │
                        v
              ┌─────────────────────┐
              │ WORKSPACE_CREATING  │
              └────────┬────────────┘
                       │
          ┌────────────┼──────────────┐
          │ (创建失败)  │              │
          v            v              │
       FAILED       RUNNING           │
                       │              │
          ┌────────────┼──────────────┤
          │ (path违规)  │ (超时)       │
          v            v              │
       FAILED     TIMED_OUT           │
                       │              │
                       v              │
                  VALIDATING          │
                       │              │
          ┌────────────┼──────────────┘
          │ (验证失败)  │
          v            v
       FAILED     COMMITTING
                       │
          ┌────────────┤
          │ (提交失败)  │
          v            v
       FAILED     SUCCEEDED
```

状态转换规则：

| 当前状态 | 事件 | 目标状态 |
| --- | --- | --- |
| PENDING | 开始执行 | WORKSPACE_CREATING |
| WORKSPACE_CREATING | clone 成功 | RUNNING |
| WORKSPACE_CREATING | clone 失败 | FAILED |
| RUNNING | adapter 完成 + path guard 通过 | VALIDATING |
| RUNNING | path guard 违规 | FAILED |
| RUNNING | 超时 | TIMED_OUT |
| RUNNING | adapter 异常 | FAILED |
| RUNNING | 用户取消 | CANCELLED |
| VALIDATING | 全部命令通过 | COMMITTING |
| VALIDATING | 有命令失败 | FAILED |
| COMMITTING | push 成功 | SUCCEEDED |
| COMMITTING | push 失败 | FAILED |

## 8. 错误处理和重试

### 8.1 重试策略

```python
async def _execute_with_retry(self, job: CodingJob, task: DevelopmentTask) -> RunnerAdapterResult:
    """带重试的适配器执行。"""
    last_result = None
    for attempt in range(1, job.max_retries + 1):
        invocation = RunnerInvocation(
            invocation_id=str(uuid.uuid4()),
            attempt=attempt,
            adapter_type=self.adapter.adapter_type,
            started_at=datetime.utcnow(),
        )

        adapter_result = await self.adapter.execute(
            workspace_dir=job.workspace_dir,
            task=task,
            timeout_seconds=job.timeout_seconds,
        )

        invocation.finished_at = datetime.utcnow()
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
```

### 8.2 超时层次

| 层次 | 默认值 | 说明 |
| --- | --- | --- |
| Git clone | 60s | workspace 创建时的 git clone 超时 |
| Adapter execute | 600s（10 分钟） | coding agent 执行超时，可在 CodingJob 中覆盖 |
| Validation command | 120s/条 | 每条验证命令的独立超时 |
| 整体 job | 900s（15 分钟） | 从 PENDING 到 SUCCEEDED/FAILED 的总时长，由调用方控制 |

### 8.3 失败归档

失败时保留以下信息：

1. `CodingJob` 对象（包含所有 invocations、result、error_message）。
2. workspace 目录（配置 `keep_on_failure=True` 时保留）。
3. adapter stdout/stderr 截断保存在 RunnerInvocation。
4. validation 命令的 stdout/stderr 保存在 ValidationResult。
5. GitLab MR comment 包含失败摘要。
6. Plane comment 包含失败摘要和 job_id。

## 9. GitLab MR 集成

### 9.1 提交流程

```
workspace 内 git add -A
    |
    v
git commit -m "feat: <title>\n\nTask: <task_id>"
    |
    v
git push origin <work_branch>
    |
    v
GitLabAdapter.comment_merge_request(
    project_id, mr_iid, result_summary
)
```

### 9.2 MR Comment 格式

成功时：

```markdown
## DevFlow Runner 执行报告

**状态**: 成功
**Job ID**: job-abc123
**Adapter**: claude_code
**Commit**: abc1234

### 变更文件

- agents/promo_recommendation/manifest.yaml
- agents/promo_recommendation/prompts/orchestrator.md
- agents/promo_recommendation/evals/golden.yaml
- tests/unit/test_promo_routing.py

### 验证结果

| 命令 | 状态 | 耗时 |
| --- | --- | --- |
| `pytest tests/unit` | PASS | 3200ms |
| `python scripts/validate_manifest.py ...` | PASS | 450ms |
| `python scripts/run_agent_eval.py ...` | PASS | 8200ms |
```

失败时：

```markdown
## DevFlow Runner 执行报告

**状态**: 失败
**Job ID**: job-abc123
**错误类型**: PATH_VIOLATION

### 违规文件

- `.env` — matches write_denied pattern: .env
- `deploy/prod/config.yaml` — matches write_denied pattern: deploy/prod/**

Runner 因路径违规被终止。请检查 task pack 的 scope 配置。
```

### 9.3 Comment 生成

```python
def _build_mr_comment(self, job: CodingJob) -> str:
    """构建 MR comment 内容。"""
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
```

## 10. Plane 状态回写

### 10.1 回写时机和内容

| 时机 | Plane 操作 |
| --- | --- |
| Job 开始 | 已由 DevFlowOrchestrator 完成 (AI Developing) |
| Runner 成功 + validation 通过 | update_work_item_state("Testing / Eval")，add_comment(执行报告摘要) |
| Runner 成功 + validation 失败 | add_comment(失败报告)，状态保持 AI Developing |
| Runner 失败 (path violation / error / timeout) | add_comment(失败报告)，状态保持 AI Developing |
| CI pipeline 通过 (不在本文档范围) | 由外部 webhook 触发状态到 Human Review |

### 10.2 回写实现

```python
async def _report_result(self, job: CodingJob) -> None:
    """成功或部分成功时回写 GitLab 和 Plane。"""
    # GitLab MR comment
    if job.mr_iid:
        comment = self._build_mr_comment(job)
        try:
            await self.gitlab.comment_merge_request(
                self.gitlab_project_id, job.mr_iid, comment,
            )
        except Exception:
            logger.warning("Failed to comment on MR %s", job.mr_iid)

    # Plane comment + 状态
    if self.plane and job.plane_project_id and job.plane_work_item_id:
        summary = self._build_plane_comment(job)
        try:
            await self.plane.add_comment(
                job.plane_project_id, job.plane_work_item_id, summary,
            )
        except Exception:
            logger.warning("Failed to comment on Plane work item %s", job.plane_work_item_id)


async def _report_failure(self, job: CodingJob) -> None:
    """失败时回写 GitLab 和 Plane。"""
    await self._report_result(job)  # 复用相同逻辑，comment 内容由 result.status 决定


def _build_plane_comment(self, job: CodingJob) -> str:
    """构建 Plane HTML comment。"""
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
```

## 11. 安全约束

### 11.1 强制规则

| 约束 | 实现方式 |
| --- | --- |
| Runner 不能修改 workspace 之外的文件 | workspace 是独立 git clone，adapter 的 cwd 设为 workspace_dir |
| Runner 不能修改 write_denied 路径 | PathGuard 在执行后检查 changed_files |
| Runner 不能访问平台 secret | adapter 进程不继承平台环境变量中的 PLANE_API_KEY、GITLAB_TOKEN 等 |
| Runner 不能直接推送到 main | workspace clone 的是 work_branch，push 也是 work_branch |
| Runner 不能绕过 validation | CodingAgentRunner 强制在 commit 前执行 validation commands |
| Runner stdout/stderr 不能包含 secret | 日志截断保存，不包含环境变量注入 |

### 11.2 环境变量隔离

adapter 启动子进程时，必须使用受限的环境变量：

```python
def _build_safe_env(self) -> dict[str, str]:
    """构建不含 secret 的子进程环境变量。"""
    import os
    safe_env = dict(os.environ)
    # 移除所有平台 secret
    for key in list(safe_env.keys()):
        if any(secret_key in key.upper() for secret_key in [
            "PLANE_API_KEY", "GITLAB_TOKEN", "API_KEY",
            "SECRET", "PASSWORD", "CREDENTIAL",
        ]):
            del safe_env[key]
    return safe_env
```

### 11.3 Coding agent 自身的 API key

Claude Code 和 Codex 需要各自的 API key 来调用 LLM。这些 key 通过以下方式管理：

- `ANTHROPIC_API_KEY` — Claude Code 使用，由平台配置注入。
- `OPENAI_API_KEY` — Codex 使用，由平台配置注入。
- 这些 key 只在 adapter 子进程环境中有效，不暴露给 task pack 或 workspace 内文件。

## 12. 与 DevFlowOrchestrator 的集成

### 12.1 调用入口

```python
# DevFlowOrchestrator.handle_webhook_event() 末尾新增：

async def handle_webhook_event(self, event: str, payload: dict) -> DevFlowResult | None:
    # ... 现有逻辑 ...
    result = DevFlowResult(task_pack=task_pack, branch=branch, mr_url=mr_url, mr_iid=mr_iid)

    # 新增：异步触发 CodingAgentRunner
    if self.runner:
        import asyncio
        asyncio.create_task(
            self._run_coding_job(task_pack, mr_iid=mr_iid, work_item_context={
                "project_id": project_id,
                "work_item_id": work_item_id,
            })
        )

    return result

async def _run_coding_job(
    self,
    task: DevelopmentTask,
    *,
    mr_iid: int | None,
    work_item_context: dict,
) -> None:
    try:
        job = await self.runner.run(task, mr_iid=mr_iid)
        logger.info("Coding job %s completed: %s", job.job_id, job.state.value)
    except Exception:
        logger.exception("Coding job failed for task %s", task.metadata.task_id)
```

### 12.2 配置

新增到 `Settings`：

```python
# config.py 新增字段
runner_adapter_type: str | None = None        # "claude_code" | "codex" | "mock" | None
runner_workspace_base_dir: str | None = None  # 默认使用 tempdir
runner_timeout_seconds: int = 600
runner_max_retries: int = 1
runner_cleanup_on_failure: bool = False
```

环境变量：

```env
RUNNER_ADAPTER_TYPE=claude_code
RUNNER_WORKSPACE_BASE_DIR=/var/devflow/workspaces
RUNNER_TIMEOUT_SECONDS=600
RUNNER_MAX_RETRIES=1
RUNNER_CLEANUP_ON_FAILURE=false
```

## 13. 文件布局

```
src/agent_platform/devflow/
├── __init__.py                     # 现有
├── agents.py                       # 现有
├── orchestrator.py                 # 现有，需要小幅修改
├── scaffolder.py                   # 现有
├── task_pack.py                    # 现有
├── requirement_parser.py           # 现有
├── issue_generator.py              # 现有
└── runner/                         # 新增
    ├── __init__.py
    ├── models.py                   # CodingJob, JobState, RunnerResult, ...
    ├── protocol.py                 # RunnerAdapter Protocol
    ├── runner.py                   # CodingAgentRunner 主控
    ├── workspace.py                # WorkspaceManager
    ├── path_guard.py               # PathGuard
    ├── factory.py                  # create_adapter()
    └── adapters/
        ├── __init__.py
        ├── claude_code.py
        ├── codex.py
        ├── mock.py
        └── openhands.py            # 后续实现
```

## 14. 测试策略

### 14.1 单元测试

| 测试目标 | 测试方法 | 文件 |
| --- | --- | --- |
| PathGuard.check() | 给定 allowed/denied patterns 和 changed_files，断言违规列表 | `tests/unit/test_path_guard.py` |
| PathGuard.from_task() | 从 DevelopmentTask 提取 scope，断言 patterns 正确 | `tests/unit/test_path_guard.py` |
| MockRunnerAdapter | 调用 execute()，断言生成了 required_outputs 文件 | `tests/unit/test_mock_adapter.py` |
| CodingJob 状态转换 | 断言状态机合法性 | `tests/unit/test_coding_job.py` |
| MR comment 生成 | 给定 job result，断言 comment 格式 | `tests/unit/test_runner_comment.py` |

### 14.2 集成测试

| 测试目标 | 测试方法 | 文件 |
| --- | --- | --- |
| MockRunner 端到端 | 用 MockRunnerAdapter + 本地 git repo，从 task pack 到 job result | `tests/integration/test_runner_e2e.py` |
| PathGuard 违规拦截 | MockAdapter 修改 denied 路径，断言 job 失败且 result 包含违规信息 | `tests/integration/test_path_guard_enforcement.py` |
| Workspace 创建和清理 | 创建 workspace，写入文件，清理后断言目录不存在 | `tests/integration/test_workspace.py` |

### 14.3 验收标准测试

对应文档要求的三条验收标准：

**AC1: mock runner 可从 task pack 生成一次完整 job result**

```python
async def test_mock_runner_full_job():
    task = TaskPackGenerator().from_requirement(
        task_id="test-001",
        title="Test Task",
        task_type="platform:change",
        project_id="test-project",
        background="Test background",
    )
    adapter = MockRunnerAdapter()
    # ... 创建 runner，执行，断言 job.state == SUCCEEDED
    # ... 断言 job.result.changed_files 非空
    # ... 断言 job.result.status == ResultStatus.SUCCESS
```

**AC2: runner 不能修改 task pack 允许路径之外的文件**

```python
async def test_path_guard_blocks_denied_files():
    guard = PathGuard(
        write_allowed=["src/**", "tests/**"],
        write_denied=[".env", "secrets/**"],
    )
    violations = guard.check([".env", "src/main.py", "secrets/key.txt"])
    assert len(violations) == 2
    assert violations[0].path == ".env"
    assert violations[1].path == "secrets/key.txt"
```

**AC3: 测试结果和 changed files 可回写 GitLab MR comment**

```python
async def test_mr_comment_contains_results():
    job = CodingJob(
        job_id="job-test",
        task_id="task-test",
        state=JobState.SUCCEEDED,
        result=RunnerResult(
            status=ResultStatus.SUCCESS,
            changed_files=["src/main.py", "tests/test_main.py"],
            validation=ValidationResult(
                commands_executed=[
                    CommandResult(command="pytest", exit_code=0, duration_ms=1000),
                ],
                all_passed=True,
            ),
            commit_sha="abc1234567890",
        ),
    )
    comment = runner._build_mr_comment(job)
    assert "src/main.py" in comment
    assert "PASS" in comment
    assert "abc12345" in comment
```

### 14.4 真实 E2E 验证记录

2026-05-18 已使用真实 Plane、GitLab 和 Codex CLI 跑通端到端链路：

```bash
.venv/bin/python scripts/devflow_real_e2e.py
```

验证环境：

| 项 | 值 |
| --- | --- |
| Runner | `DEVFLOW_RUNNER_ADAPTER=codex` |
| Plane | `http://10.193.0.147:3333` |
| GitLab | `https://gitlab.ttyuyin.com` |
| 目标默认分支 | `master` |
| 验证结果 | `13 passed, 0 failed` |
| GitLab MR | `!11` |
| Runner commit | `3d7d6a99dac657bc4987b8891ab839d5cac8f650` |

该验证覆盖：

1. Plane API 可达和 Work Item 创建。
2. GitLab API 可达、feature branch 创建、MR 创建。
3. `DevFlowOrchestrator` 生成 TaskPack，并把默认分支、MR 链接、custom properties 回写 Plane。
4. `CodingAgentRunner` 创建真实 workspace，调用 `CodexAdapter` 修改代码。
5. `PathGuard` 校验真实 changed files。
6. `WorkspaceManager` 执行 `pytest`、contract test、manifest validate、agent eval。
7. 验证通过后 commit/push 到 GitLab branch。
8. GitLab MR comment 和 Plane comment 回写。

本次验证暴露并已修复的问题：

| 问题 | 修复 |
| --- | --- |
| Plane detail 不一定返回 `properties.agent_id/task_type` | Orchestrator 回退读取 webhook payload |
| GitLab 默认分支不是 `main` | TaskPack 使用 `DevFlowOrchestrator.default_branch` |
| `agents/**` glob 误判多级路径越界 | PathGuard 使用 `fnmatchcase()` |
| Codex 会合理生成 `uv.lock` / `eval-report.json` | TaskPack 默认 scope 显式允许 |
| clean env 下找不到 `pytest` | validation 命令解析为 `sys.executable -m pytest` |

## 15. MVP 限制和后续扩展

### 15.1 MVP 范围

1. 支持 MockRunnerAdapter、CodexAdapter 和 ClaudeCodeAdapter。
2. workspace 每次创建新 clone，不复用。
3. PathGuard 使用仓库相对路径 glob，不支持正则。
4. 重试最多 1 次（可配置为更多）。
5. job 状态可通过 repository 持久化，但 stdout/stderr 长日志仍需进一步归档。
6. 不支持多 job 并发执行（后续加 async job queue）。
7. 真实 Codex E2E 已跑通；Claude Code 真实 E2E 仍需单独稳定验证。

### 15.2 后续扩展

| 扩展项 | 阶段 | 说明 |
| --- | --- | --- |
| Job 持久化 | S2 | CodingJob 入 DB，支持查询历史和恢复 |
| Async job queue | S4+ | 用 asyncio.Queue 或 Celery/Dramatiq 管理并发 |
| Workspace 复用 | S4+ | 按 branch 缓存 workspace，git fetch 更新 |
| OpenHands adapter | S4+ | 通过 OpenHands API 调用 |
| 沙箱执行 | S5 | Docker 容器内运行 adapter，强隔离文件系统和网络 |
| Cost tracking | S5 | 按 job 统计 token 消耗和 API 调用成本 |
| Streaming 日志 | S5 | adapter 执行过程中实时推送 stdout 到 WebSocket |

## 16. 与其他设计文档的关系

| 文档 | 关系 |
| --- | --- |
| `01-contracts/devflow-task-pack.md` | Runner 的输入是 DevelopmentTask，直接消费 task pack 契约 |
| `04-devflow/gitlab.md` | Runner 通过 GitLabAdapter 提交 commit、更新 MR comment |
| `04-devflow/plane.md` | Runner 通过 PlaneAdapter 回写状态和 comment |
| `next-stage-design-plan.md` P0-4 | 本文档是 P0-4 的设计产出 |
| `next-stage-design-plan.md` P0-5 | 状态同步设计是本文档的依赖，Runner 只做基础回写 |
| `implementation-gap.md` 3.2 / 4.3 | 本文档补齐 DevFlow "执行闭环" 的差距 |
