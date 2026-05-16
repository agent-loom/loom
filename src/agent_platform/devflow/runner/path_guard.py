"""文件路径安全守卫，检查变更文件是否在允许范围内。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from agent_platform.devflow.task_pack import DevelopmentTask


def _glob_match(path: str, pattern: str) -> bool:
    """Match *path* against *pattern* using PurePosixPath which correctly handles ``**`` recursive globs (fnmatch does not)."""
    return PurePosixPath(path).match(pattern)


@dataclass(frozen=True)
class PathViolation:
    """路径违规记录，包含路径和违规原因。"""
    path: str
    reason: str


@dataclass
class PathGuard:
    """基于白名单/黑名单模式的文件路径守卫。"""
    write_allowed: list[str] = field(default_factory=list)
    write_denied: list[str] = field(default_factory=list)

    @classmethod
    def from_task(cls, task: DevelopmentTask) -> PathGuard:
        return cls(
            write_allowed=task.scope.get("write_allowed", []),
            write_denied=task.scope.get("write_denied", []),
        )

    def check(self, changed_files: list[str]) -> list[PathViolation]:
        """批量检查变更文件，返回违规列表。"""
        violations: list[PathViolation] = []
        for file_path in changed_files:
            violation = self._check_single(file_path)
            if violation:
                violations.append(violation)
        return violations

    def _check_single(self, file_path: str) -> PathViolation | None:
        for pattern in self.write_denied:
            if _glob_match(file_path, pattern):
                return PathViolation(
                    path=file_path,
                    reason=f"matches write_denied pattern: {pattern}",
                )

        for pattern in self.write_allowed:
            if _glob_match(file_path, pattern):
                return None

        return PathViolation(
            path=file_path,
            reason="not in any write_allowed pattern",
        )

    def is_allowed(self, file_path: str) -> bool:
        """判断单个文件路径是否被允许修改。"""
        return self._check_single(file_path) is None
