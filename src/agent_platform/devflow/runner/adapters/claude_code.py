"""Claude Code CLI 适配器，通过子进程调用 Claude Code 执行编码任务。"""

from __future__ import annotations

import asyncio
import logging

from agent_platform.devflow.runner.adapters.utils import build_safe_env
from agent_platform.devflow.runner.protocol import RunnerAdapterResult
from agent_platform.devflow.task_pack import DevelopmentTask

logger = logging.getLogger(__name__)


class ClaudeCodeAdapter:
    """Claude Code CLI 适配器。"""

    def __init__(
        self,
        *,
        cli_path: str = "claude",
        max_turns: int = 30,
        model: str | None = None,
    ):
        """初始化 Claude Code 适配器。"""
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
        """执行编码任务，超时则取消并返回错误。"""
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
                env=build_safe_env(),
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

        except TimeoutError:
            await self.cancel()
            return RunnerAdapterResult(
                exit_code=-1,
                error_message=f"Claude Code timed out after {timeout_seconds}s",
            )
        except FileNotFoundError as exc:
            return RunnerAdapterResult(
                exit_code=127,
                error_message=f"Claude Code CLI not found: {exc}",
            )
        except PermissionError as exc:
            return RunnerAdapterResult(
                exit_code=126,
                error_message=f"Claude Code CLI permission error: {exc}",
            )
        except OSError as exc:
            return RunnerAdapterResult(
                exit_code=126,
                error_message=f"Claude Code CLI failed to start: {exc}",
            )
        except Exception as exc:
            logger.exception("Claude Code adapter failed")
            return RunnerAdapterResult(
                exit_code=1,
                error_message=f"Claude Code adapter failed: {exc}",
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
        """检查 Claude Code CLI 是否可用。"""
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
