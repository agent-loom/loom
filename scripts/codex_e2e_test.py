#!/usr/bin/env python3
"""DevFlow Codex 真实端到端测试。

使用真实 Codex CLI 在本地 git 仓库中执行编码任务。
流程：创建本地 repo → CodexAdapter.execute() → 验证文件变更 → git commit。

前置条件：
  - codex CLI 已安装且在 PATH 中
  - OPENAI_API_KEY 环境变量已设置

用法：
  uv run python scripts/codex_e2e_test.py
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv

from agent_platform.devflow.runner.adapters.codex import CodexAdapter
from agent_platform.devflow.runner.path_guard import PathGuard
from agent_platform.devflow.task_pack import (
    DevelopmentTask,
    MergeRequestSpec,
    RepositoryTarget,
    RequirementSpec,
    TaskMetadata,
)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT_SECONDS = 120

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


def _create_git_repo(base_dir: Path, name: str) -> Path:
    """创建一个带初始 commit 的本地 git 仓库。"""
    repo_dir = base_dir / name
    repo_dir.mkdir(parents=True, exist_ok=True)

    # 初始化 git
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "DevFlow E2E"],
        cwd=repo_dir, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "devflow-e2e@test.local"],
        cwd=repo_dir, check=True, capture_output=True,
    )

    # 创建初始文件
    (repo_dir / "app.py").write_text("def hello():\n    return 'Hello, World!'\n")
    (repo_dir / "README.md").write_text("# Test Project\n")

    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_dir, check=True, capture_output=True,
    )
    return repo_dir


def _create_fake_codex_cli(base_dir: Path) -> Path:
    """创建一个确定性的 fake codex CLI，用于验证 timeout/cancel 分支。"""
    fake = base_dir / "codex-fake"
    fake.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then\n"
        "  echo 'codex-fake 0.0.0'\n"
        "  exit 0\n"
        "fi\n"
        "sleep 60\n"
    )
    fake.chmod(0o755)
    return fake


def _build_task(
    task_id: str,
    title: str,
    *,
    background: str = "",
    acceptance: list[str] | None = None,
    write_allowed: list[str] | None = None,
    write_denied: list[str] | None = None,
    validation_commands: list[str] | None = None,
    required_outputs: list[str] | None = None,
) -> DevelopmentTask:
    """构造一个 DevelopmentTask。"""
    return DevelopmentTask(
        metadata=TaskMetadata(
            task_id=task_id,
            type="feature",
            title=title,
            source={"url": "http://e2e-test"},
        ),
        requirement=RequirementSpec(
            background=background or title,
            user_scenarios=[],
            acceptance=acceptance or [],
            non_goals=[],
        ),
        implementation={
            "constraints": [],
            "required_outputs": required_outputs or [],
        },
        validation={
            "commands": validation_commands or [],
            "required_reports": [],
        },
        review={"checklist": []},
        scope={
            "write_allowed": write_allowed or ["*"],
            "write_denied": write_denied or [],
        },
        repository=RepositoryTarget(
            remote_url="mock",
            project_id="1",
            default_branch="main",
            work_branch="feat/e2e-test",
            merge_request=MergeRequestSpec(title="E2E Test MR", labels=[]),
        ),
    )


# ---------------------------------------------------------------------------
# 测试场景
# ---------------------------------------------------------------------------


async def test_codex_basic_modification(*, profile: str | None, timeout_seconds: int) -> None:
    """场景 1：Codex 修改 hello() 返回值。"""
    print("\n--- 场景 1：Codex 修改 hello() 返回值 ---")

    tmp_dir = Path(tempfile.mkdtemp(prefix="codex-e2e-"))
    try:
        repo_dir = _create_git_repo(tmp_dir, "basic-mod")

        task = _build_task(
            task_id="codex-e2e-001",
            title=(
                "Modify the hello function in app.py to return 'Hello, Agent!' "
                "instead of 'Hello, World!'"
            ),
            background="We are testing the Codex adapter. Change the hello function return value.",
            acceptance=["hello() must return 'Hello, Agent!'"],
            write_allowed=["app.py"],
            required_outputs=["app.py"],
            validation_commands=[
                "python -c \"from app import hello; assert hello() == 'Hello, Agent!'\""
            ],
        )

        adapter = CodexAdapter(profile=profile)
        _check("Codex CLI 可用", await adapter.health_check())

        start = time.monotonic()
        result = await adapter.execute(
            workspace_dir=str(repo_dir),
            task=task,
            timeout_seconds=timeout_seconds,
        )
        elapsed = time.monotonic() - start

        _check(f"执行完成 (exit_code={result.exit_code}, {elapsed:.1f}s)",
               result.exit_code == 0,
               f"exit_code={result.exit_code}, stderr={result.stderr[:200]}")

        # 检查文件是否被修改
        new_content = (repo_dir / "app.py").read_text()
        _check("app.py 内容已修改", "Hello, Agent!" in new_content or "Agent" in new_content,
               f"content: {new_content[:200]}")

        # PathGuard 检查
        changed_files = []
        proc = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=repo_dir, capture_output=True, text=True,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            changed_files = [f.strip() for f in proc.stdout.strip().split("\n") if f.strip()]

        guard = PathGuard(write_allowed=["app.py"], workspace_root=str(repo_dir))
        violations = guard.check(changed_files)
        _check("PathGuard 检查通过", len(violations) == 0,
               f"violations: {[v.reason for v in violations]}")

        print(f"  INFO  stdout 前 300 字符: {result.stdout[:300]}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def test_codex_new_file_creation(*, profile: str | None, timeout_seconds: int) -> None:
    """场景 2：Codex 创建新文件。"""
    print("\n--- 场景 2：Codex 创建新文件 ---")

    tmp_dir = Path(tempfile.mkdtemp(prefix="codex-e2e-"))
    try:
        repo_dir = _create_git_repo(tmp_dir, "new-file")

        task = _build_task(
            task_id="codex-e2e-002",
            title="Create a new file called utils.py with a function add(a, b) that returns a + b",
            background="We need a utility module with basic math functions.",
            acceptance=["utils.py exists", "add(2, 3) == 5"],
            write_allowed=["utils.py"],
            required_outputs=["utils.py"],
        )

        adapter = CodexAdapter(profile=profile)
        result = await adapter.execute(
            workspace_dir=str(repo_dir),
            task=task,
            timeout_seconds=timeout_seconds,
        )

        _check(f"执行完成 (exit_code={result.exit_code})",
               result.exit_code == 0,
               f"exit_code={result.exit_code}, stderr={result.stderr[:200]}")

        utils_file = repo_dir / "utils.py"
        _check("utils.py 已创建", utils_file.exists())

        if utils_file.exists():
            content = utils_file.read_text()
            _check("utils.py 包含 add 函数", "def add" in content or "add" in content,
                   f"content: {content[:200]}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def test_codex_path_violation() -> None:
    """场景 3：PathGuard 拦截越界修改。"""
    print("\n--- 场景 3：PathGuard 拦截越界修改 ---")

    tmp_dir = Path(tempfile.mkdtemp(prefix="codex-e2e-"))
    try:
        repo_dir = _create_git_repo(tmp_dir, "path-violation")

        # 任务要求修改 app.py，但 scope 只允许修改 utils.py
        task = _build_task(
            task_id="codex-e2e-003",
            title="Modify the hello function in app.py to return 'Hacked!'",
            background="Testing path violation detection.",
            write_allowed=["utils.py"],
            write_denied=["app.py"],
        )

        adapter = CodexAdapter()
        await adapter.execute(
            workspace_dir=str(repo_dir),
            task=task,
            timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        )

        # 不管 Codex 是否修改了 app.py，PathGuard 应该能检测到
        proc = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=repo_dir, capture_output=True, text=True,
        )
        changed_files = []
        if proc.returncode == 0 and proc.stdout.strip():
            changed_files = [f.strip() for f in proc.stdout.strip().split("\n") if f.strip()]

        guard = PathGuard(
            write_allowed=["utils.py"],
            write_denied=["app.py"],
            workspace_root=str(repo_dir),
        )
        violations = guard.check(changed_files)

        if "app.py" in changed_files:
            _check("PathGuard 检测到 app.py 越界修改",
                   any(v.path == "app.py" for v in violations),
                   f"violations: {[(v.path, v.reason) for v in violations]}")
        else:
            _check("Codex 未修改越界文件（遵守 scope）", True)

        # 确定性验证：即使真实 Codex 遵守 scope，PathGuard 自身也必须能拦截越界列表。
        synthetic_violations = guard.check(["app.py"])
        _check(
            "PathGuard 可确定性拦截 app.py",
            any(v.path == "app.py" for v in synthetic_violations),
            f"violations: {[(v.path, v.reason) for v in synthetic_violations]}",
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def test_codex_timeout() -> None:
    """场景 4：Codex 超时取消。

    这里使用 fake CLI，而不是依赖真实 Codex 对某个复杂 prompt 的耗时。
    这样可以稳定覆盖 CodexAdapter 的 timeout/cancel 分支。
    """
    print("\n--- 场景 4：Codex 超时取消 ---")

    tmp_dir = Path(tempfile.mkdtemp(prefix="codex-e2e-"))
    try:
        repo_dir = _create_git_repo(tmp_dir, "timeout")

        task = _build_task(
            task_id="codex-e2e-004",
            title="Implement a complex multi-file refactoring with tests",
            background="This is a large task that should time out.",
        )

        fake_codex = _create_fake_codex_cli(tmp_dir)
        adapter = CodexAdapter(cli_path=str(fake_codex))
        start = time.monotonic()
        result = await adapter.execute(
            workspace_dir=str(repo_dir),
            task=task,
            timeout_seconds=1,
        )
        elapsed = time.monotonic() - start

        _check(f"超时返回 exit_code=-1 (got {result.exit_code})",
               result.exit_code == -1,
               f"exit_code={result.exit_code}")
        _check("包含超时错误信息",
               result.error_message is not None and "timed out" in (result.error_message or ""),
               f"error: {result.error_message}")
        _check(f"耗时在合理范围 (got {elapsed:.1f}s)",
               elapsed < 30,
               f"elapsed={elapsed:.1f}s")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def test_codex_health_check() -> None:
    """场景 5：Codex 健康检查。"""
    print("\n--- 场景 5：Codex 健康检查 ---")

    adapter = CodexAdapter()
    healthy = await adapter.health_check()
    _check("health_check 返回 True", healthy)

    bad_adapter = CodexAdapter(cli_path="/nonexistent/codex")
    bad_healthy = await bad_adapter.health_check()
    _check("错误路径 health_check 返回 False", not bad_healthy)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CodexAdapter 本地真实 E2E 测试")
    parser.add_argument(
        "--required",
        action="store_true",
        help="缺少 codex CLI 时返回失败；默认返回 0 并跳过，方便本地非 Codex 环境。",
    )
    parser.add_argument(
        "--profile",
        default=os.getenv("DEVFLOW_CODEX_PROFILE"),
        help="Codex profile，默认读取 DEVFLOW_CODEX_PROFILE。",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.getenv("CODEX_E2E_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))),
        help="真实 Codex 场景超时时间，默认 120 秒。",
    )
    parser.add_argument(
        "--skip-timeout",
        action="store_true",
        help="跳过 timeout/cancel 分支测试。",
    )
    return parser.parse_args()


async def main() -> None:
    load_dotenv(override=True)
    args = _parse_args()

    print("=" * 60)
    print("DevFlow Codex 真实端到端测试")
    print("=" * 60)
    print(f"Codex profile: {args.profile or '(default)'}")
    print(f"Timeout: {args.timeout}s")

    # 前置检查
    if not shutil.which("codex"):
        print("SKIP: codex CLI 未安装，跳过真实端到端测试")
        sys.exit(1 if args.required else 0)

    if not (os.getenv("OPENAI_API_KEY") or os.getenv("CODEX_HOME") or args.profile):
        print(
            "WARN: 未检测到 OPENAI_API_KEY/CODEX_HOME/--profile；"
            "如果 Codex CLI 未登录，真实执行场景可能失败。"
        )

    await test_codex_health_check()
    await test_codex_basic_modification(profile=args.profile, timeout_seconds=args.timeout)
    await test_codex_new_file_creation(profile=args.profile, timeout_seconds=args.timeout)
    await test_codex_path_violation()
    if not args.skip_timeout:
        await test_codex_timeout()

    print("\n" + "=" * 60)
    print(f"结果: {passed} 通过, {failed} 失败, 共 {passed + failed} 项")
    print("=" * 60)

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
