"""Codex CLI 适配器，通过子进程调用 Codex 执行编码任务。"""

from __future__ import annotations

import asyncio
import logging

from agent_platform.devflow.runner.adapters.utils import build_safe_env
from agent_platform.devflow.runner.protocol import RunnerAdapterResult
from agent_platform.devflow.task_pack import DevelopmentTask

logger = logging.getLogger(__name__)


class CodexAdapter:
    """Codex CLI 适配器。

    支持两种沙箱模式：
    - bypass: 使用 --dangerously-bypass-approvals-and-sandbox 跳过沙箱（默认，开发环境）
    - docker: 通过 docker 容器隔离执行（推荐生产环境使用）
    """

    _VALID_SANDBOX_MODES = ("bypass", "docker")

    def __init__(
        self,
        *,
        cli_path: str = "codex",
        model: str | None = None,
        profile: str | None = None,
        sandbox_mode: str = "bypass",
        docker_image: str = "codex-runner",
    ):
        """初始化 Codex 适配器。

        Args:
            cli_path: Codex CLI 可执行文件路径。
            model: 指定使用的模型名称。
            profile: Codex 配置文件名。
            sandbox_mode: 沙箱模式，可选 'bypass' 或 'docker'。
            docker_image: docker 模式下使用的镜像名。
        """
        if sandbox_mode not in self._VALID_SANDBOX_MODES:
            raise ValueError(
                f"无效的 sandbox_mode: {sandbox_mode!r}，"
                f"可选值: {self._VALID_SANDBOX_MODES}"
            )
        self.cli_path = cli_path
        self.model = model
        self.profile = profile
        self.sandbox_mode = sandbox_mode
        self.docker_image = docker_image
        self._process: asyncio.subprocess.Process | None = None
        self._bypass_warned: bool = False

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
        cmd = self._build_cmd(prompt, workspace_dir)
        # docker 模式下 cwd 无意义（容器内使用 -w /workspace），
        # bypass 模式下 cwd 传入工作目录
        exec_cwd = workspace_dir if self.sandbox_mode == "bypass" else None

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=exec_cwd,
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

    def _build_cmd(self, prompt: str, workspace_dir: str) -> list[str]:
        """根据沙箱模式构建执行命令。"""
        if self.sandbox_mode == "bypass":
            # bypass 模式：直接调用 CLI，跳过沙箱
            if not self._bypass_warned:
                logger.warning(
                    "Codex 以 bypass 沙箱模式运行，生产环境应使用 docker 模式"
                )
                self._bypass_warned = True
            cmd = [
                self.cli_path,
                "exec",
                "--dangerously-bypass-approvals-and-sandbox",
                "--skip-git-repo-check",
                "--ephemeral",
            ]
        else:
            # docker 模式：通过 docker 容器隔离执行
            cmd = [
                "docker", "run", "--rm",
                "-v", f"{workspace_dir}:/workspace",
                "-w", "/workspace",
                "--network", "none",
                "--memory", "2g",
                "--cpus", "2",
                self.docker_image,
                self.cli_path, "exec",
                "--skip-git-repo-check",
                "--ephemeral",
            ]

        if self.profile:
            cmd.extend(["-c", f"profile={self.profile}"])
        if self.model:
            cmd.extend(["--model", self.model])
        cmd.append(prompt)
        return cmd

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
