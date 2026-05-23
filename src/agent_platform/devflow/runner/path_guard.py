"""文件路径安全守卫：限制与审计沙箱环境的文件变更范围。

设计定位：
  研发沙箱安全与权限管控层 (Sandbox Path Guard)。
  对应 docs/04-devflow/devflow-runner-workspace-design.md 中的"路径守卫"设计。
  当 AI 沙箱环境运行结束并上报变更文件列表后，本模块通过白名单 (write_allowed) 和黑名单 (write_denied)
  规则静态审计变更路径的合法性，防止 AI 恶意修改系统敏感区域、利用相对路径越权逃逸或建立不安全符号链接。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import PurePosixPath

from agent_platform.devflow.task_pack import DevelopmentTask


def _glob_match(path: str, pattern: str) -> bool:
    """匹配任务包路径 Glob。

    DevFlow 作用域使用相对于仓库的 Glob 模式，如 ``agents/**`` 和 ``src/agent_platform/**``。
    ``PurePosixPath.match`` 对这类模式的匹配表现更像后缀匹配，并不支持任务作者所预期的任意深度后代匹配，
    因此我们对规范化后的 POSIX 路径采用 Shell 风格的匹配。
    """
    return fnmatchcase(path, pattern)


@dataclass(frozen=True)
class PathViolation:
    """路径违规记录，包含路径和违规原因。

    # TODO Design Gap:
    # 路径违规记录目前缺少严重性标记 (Severity)，这导致外部模块在接收到违规列表时无法做精细化控制
    # (例如将某些微不足道的路径变更降级为警告，而将高危路径变更作为致命阻断)。
    """
    path: str
    reason: str


@dataclass
class PathGuard:
    """基于白名单/黑名单模式的文件路径守卫 (Path Guard)

    对 Agent 沙箱内拟提交或修改的所有文件路径进行静态一致性边界判定。
    优先级规则：
      1. 路径穿越检查 (阻止 '..')
      2. 符号链接跨目录逃逸检查 (Symlink Escape)
      3. 显式拒绝规则匹配 (write_denied)
      4. 显式允许规则匹配 (write_allowed)
      5. 默认兜底：拒绝一切未匹配允许规则的文件修改
    """
    write_allowed: list[str] = field(default_factory=list)
    write_denied: list[str] = field(default_factory=list)
    workspace_root: str | None = None

    @classmethod
    def from_task(cls, task: DevelopmentTask, *, workspace_root: str | None = None) -> PathGuard:
        return cls(
            write_allowed=task.scope.get("write_allowed", []),
            write_denied=task.scope.get("write_denied", []),
            workspace_root=workspace_root,
        )

    def check(self, changed_files: list[str]) -> list[PathViolation]:
        """批量检查变更文件，返回违规列表。"""
        # TODO Design Gap:
        # changed_files 列表纯粹由外部调用方 (Runner/Sandbox) 进行传入，本守卫属于被动检查。
        # 如果底层 Runner 适配器在数据提取阶段发生逻辑错误或被 Agent 恶意伪造从而少报/瞒报了变更文件，
        # PathGuard 可能会完全漏检，未来需要增加主动的文件差异检测机制以保证 changed_files 列表的完整性与真实性。
        violations: list[PathViolation] = []
        for file_path in changed_files:
            violation = self._check_single(file_path)
            if violation:
                violations.append(violation)
        return violations

    def _check_single(self, file_path: str) -> PathViolation | None:
        normalized = PurePosixPath(file_path).as_posix()
        if ".." in normalized.split("/"):
            return PathViolation(
                path=file_path,
                reason="path traversal detected",
            )

        # Symlink 防御：如果配置了 workspace_root，验证 realpath 仍在 workspace 内
        # TODO Design Gap:
        # workspace_root 当前是可选的 (Optional)。如果外部未传入 workspace_root，符号链接逃逸检查
        # 就会被默默跳过且完全不记录任何安全日志或警告，这在高安全级沙箱容器中是一个潜在的绕过漏洞。
        if self.workspace_root:
            real_root = os.path.realpath(self.workspace_root)
            full_path = os.path.realpath(os.path.join(self.workspace_root, normalized))
            if not full_path.startswith(real_root + os.sep) and full_path != real_root:
                return PathViolation(
                    path=file_path,
                    reason="symlink escape: resolves outside workspace root",
                )

        # TODO Design Gap:
        # 目前 _glob_match 使用 fnmatchcase 来实现，其通配符语义与标准 .gitignore 或双星号 (**) 机制
        # 存在细微偏差 (尤其是在跨多级子目录时)，容易造成任务配置作者的理解误区，未来建议切换至 pathlib.Path.match 或专用的 pathspec 库。
        for pattern in self.write_denied:
            if _glob_match(normalized, pattern):
                return PathViolation(
                    path=file_path,
                    reason=f"matches write_denied pattern: {pattern}",
                )

        for pattern in self.write_allowed:
            if _glob_match(normalized, pattern):
                return None

        return PathViolation(
            path=file_path,
            reason="not in any write_allowed pattern",
        )

    def is_allowed(self, file_path: str) -> bool:
        """判断单个文件路径是否被允许修改。"""
        return self._check_single(file_path) is None
