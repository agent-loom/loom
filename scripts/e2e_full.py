#!/usr/bin/env python3
"""DevFlow 完整真实端到端测试。

阶段 A：Codex 适配器隔离验证（本地 git repo，无 Plane/GitLab）
阶段 B：DevFlow 全链路（真实 Plane + GitLab + Codex）

用法：
  DEVFLOW_CODEX_PROFILE=cliproxy uv run python scripts/e2e_full.py

前置条件：
  - codex CLI 已安装且在 PATH 中
  - .env 中配置好 Plane + GitLab 变量
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(override=True)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s  %(name)s  %(message)s")
logger = logging.getLogger("e2e_full")

# ---------------------------------------------------------------------------
# 计分
# ---------------------------------------------------------------------------

passed = 0
failed = 0
skipped = 0


def _check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        msg = f"  FAIL  {label}"
        if detail:
            msg += f"\n        {detail}"
        print(msg)


def _skip(label: str, reason: str) -> None:
    global skipped
    skipped += 1
    print(f"  SKIP  {label}  ({reason})")


def _section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


# ---------------------------------------------------------------------------
# Git 仓库工具
# ---------------------------------------------------------------------------

def _make_git_repo(base: Path, name: str) -> Path:
    repo = base / name
    repo.mkdir(parents=True, exist_ok=True)
    for cmd in [
        ["git", "init"],
        ["git", "config", "user.name", "DevFlow E2E"],
        ["git", "config", "user.email", "devflow-e2e@test.local"],
    ]:
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
    (repo / "app.py").write_text("def greet():\n    return 'Hello'\n")
    (repo / "utils.py").write_text("def add(a, b):\n    return a + b\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def _changed_files(repo: Path) -> list[str]:
    r = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=repo, capture_output=True, text=True,
    )
    return [f.strip() for f in r.stdout.strip().split("\n") if f.strip()]


# ---------------------------------------------------------------------------
# 阶段 A：Codex 适配器隔离测试
# ---------------------------------------------------------------------------

async def phase_a_codex_adapter() -> bool:
    """返回 False 表示 Codex 不可用，跳过后续阶段。"""
    print("\n\n" + "=" * 60)
    print("  阶段 A：Codex 适配器隔离验证")
    print("=" * 60)

    from agent_platform.devflow.runner.adapters.codex import CodexAdapter
    from agent_platform.devflow.runner.path_guard import PathGuard
    from agent_platform.devflow.task_pack import (
        DevelopmentTask, MergeRequestSpec, RepositoryTarget,
        RequirementSpec, TaskMetadata,
    )

    profile = os.environ.get("DEVFLOW_CODEX_PROFILE")
    timeout = int(os.environ.get("CODEX_TIMEOUT", "180"))

    def _make_task(task_id: str, title: str, *,
                   write_allowed: list[str] | None = None,
                   validation_commands: list[str] | None = None) -> DevelopmentTask:
        return DevelopmentTask(
            metadata=TaskMetadata(task_id=task_id, type="feature", title=title,
                                  source={"url": "http://e2e"}),
            requirement=RequirementSpec(background=title, user_scenarios=[],
                                        acceptance=[], non_goals=[]),
            implementation={"constraints": [], "required_outputs": []},
            validation={"commands": validation_commands or [], "required_reports": []},
            review={"checklist": []},
            scope={"write_allowed": write_allowed or ["*"], "write_denied": []},
            repository=RepositoryTarget(
                remote_url="mock", project_id="1", default_branch="main",
                work_branch="feat/e2e",
                merge_request=MergeRequestSpec(title="E2E", labels=[]),
            ),
        )

    # A-0: 健康检查
    _section("A-0  Codex CLI 健康检查")
    adapter = CodexAdapter(profile=profile)
    healthy = await adapter.health_check()
    _check("codex CLI 可用", healthy)
    if not healthy:
        _skip("阶段 A 其余场景", "codex CLI 不可用")
        return False

    bad = CodexAdapter(cli_path="/nonexistent/codex")
    _check("错误路径 health_check=False", not await bad.health_check())

    # A-1: 修改已有函数
    _section("A-1  Codex 修改已有函数")
    tmp = Path(tempfile.mkdtemp(prefix="e2e-a1-"))
    try:
        repo = _make_git_repo(tmp, "mod")
        task = _make_task(
            "a1",
            "Modify the greet function in app.py to return 'Hello, DevFlow!' instead of 'Hello'",
            write_allowed=["app.py"],
        )
        t0 = time.monotonic()
        result = await adapter.execute(
            workspace_dir=str(repo), task=task, timeout_seconds=timeout,
        )
        elapsed = time.monotonic() - t0
        _check(f"exit_code=0 ({elapsed:.1f}s)", result.exit_code == 0,
               f"exit={result.exit_code}\nstderr={result.stderr[:400]}")
        content = (repo / "app.py").read_text()
        _check("app.py 包含 'DevFlow'",
               "DevFlow" in content or "devflow" in content.lower(),
               f"content=\n{content}")
        guard = PathGuard(write_allowed=["app.py"], workspace_root=str(repo))
        _check("PathGuard 无越界", len(guard.check(_changed_files(repo))) == 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # A-2: 创建新文件
    _section("A-2  Codex 创建新文件")
    tmp = Path(tempfile.mkdtemp(prefix="e2e-a2-"))
    try:
        repo = _make_git_repo(tmp, "newfile")
        task = _make_task(
            "a2",
            "Create a new file math_utils.py with a function multiply(a, b) that returns a * b",
            write_allowed=["math_utils.py"],
        )
        result = await adapter.execute(
            workspace_dir=str(repo), task=task, timeout_seconds=timeout,
        )
        _check(f"exit_code=0", result.exit_code == 0,
               f"exit={result.exit_code}\nstderr={result.stderr[:400]}")
        f = repo / "math_utils.py"
        _check("math_utils.py 已创建", f.exists())
        if f.exists():
            _check("包含 multiply 函数", "multiply" in f.read_text().lower())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # A-3: 带验证命令的任务
    _section("A-3  Codex 执行后验证命令通过")
    tmp = Path(tempfile.mkdtemp(prefix="e2e-a3-"))
    try:
        repo = _make_git_repo(tmp, "validation")
        task = _make_task(
            "a3",
            "Modify the greet function in app.py to return 'Hello, Agent!'",
            write_allowed=["app.py"],
            validation_commands=[
                "python -c \"from app import greet; assert 'Agent' in greet(), f'got: {greet()}'\"",
            ],
        )
        result = await adapter.execute(
            workspace_dir=str(repo), task=task, timeout_seconds=timeout,
        )
        _check("带验证命令执行完成", result.exit_code == 0,
               f"exit={result.exit_code}\nstderr={result.stderr[:400]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return True


# ---------------------------------------------------------------------------
# 阶段 B：DevFlow 全链路（真实 Plane + GitLab + Codex）
# ---------------------------------------------------------------------------

async def phase_b_devflow_pipeline() -> None:
    print("\n\n" + "=" * 60)
    print("  阶段 B：DevFlow 全链路（真实 Plane + GitLab）")
    print("=" * 60)

    from agent_platform.devflow.orchestrator import DevFlowOrchestrator, DevFlowResult
    from agent_platform.devflow.runner.execution_log import InMemoryExecutionLogRepository
    from agent_platform.devflow.runner.factory import create_adapter
    from agent_platform.devflow.runner.models import JobState
    from agent_platform.devflow.runner.runner import CodingAgentRunner
    from agent_platform.devflow.runner.workspace import WorkspaceManager
    from agent_platform.integrations.gitlab.adapter import GitLabAdapter
    from agent_platform.integrations.plane.adapter import PlaneAdapter

    # 读取配置
    plane_base = os.environ["PLANE_BASE_URL"]
    plane_key = os.environ["PLANE_API_KEY"]
    plane_slug = os.environ["PLANE_WORKSPACE_SLUG"]
    plane_project_id = os.environ["PLANE_PROJECT_ID"]
    gitlab_base = os.environ["GITLAB_BASE_URL"]
    gitlab_token = os.environ["GITLAB_TOKEN"]
    gitlab_project_id = os.environ["GITLAB_PROJECT_ID"]
    default_branch = os.environ.get("DEVFLOW_DEFAULT_BRANCH", "master")
    runner_adapter_name = os.environ.get("DEVFLOW_RUNNER_ADAPTER", "mock")
    codex_profile = os.environ.get("DEVFLOW_CODEX_PROFILE")
    repo_url = os.environ["DEVFLOW_REPO_URL"]
    workspace_base = os.environ.get("DEVFLOW_WORKSPACE_BASE_DIR")
    cleanup_success = os.environ.get("DEVFLOW_CLEANUP_ON_SUCCESS", "false").lower() == "true"

    print(f"\n  Plane:   {plane_base} / {plane_slug} / {plane_project_id[:8]}...")
    print(f"  GitLab:  {gitlab_base} / project {gitlab_project_id}")
    print(f"  Runner:  {runner_adapter_name} (profile: {codex_profile or 'default'})")
    print(f"  Branch:  {default_branch}")

    plane = PlaneAdapter(base_url=plane_base, api_key=plane_key, workspace_slug=plane_slug)
    gitlab = GitLabAdapter(base_url=gitlab_base, token=gitlab_token)

    # B-0: Plane 连通性
    _section("B-0  Plane API 连通性")
    try:
        projects = await plane.list_projects()
        _check("Plane API 可达", True)
        results = projects.get("results", [])
        target = next((p for p in results if p["id"] == plane_project_id), None)
        _check(f"项目 {plane_project_id[:8]}... 存在", target is not None,
               f"available: {[p['id'][:8] for p in results]}")
        if target:
            print(f"  项目名: {target.get('name')}")
    except Exception as e:
        _check("Plane API 可达", False, str(e))

    # B-1: GitLab 连通性
    _section("B-1  GitLab API 连通性")
    try:
        async with httpx.AsyncClient(
            headers={"PRIVATE-TOKEN": gitlab_token}, timeout=10
        ) as c:
            r = await c.get(f"{gitlab_base}/api/v4/projects/{gitlab_project_id}")
            data = r.json()
        _check("GitLab API 可达", r.status_code == 200, f"status={r.status_code}")
        _check("项目存在", "name" in data, str(data.get("message", "")))
        print(f"  项目名: {data.get('name')} | 默认分支: {data.get('default_branch')}")
    except Exception as e:
        _check("GitLab API 可达", False, str(e))

    # B-2: 创建 Plane 工作项
    _section("B-2  创建 Plane 工作项")
    ts = int(time.time())
    work_item_name = f"DevFlow E2E 全链路测试 {ts}"
    requirement = os.environ.get(
        "DEVFLOW_TEST_REQUIREMENT",
        (
            "DevFlow E2E 全链路测试任务。请只做一个最小可验证的变更："
            "在 agents/echo/prompts/orchestrator.md 文件末尾追加一行"
            f" '# DevFlow E2E marker {ts}'，不要修改任何其他文件。"
        ),
    )
    work_item_id = None
    try:
        wi = await plane.create_work_item(
            plane_project_id,
            name=work_item_name,
            description=f"<p>{requirement}</p>",
        )
        work_item_id = wi.get("id")
        _check("工作项创建成功", bool(work_item_id), str(wi))
        print(f"  work_item_id: {work_item_id}")
    except Exception as e:
        _check("工作项创建成功", False, str(e))

    if not work_item_id:
        print("\n  ⚠  无法继续：工作项创建失败")
        await plane.close()
        await gitlab.close()
        return

    # B-3: 触发 DevFlow 流水线
    _section("B-3  触发 DevFlow 流水线")
    runner = CodingAgentRunner(
        adapter=create_adapter(runner_adapter_name, codex_profile=codex_profile),
        workspace_manager=WorkspaceManager(
            base_dir=workspace_base,
            cleanup_on_success=cleanup_success,
            cleanup_on_failure=False,
        ),
        gitlab=gitlab,
        plane=plane,
        gitlab_project_id=gitlab_project_id,
        repo_url=repo_url,
        log_repo=InMemoryExecutionLogRepository(),
    )
    orch = DevFlowOrchestrator(
        plane=plane,
        gitlab=gitlab,
        gitlab_project_id=gitlab_project_id,
        coding_runner=runner,
        default_branch=default_branch,
    )

    payload = {
        "data": {
            "id": work_item_id,
            "project": plane_project_id,
            "name": work_item_name,
            "description_stripped": requirement,
            "state_detail": {"name": "Ready for AI Dev"},
            "properties": {
                "agent_id": os.environ.get("DEVFLOW_TEST_AGENT_ID", "echo"),
                "task_type": os.environ.get("DEVFLOW_TEST_TASK_TYPE", "agent:change"),
            },
        }
    }

    result: DevFlowResult | None = None
    try:
        t0 = time.monotonic()
        result = await orch.handle_webhook_event("work_item.updated", payload)
        elapsed = time.monotonic() - t0
        _check(f"handle_webhook_event 返回结果 ({elapsed:.1f}s)", result is not None)
    except Exception as e:
        _check("handle_webhook_event 无异常", False, str(e))
        logger.exception("流水线异常")

    if result:
        _check("分支名格式正确", result.branch.startswith("feat/"),
               f"branch={result.branch}")
        _check("MR IID 已创建", result.mr_iid is not None,
               f"mr_iid={result.mr_iid}")
        _check("MR URL 非空", bool(result.mr_url))
        print(f"  branch: {result.branch}")
        print(f"  MR:     {result.mr_url}")

        job = result.coding_job
        _check("coding_job 不为 None", job is not None)
        if job:
            _check("job 状态为 SUCCEEDED", job.state == JobState.SUCCEEDED,
                   f"state={job.state}")
            if job.result:
                _check("commit_sha 非空", bool(job.result.commit_sha),
                       f"commit_sha={job.result.commit_sha}")
                print(f"  job.result.status: {job.result.status}")
                print(f"  commit_sha: {job.result.commit_sha}")

    # B-4: 验证 GitLab MR
    if result and result.mr_iid:
        _section("B-4  验证 GitLab MR")
        try:
            mr = await gitlab.get_merge_request(gitlab_project_id, result.mr_iid)
            _check("GitLab MR 存在", bool(mr.get("id") or mr.get("iid")))
            _check("MR 源分支正确", mr.get("source_branch") == result.branch,
                   f"source={mr.get('source_branch')} expected={result.branch}")
            _check("MR 目标分支正确", mr.get("target_branch") == default_branch,
                   f"target={mr.get('target_branch')}")
            print(f"  MR title:  {mr.get('title')}")
            print(f"  MR state:  {mr.get('state')}")
        except Exception as e:
            _check("GitLab MR 可查询", False, str(e))

    # B-5: 验证 Plane 评论回写
    _section("B-5  验证 Plane 评论回写")
    try:
        async with httpx.AsyncClient(
            headers={"X-API-Key": plane_key}, timeout=10
        ) as c:
            r = await c.get(
                f"{plane_base}/api/v1/workspaces/{plane_slug}/projects"
                f"/{plane_project_id}/work-items/{work_item_id}/comments/"
            )
            comments = r.json()
            count = (comments.get("total_count", 0)
                     if isinstance(comments, dict) else len(comments))
            _check("Plane 工作项有评论（DevFlow 回写）", count > 0,
                   f"comment_count={count}")
            if count > 0 and isinstance(comments, dict):
                first = (comments.get("results") or [{}])[0]
                print(f"  最新评论: {str(first.get('comment_html', ''))[:120]}")
    except Exception as e:
        _check("Plane 评论可查询", False, str(e))

    await plane.close()
    await gitlab.close()


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

async def main() -> None:
    print("=" * 60)
    print("  DevFlow 完整真实端到端测试")
    print(f"  Runner: {os.environ.get('DEVFLOW_RUNNER_ADAPTER', 'mock')}")
    print(f"  Profile: {os.environ.get('DEVFLOW_CODEX_PROFILE', '(default)')}")
    print("=" * 60)

    if not shutil.which("codex"):
        print("\n  ⚠  codex CLI 未安装，跳过阶段 A")
        codex_ok = False
    else:
        codex_ok = await phase_a_codex_adapter()

    missing = [v for v in [
        "PLANE_BASE_URL", "PLANE_API_KEY", "PLANE_WORKSPACE_SLUG",
        "PLANE_PROJECT_ID", "GITLAB_BASE_URL", "GITLAB_TOKEN",
        "GITLAB_PROJECT_ID", "DEVFLOW_REPO_URL",
    ] if not os.environ.get(v)]

    if missing:
        print(f"\n  ⚠  缺少环境变量，跳过阶段 B: {missing}")
    else:
        await phase_b_devflow_pipeline()

    print("\n" + "=" * 60)
    print(f"  结果: {passed} 通过, {failed} 失败, {skipped} 跳过  (共 {passed+failed+skipped} 项)")
    print("=" * 60)

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
