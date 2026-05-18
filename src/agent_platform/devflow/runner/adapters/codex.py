"""Codex CLI 适配器，通过子进程调用 Codex 执行编码任务。"""

from __future__ import annotations

import asyncio
import logging

from agent_platform.devflow.runner.adapters.utils import build_safe_env
from agent_platform.devflow.runner.protocol import RunnerAdapterResult
from agent_platform.devflow.task_pack import DevelopmentTask

logger = logging.getLogger(__name__)


class CodexAdapter:
    """Codex CLI 适配器。"""

    def __init__(
        self,
        *,
        cli_path: str = "codex",
        model: str | None = None,
        profile: str | None = None,
    ):
        """初始化 Codex 适配器。"""
        self.cli_path = cli_path
        self.model = model
        self.profile = profile
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
        """执行编码任务，超时则取消并返回错误。"""
        prompt = self._build_prompt(task)
        cmd = [
            self.cli_path,
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "--ephemeral",
        ]
        if self.profile:
            cmd.extend(["-c", f"profile={self.profile}"])
        if self.model:
            cmd.extend(["--model", self.model])
        cmd.append(prompt)

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace_dir,
                env=build_safe_env(),
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
        except TimeoutError:
            await self.cancel()
            return RunnerAdapterResult(
                exit_code=-1,
                error_message=f"Codex timed out after {timeout_seconds}s",
            )
        except FileNotFoundError as exc:
            return RunnerAdapterResult(
                exit_code=127,
                error_message=f"Codex CLI not found: {exc}",
            )
        except PermissionError as exc:
            return RunnerAdapterResult(
                exit_code=126,
                error_message=f"Codex CLI permission error: {exc}",
            )
        except OSError as exc:
            return RunnerAdapterResult(
                exit_code=126,
                error_message=f"Codex CLI failed to start: {exc}",
            )
        except Exception as exc:
            logger.exception("Codex adapter failed")
            return RunnerAdapterResult(
                exit_code=1,
                error_message=f"Codex adapter failed: {exc}",
            )

    async def cancel(self) -> None:
        """取消正在运行的子进程。"""
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=10)
            except TimeoutError:
                self._process.kill()

    async def health_check(self) -> bool:
        """检查 Codex CLI 是否可用。"""
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
        scope_allowed = task.scope.get("write_allowed", [])
        scope_denied = task.scope.get("write_denied", [])
        lines = [
            f"# 任务: {task.metadata.title}",
            f"Task ID: {task.metadata.task_id}",
            "",
            "## 需求背景",
            task.requirement.background,
            "",
            "## 允许修改的路径",
            *[f"- {item}" for item in scope_allowed],
            "",
            "## 禁止修改的路径",
            *[f"- {item}" for item in scope_denied],
        ]
        if task.requirement.acceptance:
            lines.extend(["", "## 验收标准"])
            lines.extend(f"- {item}" for item in task.requirement.acceptance)
        if task.requirement.non_goals:
            lines.extend(["", "## 非目标（不要做）"])
            lines.extend(f"- {item}" for item in task.requirement.non_goals)
        required_outputs = task.implementation.get("required_outputs", [])
        if required_outputs:
            lines.extend(["", "## 必须产出或修改"])
            lines.extend(f"- {item}" for item in required_outputs)
        validation_commands = task.validation.get("commands", [])
        if validation_commands:
            lines.extend(["", "## 完成后请执行以下验证命令"])
            lines.extend(f"- `{item}`" for item in validation_commands)
        lines.extend([
            "",
            "## 要求",
            "1. 先阅读相关代码，理解上下文。",
            "2. 只修改允许路径，禁止写入密钥或生产凭证。",
            "3. 完成后简要说明变更文件和风险。",
        ])
        return "\n".join(lines)
