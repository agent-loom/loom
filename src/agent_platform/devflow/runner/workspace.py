"""运行时工作空间管理器：Git 本地隔离沙箱生命周期控制。

设计定位：
  DevFlow 研发沙箱运行层 (Sandbox Workspace Manager)。
  对应 docs/04-devflow/devflow-runner-workspace-design.md 中的"工作区管理器"设计。
  负责为每次 AI 编码与自动提议任务拉起一个干净、隔离的 Git 本地工作副本目录，
  处理克隆、动态拉取分支 checkout、变更提取、受限的命令行回归验证，
  以及将通过验证的代码通过 Git commit & push 最终同步推送至远程演进分支，并最终清理沙箱现场。
"""

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
from agent_platform.devflow.runner.command_guard import CommandGuard, GuardVerdict
from agent_platform.devflow.runner.models import CommandResult, ValidationResult

logger = logging.getLogger(__name__)


class WorkspaceManager:
    """工作空间管理器 (Workspace Manager)

    实现 AI 编码与验证沙箱的完整物理周期治理。
    状态管线：
      WORKSPACE_CREATING (克隆) -> RUNNING (代码生成) -> VALIDATING (测试执行) -> COMMITTING (强推发布) -> CLEANING (现场销毁)
    """

    # 限制 Git 进程生命周期，防止僵死子进程无限期阻塞 Runner 引擎
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

        # 克隆默认分支（不指定 --branch），避免新建分支尚未推送远程时克隆失败的竞态问题
        clone_cmd = [
            "git", "clone",
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

        # 克隆完成后再切换到目标分支（先拉取远程引用，再本地 checkout）
        await self._fetch_and_checkout(workspace_dir, branch)

        logger.info("Workspace created: %s (branch: %s)", workspace_dir, branch)
        return workspace_dir

    async def _fetch_and_checkout(self, workspace_dir: Path, branch: str) -> None:
        """
        拉取远程分支引用并在本地创建跟踪分支。
        若远程分支不存在（刚创建的空分支），则直接在本地新建。
        """
        # 拉取所有远程引用（不展开，仅更新 refs）
        fetch_proc = await asyncio.create_subprocess_exec(
            "git", "fetch", "origin", branch,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workspace_dir),
        )
        try:
            _, fetch_stderr = await asyncio.wait_for(
                fetch_proc.communicate(), timeout=self.GIT_CLONE_TIMEOUT,
            )
        except TimeoutError:
            fetch_proc.kill()
            await fetch_proc.wait()
            raise RuntimeError(f"git fetch timed out for branch {branch}") from None

        if fetch_proc.returncode == 0:
            # 远程分支存在，切到它（fetch 只更新 FETCH_HEAD，不创建 origin/<branch> tracking ref）
            await self._run_git(
                workspace_dir,
                ["git", "checkout", "-b", branch, "FETCH_HEAD"],
            )
        else:
            # 远程分支不存在（罕见，防御性处理），本地新建
            logger.warning(
                "Remote branch %s not found (%s), creating locally",
                branch,
                fetch_stderr.decode(errors="replace").strip(),
            )
            await self._run_git(workspace_dir, ["git", "checkout", "-b", branch])

    async def get_changed_files(self, workspace_dir: Path) -> list[str]:
        """
        使用 git status 获取当前工作区中发生了修改、新增或删除的文件列表。

        :param workspace_dir: 工作区目录。
        :return: 变更文件的相对路径列表。
        """
        # TODO Design Gap:
        # get_changed_files 获取变更文件时仅使用了 GIT_COMMAND_TIMEOUT 做超时拦截，
        # 目前已补齐 wait_for 守护，但由于 status 输出量过大时（如大项目被全删全加），可能会面临
        # IO 阻塞卡顿风险，后续有必要考虑在大型仓库中基于 git diff-index 进行替代以提升查询性能。
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

    # 沙箱运行环境受限制的可信验证指令白名单。
    # 任何不在白名单内的基础命令头都将被就地拒绝，保障执行的命令边界。
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
            guard_result = CommandGuard.check(cmd)
            if guard_result.verdict == GuardVerdict.BLOCKED:
                logger.warning("Command Guard 拦截危险命令: %s — %s", cmd, guard_result.reason)
                results.append(CommandResult(
                    command=cmd, exit_code=1,
                    stdout="", stderr=f"Command Guard: {guard_result.reason}",
                    duration_ms=0,
                ))
                all_passed = False
                continue

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

            # TODO Design Gap:
            # 1. 这里执行验证命令的 asyncio.wait_for 中的 timeout 目前硬编码为了 120 秒，
            #    并未暴露给任务参数或与类级 constants (例如 GIT_COMMAND_TIMEOUT) 联动，导致复杂集成测试极易超时被杀。
            # 2. stdout_bytes 和 stderr_bytes 的截断只保留了最尾部的 2000 字符 (decode[-2000:])，
            #    如果测试套件报错信息长达上万字节，头部关键错误栈将会被默默物理截断抛弃，导致 AI 诊断自进化时信息残缺不齐。
            #    后续应考虑将完整输出重定向写入审计/测试报告文件中持久化保留。
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
        """对齐当前 python 进程执行器。

        工作区沙箱执行时使用经过极度净化的精简安全环境变量，
        因此若直接依靠 PATH 解析 `pytest` 或虚拟环境的 `python` 是脆弱且易损的。
        这里强制劫持并重定向替换为启动本平台进程的 python 物理编译解释器路径。
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
        # TODO Design Gap:
        # 此模块目前缺乏 commit 变更预览与二次确认插槽 (dry-run mode)。
        # 一旦 AI 沙箱脚本验证全数 PASS，修改将绕过任何人工或高级别安全校验，直接强推 (Push) 至远程生产/进化分支。
        # 未来有必要引入审批节点拦截机制，输出暂存 diff 后挂起，待 HITL 回复后再执行远程推送。
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