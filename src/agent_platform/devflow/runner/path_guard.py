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
    write_allowed: list[str] = field(default_factory=list)
    write_denied: list[str] = field(default_factory=list)

    @classmethod
    def from_task(cls, task: DevelopmentTask) -> PathGuard:
        return cls(
            write_allowed=task.scope.get("write_allowed", []),
            write_denied=task.scope.get("write_denied", []),
        )

    def check(self, changed_files: list[str]) -> list[PathViolation]:
        violations: list[PathViolation] = []
        for file_path in changed_files:
            violation = self._check_single(file_path)
            if violation:
                violations.append(violation)
        return violations

    def _check_single(self, file_path: str) -> PathViolation | None:
        for pattern in self.write_denied:
            if fnmatch.fnmatch(file_path, pattern):
                return PathViolation(
                    path=file_path,
                    reason=f"matches write_denied pattern: {pattern}",
                )

        for pattern in self.write_allowed:
            if fnmatch.fnmatch(file_path, pattern):
                return None

        return PathViolation(
            path=file_path,
            reason="not in any write_allowed pattern",
        )

    def is_allowed(self, file_path: str) -> bool:
        return self._check_single(file_path) is None
