from __future__ import annotations

import asyncio
import logging
import shlex
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

from agent_platform.devflow.runner.adapters.utils import build_safe_env
from agent_platform.devflow.runner.models import CommandResult, ValidationResult

logger = logging.getLogger(__name__)


class WorkspaceManager:
    """
    工作区管理器。
    负责为每次 AI 编码任务建立独立的本地环境，处理代码克隆、文件变更检测、
    执行验证脚本、以及最终的代码提交、推送和工作区清理。
    """

    # Timeouts prevent zombie git processes from blocking the runner indefinitely
    GIT_CLONE_TIMEOUT = 300
    GIT_COMMAND_TIMEOUT = 120

    def __init__(
        self,
        *,
        base_dir: str | Path | None = None,
        cleanup_on_success: bool = True,
        cleanup_on_failure: bool = False,
    ):
        """
        初始化工作区管理器。

        :param base_dir: 工作区的根目录，如果未提供则使用系统临时目录下的 devflow-workspaces。
        :param cleanup_on_success: 任务成功完成后是否清理工作区，默认为 True。
        :param cleanup_on_failure: 任务失败后是否清理工作区，默认为 False（保留以供排错）。
        """
        self.base_dir = (
            Path(base_dir) if base_dir
            else Path(tempfile.gettempdir()) / "devflow-workspaces"
        )
        self.cleanup_on_success = cleanup_on_success
        self.cleanup_on_failure = cleanup_on_failure
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def create(self, *, branch: str, repo_url: str) -> Path:
        """
        创建新的工作区并从远程仓库克隆代码。

        :param branch: 目标分支。
        :param repo_url: Git 仓库的拉取地址。
        :return: 返回创建的本地工作区目录的 Path 对象。
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
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.GIT_CLONE_TIMEOUT,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(
                f"git clone timed out after {self.GIT_CLONE_TIMEOUT}s for {repo_url}"
            ) from None
        if proc.returncode != 0:
            raise RuntimeError(
                f"git clone failed (exit {proc.returncode}): {stderr.decode(errors='replace')}"
            )

        logger.info("Workspace created: %s (branch: %s)", workspace_dir, branch)
        return workspace_dir

    async def get_changed_files(self, workspace_dir: Path) -> list[str]:
        """
        使用 git status 获取当前工作区中发生了修改、新增或删除的文件列表。

        :param workspace_dir: 工作区目录。
        :return: 变更文件的相对路径列表。
        """
        proc = await asyncio.create_subprocess_exec(
            "git", "status", "--porcelain=v1", "-z", "-uall",
            cwd=str(workspace_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=self.GIT_COMMAND_TIMEOUT,
        )
        if proc.returncode != 0:
            logger.warning(
                "git status failed (exit %d): %s",
                proc.returncode,
                stderr_bytes.decode(errors="replace"),
            )
            return []
        files = []
        parts = stdout_bytes.split(b'\x00')
        i = 0
        while i < len(parts):
            part = parts[i]
            if not part:
                i += 1
                continue
            status = part[:2].decode(errors='replace')
            path = part[3:].decode(errors='replace')
            # 如果是重命名或拷贝，提取新文件路径
            if status.startswith("R") or status.startswith("C"):
                files.append(parts[i+1].decode(errors='replace'))
                i += 2
            else:
                files.append(path)
                i += 1
        return files

    _ALLOWED_COMMANDS = frozenset({
        "pytest", "python", "python3", "ruff", "mypy", "flake8", "black",
        "npm", "npx", "node", "cargo", "go", "make",
        "cat", "ls", "echo", "grep", "diff", "head", "tail",
    })

    async def run_validation(
        self,
        workspace_dir: Path,
        commands: list[str],
    ) -> ValidationResult:
        """
        在工作区目录中执行指定的验证命令列表。

        :param workspace_dir: 工作区目录。
        :param commands: 待执行的 shell 命令列表。
        :return: 包含验证结果和解析出的测试报告路径信息的 ValidationResult。
        """
        results: list[CommandResult] = []
        all_passed = True

        for cmd in commands:
            cmd_base = cmd.strip().split()[0] if cmd.strip() else ""
            if cmd_base not in self._ALLOWED_COMMANDS:
                logger.warning("拒绝执行不在白名单的命令: %s", cmd_base)
                results.append(CommandResult(
                    command=cmd, exit_code=1,
                    stdout="", stderr=f"命令 '{cmd_base}' 不在白名单中",
                    duration_ms=0,
                ))
                all_passed = False
                continue

            start = time.monotonic()
            command_args = self._resolve_validation_command(cmd)

            proc = await asyncio.create_subprocess_exec(
                *command_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace_dir),
                env=build_safe_env(),
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=120,
                )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                raise
            duration_ms = int((time.monotonic() - start) * 1000)

            cmd_result = CommandResult(
                command=cmd,
                exit_code=proc.returncode or 0,
                stdout=stdout_bytes.decode(errors="replace")[-2000:],
                stderr=stderr_bytes.decode(errors="replace")[-2000:],
                duration_ms=duration_ms,
            )
            results.append(cmd_result)

            if cmd_result.exit_code != 0:
                all_passed = False

        # 尝试搜集常见的测试或验证报告文件
        report_paths: list[str] = []
        for pattern in ["eval-report.json", "test-report.xml"]:
            found = list(workspace_dir.rglob(pattern))
            report_paths.extend(str(p.relative_to(workspace_dir)) for p in found)

        return ValidationResult(
            commands_executed=results,
            all_passed=all_passed,
            report_paths=report_paths,
        )

    @staticmethod
    def _resolve_validation_command(command: str) -> list[str]:
        """Resolve Python tool commands against the current interpreter.

        Runner workspaces are intentionally executed with a sanitized
        environment, so relying on PATH to find ``pytest`` or the intended
        virtualenv ``python`` is brittle. Use the interpreter that launched the
        platform process for Python validation commands.
        """
        parts = shlex.split(command)
        if not parts:
            return []
        executable, *args = parts
        if executable == "pytest":
            return [sys.executable, "-m", "pytest", *args]
        if executable == "python":
            return [sys.executable, *args]
        return parts

    async def commit_and_push(
        self,
        workspace_dir: Path,
        *,
        message: str,
        branch: str,
        changed_files: list[str],
    ) -> str | None:
        """
        将所有变更文件添加进暂存区，并进行提交和推送到远程分支。

        :param workspace_dir: 工作区目录。
        :param message: Git commit 描述信息。
        :param branch: 推送的目标分支名。
        :param changed_files: 需要暂存的文件列表。
        :return: 成功提交后的 commit sha，若没有变更则返回 None。
        """
        if not changed_files:
            return None
            
        cmd = ["git", "add", "--"] + changed_files
        await self._run_git(workspace_dir, cmd)

        # 配置提交的用户信息
        await self._run_git(
            workspace_dir,
            ["git", "config", "user.name", "Agent Platform DevFlow"],
        )
        await self._run_git(
            workspace_dir,
            ["git", "config", "user.email", "devflow@agent.platform"],
        )

        proc = await asyncio.create_subprocess_exec(
            "git", "commit", "-m", message,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workspace_dir),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.GIT_COMMAND_TIMEOUT,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(
                f"git commit timed out after {self.GIT_COMMAND_TIMEOUT}s"
            ) from None
        if proc.returncode != 0:
            if b"nothing to commit" in stdout or b"nothing to commit" in stderr:
                logger.info("Nothing to commit in %s", workspace_dir)
                return None
            raise RuntimeError(f"git commit failed: {stderr.decode(errors='replace')}")

        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "HEAD",
            stdout=asyncio.subprocess.PIPE,
            cwd=str(workspace_dir),
        )
        stdout, _ = await proc.communicate()
        commit_sha = stdout.decode().strip()

        await self._run_git(workspace_dir, ["git", "push", "origin", branch])

        logger.info("Committed and pushed %s to %s", commit_sha[:8], branch)
        return commit_sha

    async def cleanup(self, workspace_dir: Path, *, keep_on_failure: bool = False) -> None:
        """
        清理并删除本地工作区目录。

        :param workspace_dir: 需要清理的工作区目录。
        :param keep_on_failure: 如果被标记为失败且需要保留现场时传 True，否则删除目录。
        """
        if keep_on_failure:
            logger.info("Keeping workspace for debugging: %s", workspace_dir)
            return
        if workspace_dir.exists():
            await asyncio.to_thread(shutil.rmtree, workspace_dir, ignore_errors=True)
            logger.info("Cleaned up workspace: %s", workspace_dir)

    async def _run_git(self, cwd: Path, cmd: list[str]) -> None:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.GIT_COMMAND_TIMEOUT,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(
                f"{' '.join(cmd)} timed out after {self.GIT_COMMAND_TIMEOUT}s"
            ) from None
        if proc.returncode != 0:
            raise RuntimeError(
                f"{' '.join(cmd)} failed (exit {proc.returncode}): "
                f"{stderr.decode(errors='replace')}"
            )
