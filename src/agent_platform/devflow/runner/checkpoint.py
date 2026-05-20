"""工作区检查点管理器，在 Runner 关键阶段创建轻量快照。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 30


@dataclass(frozen=True)
class Checkpoint:
    """单个工作区检查点。"""

    checkpoint_id: str
    checkpoint_type: str
    head_sha: str
    diff_stat: str
    changed_files: list[str]
    created_at: datetime


class CheckpointManager:
    """为 Runner 在关键阶段创建 git 状态快照。

    四个检查点类型：
    - before_runner: adapter 执行前（基线）
    - before_validation: adapter 执行后、验证前
    - before_commit: 验证通过后、commit 前
    - after_commit: commit+push 成功后
    """

    def __init__(self) -> None:
        self._checkpoints: dict[str, list[Checkpoint]] = {}

    async def create(
        self, workspace_dir: Path, checkpoint_type: str,
    ) -> Checkpoint:
        head_sha = await self._git_output(
            workspace_dir, ["git", "rev-parse", "HEAD"],
        )

        diff_stat = await self._git_output(
            workspace_dir, ["git", "diff", "--stat", "HEAD"],
        )

        status_output = await self._git_output(
            workspace_dir, ["git", "status", "--porcelain", "-uall"],
        )
        changed_files = [
            line[3:] for line in status_output.splitlines() if len(line) > 3
        ]

        cp = Checkpoint(
            checkpoint_id=f"cp-{uuid.uuid4().hex[:8]}",
            checkpoint_type=checkpoint_type,
            head_sha=head_sha,
            diff_stat=diff_stat[:2000],
            changed_files=changed_files,
            created_at=datetime.now(UTC),
        )

        key = str(workspace_dir)
        self._checkpoints.setdefault(key, []).append(cp)
        logger.info(
            "Checkpoint [%s] %s: head=%s, changed=%d",
            cp.checkpoint_type, cp.checkpoint_id, head_sha[:8], len(changed_files),
        )
        return cp

    def get_checkpoints(self, workspace_dir: Path) -> list[Checkpoint]:
        return list(self._checkpoints.get(str(workspace_dir), []))

    def format_for_report(self, workspace_dir: Path) -> str:
        cps = self.get_checkpoints(workspace_dir)
        if not cps:
            return ""
        lines = ["## Checkpoints", ""]
        for cp in cps:
            lines.append(
                f"- **{cp.checkpoint_type}** `{cp.checkpoint_id}` "
                f"head=`{cp.head_sha[:8]}` "
                f"files={len(cp.changed_files)} "
                f"at {cp.created_at:%H:%M:%S}",
            )
        return "\n".join(lines)

    @staticmethod
    async def _git_output(cwd: Path, cmd: list[str]) -> str:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=_GIT_TIMEOUT,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return ""
        return stdout.decode(errors="replace").strip()
