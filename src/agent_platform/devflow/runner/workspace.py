from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from agent_platform.devflow.runner.models import CommandResult, ValidationResult

logger = logging.getLogger(__name__)


class WorkspaceManager:

    def __init__(
        self,
        *,
        base_dir: str | Path | None = None,
        cleanup_on_success: bool = True,
        cleanup_on_failure: bool = False,
    ):
        self.base_dir = (
            Path(base_dir) if base_dir
            else Path(tempfile.gettempdir()) / "devflow-workspaces"
        )
        self.cleanup_on_success = cleanup_on_success
        self.cleanup_on_failure = cleanup_on_failure
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def create(self, *, branch: str, repo_url: str) -> Path:
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
        results: list[CommandResult] = []
        all_passed = True

        for cmd in commands:
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
                stdout=stdout_bytes.decode(errors="replace")[-2000:],
                stderr=stderr_bytes.decode(errors="replace")[-2000:],
                duration_ms=duration_ms,
            )
            results.append(cmd_result)

            if cmd_result.exit_code != 0:
                all_passed = False

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
        await self._run_git(workspace_dir, ["git", "add", "-A"])

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
