"""命令级安全守卫，拦截危险 shell 命令。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class GuardVerdict(StrEnum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class CommandGuardResult:
    verdict: GuardVerdict
    reason: str = ""


_HARD_BLOCK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+(-\w*r\w*f|--force)\s+/\s*$", re.I),
    re.compile(r"\brm\s+-rf\b", re.I),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\b.*\bof=/dev/"),
    re.compile(r"\b(shutdown|reboot|poweroff|halt)\b"),
    re.compile(r"\bsudo\s+-S\b"),
    re.compile(r"\bsudo\s+rm\b"),
    re.compile(r"\bcat\s+\.env\b"),
    re.compile(r"\bcat\s+secrets/"),
    re.compile(r"\bgit\s+push\s+origin\s+(master|main)\b"),
    re.compile(r"\bgit\s+push\s+--force\b"),
    re.compile(r"\bgit\s+push\s+-f\b"),
    re.compile(r"\bkubectl\s+apply\s+-f\s+deploy/prod\b"),
    re.compile(r"\bkubectl\s+delete\b"),
    re.compile(r"\bcurl\b.*\|\s*(ba)?sh\b"),
    re.compile(r"\bwget\b.*\|\s*(ba)?sh\b"),
    re.compile(r"\bchmod\s+777\b"),
    re.compile(r"\bchown\s+-R\s+root\b"),
    re.compile(r"\b>\s*/etc/"),
    re.compile(r"\btruncate\b"),
    re.compile(r"\bnohup\b"),
    re.compile(r"\bdocker\s+rm\s+-f\b"),
    re.compile(r"\bdocker\s+system\s+prune\b"),
]


class CommandGuard:
    """检查命令是否在 Hard Block 列表中。"""

    @staticmethod
    def check(command: str) -> CommandGuardResult:
        if not command or not command.strip():
            return CommandGuardResult(verdict=GuardVerdict.ALLOWED)

        for pattern in _HARD_BLOCK_PATTERNS:
            if pattern.search(command):
                return CommandGuardResult(
                    verdict=GuardVerdict.BLOCKED,
                    reason=f"命令匹配 Hard Block 规则: {pattern.pattern}",
                )

        return CommandGuardResult(verdict=GuardVerdict.ALLOWED)

    @staticmethod
    def check_batch(commands: list[str]) -> list[tuple[str, CommandGuardResult]]:
        return [(cmd, CommandGuard.check(cmd)) for cmd in commands]
