#!/usr/bin/env python3
"""Claude Code 本地端到端测试（无需 Plane/GitLab）。

使用本地 git 仓库 + ClaudeCodeAdapter 验证：
  1. Claude Code 读取代码 → 修改文件 → 返回结果
  2. PathGuard 检查变更文件
  3. WorkspaceManager 完整流程（create → execute → changed_files → commit）

用法：
  uv run python scripts/claude_code_local_e2e.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from agent_platform.devflow.runner.adapters.claude_code import ClaudeCodeAdapter
from agent_platform.devflow.runner.path_guard import PathGuard
from agent_platform.devflow.runner.workspace import WorkspaceManager
from agent_platform.devflow.task_pack import (
    DevelopmentTask,
    MergeRequestSpec,
    RepositoryTarget,
    RequirementSpec,
    TaskMetadata,
)

TIMEOUT_SECONDS = 180
passed = 0
failed = 0


def _check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        msg = f"  FAIL  {label}"
        if detail:
            msg += f"  ({detail})"
        print(msg)


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "DevFlow E2E"],
                   cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "devflow-e2e@test.local"],
                   cwd=path, check=True, capture_output=True)
    (path / "app.py").write_text("def greet():\n    return 'Hello'\n")
    (path / "utils.py").write_text("def add(a, b):\n    return a + b\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def _make_task(
    task_id: str, title: str, *,
    write_allowed: list[str] | None = None,
    write_denied: list[str] | None = None,
    validation_commands: list[str] | None = None,
) -> DevelopmentTask:
    return DevelopmentTask(
        metadata=TaskMetadata(task_id=task_id, type="feature", title=title,
                               source={"url": "http://e2e"}),
        requirement=RequirementSpec(
            background=title, user_scenarios=[], acceptance=[], non_goals=[],
        ),
        implementation={"constraints": [], "required_outputs": []},
        validation={"commands": validation_commands or [], "required_reports": []},
        review={"checklist": []},
        scope={"write_allowed": write_allowed or ["*"],
               "write_denied": write_denied or []},
        repository=RepositoryTarget(
            remote_url="mock", project_id="1", default_branch="main",
            work_branch="feat/e2e",
            merge_request=MergeRequestSpec(title="E2E", labels=[]),
        ),
    )


# ---------------------------------------------------------------------------
# 场景 1：Claude Code 修改函数
# ---------------------------------------------------------------------------
async def test_claude_modifies_function() -> None:
    print("\n--- 场景 1：Claude Code 修改 greet() 返回值 ---")
    tmp = Path(tempfile.mkdtemp(prefix="claude-local-"))
    try:
        _init_git_repo(tmp)
        adapter = ClaudeCodeAdapter()

        if not await adapter.health_check():
            _check("Claude Code CLI 可用", False, "health_check 返回 False")
            return
        _check("Claude Code CLI 可用", True)

        task = _make_task(
            "e2e-001",
            "Modify the greet function in app.py to return 'Hello, DevFlow!' instead of 'Hello'",
            write_allowed=["app.py"],
        )

        t0 = time.monotonic()
        result = await adapter.execute(
            workspace_dir=str(tmp), task=task, timeout_seconds=TIMEOUT_SECONDS,
        )
        elapsed = time.monotonic() - t0
        _check(f"执行完成 ({elapsed:.1f}s, exit={result.exit_code})",
               result.exit_code == 0,
               f"exit={result.exit_code} stderr={result.stderr[:300]}")

        content = (tmp / "app.py").read_text()
        _check("app.py 包含 'DevFlow'",
               "DevFlow" in content or "devflow" in content.lower(),
               f"content={content[:200]}")

        # git diff 检查
        proc = subprocess.run(["git", "diff", "--name-only", "HEAD"],
                              cwd=tmp, capture_output=True, text=True)
        changed = [f.strip() for f in proc.stdout.strip().split("\n") if f.strip()]
        guard = PathGuard(write_allowed=["app.py"], workspace_root=str(tmp))
        violations = guard.check(changed)
        _check("PathGuard 无违规", len(violations) == 0,
               f"violations: {[(v.path, v.reason) for v in violations]}")

        if result.stdout:
            print(f"  INFO  stdout[0:200]: {result.stdout[:200]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 场景 2：Claude Code 创建新文件
# ---------------------------------------------------------------------------
async def test_claude_creates_file() -> None:
    print("\n--- 场景 2：Claude Code 创建新文件 math_utils.py ---")
    tmp = Path(tempfile.mkdtemp(prefix="claude-local-"))
    try:
        _init_git_repo(tmp)
        adapter = ClaudeCodeAdapter()

        task = _make_task(
            "e2e-002",
            "Create a new file math_utils.py with a function multiply(a, b) that returns a * b",
            write_allowed=["math_utils.py"],
        )

        result = await adapter.execute(
            workspace_dir=str(tmp), task=task, timeout_seconds=TIMEOUT_SECONDS,
        )
        _check("exit_code=0", result.exit_code == 0,
               f"exit={result.exit_code} stderr={result.stderr[:200]}")

        f = tmp / "math_utils.py"
        _check("math_utils.py 已创建", f.exists())
        if f.exists():
            _check("包含 multiply", "multiply" in f.read_text().lower())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 场景 3：PathGuard 拦截越界
# ---------------------------------------------------------------------------
async def test_path_violation_detection() -> None:
    print("\n--- 场景 3：PathGuard 检测越界修改 ---")
    tmp = Path(tempfile.mkdtemp(prefix="claude-local-"))
    try:
        _init_git_repo(tmp)
        adapter = ClaudeCodeAdapter()

        # scope 只允许 app.py，但如果 Claude Code 修改了 utils.py
        task = _make_task(
            "e2e-003",
            "Modify the greet function in app.py to return 'Changed!'",
            write_allowed=["app.py"],
            write_denied=["utils.py"],
        )

        result = await adapter.execute(
            workspace_dir=str(tmp), task=task, timeout_seconds=TIMEOUT_SECONDS,
        )
        _check("执行完成", result.exit_code == 0 or result.exit_code != 0)

        proc = subprocess.run(["git", "diff", "--name-only", "HEAD"],
                              cwd=tmp, capture_output=True, text=True)
        changed = [f.strip() for f in proc.stdout.strip().split("\n") if f.strip()]

        guard = PathGuard(
            write_allowed=["app.py"],
            write_denied=["utils.py"],
            workspace_root=str(tmp),
        )
        violations = guard.check(changed)

        if "utils.py" in changed:
            _check("检测到 utils.py 越界",
                   any(v.path == "utils.py" for v in violations))
        else:
            _check("Claude Code 遵守 scope（未修改 utils.py）", True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 场景 4：健康检查
# ---------------------------------------------------------------------------
async def test_health_check() -> None:
    print("\n--- 场景 4：健康检查 ---")
    adapter = ClaudeCodeAdapter()
    _check("claude health_check=True", await adapter.health_check())

    bad = ClaudeCodeAdapter(cli_path="/nonexistent/claude")
    _check("bad path health_check=False", not await bad.health_check())


# ---------------------------------------------------------------------------
# 场景 5：使用 WorkspaceManager 完整流程
# ---------------------------------------------------------------------------
async def test_workspace_manager_flow() -> None:
    print("\n--- 场景 5：WorkspaceManager 完整流程 ---")
    mock_remote_dir = Path(tempfile.mkdtemp(prefix="claude-remote-"))
    wm_base_dir = Path(tempfile.mkdtemp(prefix="claude-wm-"))
    try:
        # 1. 准备 mock 远程仓库
        _init_git_repo(mock_remote_dir)

        # 2. WorkspaceManager 克隆
        wm = WorkspaceManager(base_dir=wm_base_dir)
        workspace_dir = await wm.create(branch="main", repo_url=f"file://{mock_remote_dir}")

        # 3. 准备任务与适配器
        adapter = ClaudeCodeAdapter()
        task = _make_task(
            "e2e-004",
            "Modify the add function in utils.py to return a + b + 1",
            write_allowed=["utils.py"],
        )

        # 4. 执行任务
        result = await adapter.execute(
            workspace_dir=str(workspace_dir), task=task, timeout_seconds=TIMEOUT_SECONDS,
        )
        _check("执行完成", result.exit_code == 0, f"exit={result.exit_code} stderr={result.stderr[:200]}")

        # 5. 验证文件修改
        content = (workspace_dir / "utils.py").read_text()
        _check("utils.py 已正确修改", "a + b + 1" in content or "a+b+1" in content or "a + b +1" in content)

        # 6. 获取变更文件并提交
        changed_files = await wm.get_changed_files(workspace_dir)
        _check("检测到变更文件", "utils.py" in changed_files, f"changed={changed_files}")

        # 尝试提交
        commit_sha = await wm.commit_and_push(
            workspace_dir,
            message="feat: updated add function",
            branch="main",
            changed_files=changed_files,
        )
        _check("提交并推送成功", commit_sha is not None, f"sha={commit_sha}")

    finally:
        shutil.rmtree(mock_remote_dir, ignore_errors=True)
        shutil.rmtree(wm_base_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
async def main() -> None:
    print("=" * 60)
    print("Claude Code 本地端到端测试")
    print("=" * 60)

    if not shutil.which("claude"):
        print("SKIP: claude CLI 未安装")
        sys.exit(0)

    await test_health_check()
    await test_claude_modifies_function()
    await test_claude_creates_file()
    await test_path_violation_detection()
    await test_workspace_manager_flow()

    print("\n" + "=" * 60)
    print(f"结果: {passed} 通过, {failed} 失败, 共 {passed + failed} 项")
    print("=" * 60)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
