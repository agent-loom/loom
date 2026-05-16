"""Codex CLI 适配器，通过子进程调用 Codex 执行编码任务。"""

from __future__ import annotations

import asyncio
import logging
import os

from agent_platform.devflow.runner.protocol import RunnerAdapterResult
from agent_platform.devflow.task_pack import DevelopmentTask

logger = logging.getLogger(__name__)

_SECRET_ENV_KEYWORDS = frozenset([
    "PLANE_API_KEY", "GITLAB_TOKEN", "API_KEY",
    "SECRET", "PASSWORD", "CREDENTIAL",
])


def _build_safe_env() -> dict[str, str]:
    safe_env = dict(os.environ)
    for key in list(safe_env.keys()):
        if any(kw in key.upper() for kw in _SECRET_ENV_KEYWORDS):
            del safe_env[key]
    return safe_env


class CodexAdapter:
    """Codex CLI 适配器。"""

    def __init__(self, *, cli_path: str = "codex", model: str | None = None):
        """初始化 Codex 适配器。"""
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
        """执行编码任务，超时则取消并返回错误。"""
        prompt = self._build_prompt(task)
        cmd = [self.cli_path, "--approval-mode", "full-auto", "--quiet"]
        if self.model:
            cmd.extend(["--model", self.model])
        cmd.append(prompt)

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace_dir,
                env=_build_safe_env(),
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                self._process.communicate(),
                timeout=timeout_seconds,
            )
            return RunnerAdapterResult(
                exit_code=self._process.returncode or 0,
                stdout=stdout_bytes.decode(errors="replace"),
                stderr=stderr_bytes.decode(errors="replace"),
            )
        except TimeoutError:
            await self.cancel()
            return RunnerAdapterResult(
                exit_code=-1,
                error_message=f"Codex timed out after {timeout_seconds}s",
            )

    async def cancel(self) -> None:
        """取消正在运行的子进程。"""
        if self._process and self._process.returncode is None:
            self._process.terminate()

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
