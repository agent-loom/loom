"""命令级安全守卫：拦截在 AI 沙箱环境中执行的危险 Shell 指令。

设计定位：
  DevFlow 研发沙箱安全的重要守卫屏障 (Sandbox Command Guard)。
  为了防止 AI 代理意外或恶性地在开发机或容器中执行破坏性操作（如 rm -rf、dd、格式化、未授权 git 强推、读取 secrets 等），
  通过静态正则检测，阻断任意匹配 `_HARD_BLOCK_PATTERNS` 规则的命令。
  设计文档见 docs/04-devflow/devflow-runner-workspace-design.md。
"""

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
    """安全命令过滤器 (Command Guard)

    对 Agent 沙箱内拟运行的所有 Shell 命令行做前置拦截过滤。
    提供 check() 和 check_batch() 两种静态检验入口。
    一经 blocked，沙箱执行流程立即退出，防止恶意命令扩散到宿主环境。
    """

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
