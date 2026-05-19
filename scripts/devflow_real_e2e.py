#!/usr/bin/env python3
"""DevFlow 真实端到端集成脚本。

使用真实的 Plane + GitLab 实例验证完整 DevFlow 流水线。
需要 .env 中配置好以下变量：
  PLANE_BASE_URL, PLANE_API_KEY, PLANE_WORKSPACE_SLUG, PLANE_PROJECT_ID
  GITLAB_BASE_URL, GITLAB_TOKEN, GITLAB_PROJECT_ID
  DEVFLOW_REPO_URL, DEVFLOW_RUNNER_ADAPTER
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Any

from dotenv import load_dotenv

from agent_platform.devflow.orchestrator import DevFlowOrchestrator, DevFlowResult
from agent_platform.devflow.runner.execution_log import InMemoryExecutionLogRepository
from agent_platform.devflow.runner.factory import create_adapter
from agent_platform.devflow.runner.models import JobState
from agent_platform.devflow.runner.runner import CodingAgentRunner
from agent_platform.devflow.runner.workspace import WorkspaceManager
from agent_platform.integrations.gitlab.adapter import GitLabAdapter
from agent_platform.integrations.plane.adapter import PlaneAdapter

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
logger = logging.getLogger("devflow_real_e2e")

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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="DevFlow 直连真实 Plane/GitLab/Runner 的 E2E 集成验证"
    )
    parser.add_argument(
        "--require-real-runner",
        action="store_true",
        help="要求 DEVFLOW_RUNNER_ADAPTER 不是 mock。",
    )
    parser.add_argument(
        "--require-commit",
        action="store_true",
        help="要求 Runner 成功后产生 commit_sha。",
    )
    parser.add_argument(
        "--require-state-sync",
        action="store_true",
        help="要求配置 Plane AI Developing / Testing 状态 ID，并验证状态推进。",
    )
    return parser.parse_args()


async def _get_gitlab_mr_notes(
    *,
    base_url: str,
    token: str,
    project_id: str,
    mr_iid: int,
) -> list[dict[str, Any]]:
    import httpx

    async with httpx.AsyncClient(headers={"PRIVATE-TOKEN": token}, timeout=10) as client:
        response = await client.get(
            f"{base_url}/api/v4/projects/{project_id}/merge_requests/{mr_iid}/notes"
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []


async def _get_plane_comments(
    *,
    base_url: str,
    api_key: str,
    workspace_slug: str,
    project_id: str,
    work_item_id: str,
) -> list[dict[str, Any]]:
    import httpx

    async with httpx.AsyncClient(headers={"X-API-Key": api_key}, timeout=10) as client:
        response = await client.get(
            f"{base_url}/api/v1/workspaces/{workspace_slug}/projects/{project_id}"
            f"/work-items/{work_item_id}/comments/"
        )
        response.raise_for_status()
        data = response.json()
    if isinstance(data, dict):
        results = data.get("results")
        if isinstance(results, list):
            return results
        comments = data.get("comments")
        if isinstance(comments, list):
            return comments
        return []
    return data if isinstance(data, list) else []


def _comment_text(comment: dict[str, Any]) -> str:
    for key in ("body", "comment_html", "comment_stripped", "comment"):
        value = comment.get(key)
        if isinstance(value, str):
            return value
    return str(comment)


async def main() -> None:
    load_dotenv(override=True)
    args = _parse_args()

    print("=" * 60)
    print("DevFlow 真实 E2E 集成验证")
    print("=" * 60)

    # 读取环境变量
    plane_base = os.environ["PLANE_BASE_URL"]
    plane_key = os.environ["PLANE_API_KEY"]
    plane_slug = os.environ["PLANE_WORKSPACE_SLUG"]
    plane_project_id = os.environ["PLANE_PROJECT_ID"]
    gitlab_base = os.environ["GITLAB_BASE_URL"]
    gitlab_token = os.environ["GITLAB_TOKEN"]
    gitlab_project_id = os.environ["GITLAB_PROJECT_ID"]
    default_branch = (
        os.environ.get("DEVFLOW_DEFAULT_BRANCH")
        or os.environ.get("GITLAB_DEFAULT_BRANCH")
        or "master"
    )
    runner_adapter = os.environ.get("DEVFLOW_RUNNER_ADAPTER", "mock")
    codex_profile = os.environ.get("DEVFLOW_CODEX_PROFILE")
    repo_url = os.environ["DEVFLOW_REPO_URL"]
    workspace_base = os.environ.get("DEVFLOW_WORKSPACE_BASE_DIR")
    cleanup_success = os.environ.get("DEVFLOW_CLEANUP_ON_SUCCESS", "false").lower() == "true"
    ai_developing_state_id = os.environ.get("PLANE_AI_DEVELOPING_STATE_ID")
    testing_state_id = os.environ.get("PLANE_TESTING_STATE_ID")

    print("\n配置:")
    print(f"  Plane:  {plane_base} / {plane_slug} / {plane_project_id}")
    print(f"  GitLab: {gitlab_base} / project {gitlab_project_id} (default: {default_branch})")
    print(f"  Runner: {runner_adapter} (profile: {codex_profile or 'default'})")
    print(f"  Workspace cleanup on success: {cleanup_success}")
    print(
        "  Plane states: "
        f"AI Developing={bool(ai_developing_state_id)}, "
        f"Testing={bool(testing_state_id)}"
    )

    if args.require_real_runner and runner_adapter == "mock":
        _check("真实 Runner 已启用", False, "DEVFLOW_RUNNER_ADAPTER=mock")
        sys.exit(1)
    if args.require_state_sync:
        _check("PLANE_AI_DEVELOPING_STATE_ID 已配置", bool(ai_developing_state_id))
        _check("PLANE_TESTING_STATE_ID 已配置", bool(testing_state_id))
        if not (ai_developing_state_id and testing_state_id):
            sys.exit(1)

    # 构建真实适配器
    plane = PlaneAdapter(
        base_url=plane_base,
        api_key=plane_key,
        workspace_slug=plane_slug,
    )
    gitlab = GitLabAdapter(
        base_url=gitlab_base,
        token=gitlab_token,
    )

    # 步骤 1：验证 Plane 连接
    print("\n--- 步骤 1：Plane 连接 ---")
    try:
        projects = await plane.list_projects()
        _check("Plane API 可达", True)
        results = projects.get("results", [])
        target = next((p for p in results if p["id"] == plane_project_id), None)
        _check(f"项目 {plane_project_id[:8]}... 存在", target is not None)
        if target:
            print(f"  项目名: {target.get('name')}")
    except Exception as e:
        _check("Plane API 可达", False, str(e))

    # 步骤 2：验证 GitLab 连接
    print("\n--- 步骤 2：GitLab 连接 ---")
    try:
        import httpx
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

    # 步骤 3：在 Plane 创建测试工作项
    print("\n--- 步骤 3：创建 Plane 工作项 ---")
    import time
    ts = int(time.time())
    work_item_name = f"DevFlow 真实 E2E 测试 {ts}"
    test_requirement = os.environ.get(
        "DEVFLOW_TEST_REQUIREMENT",
        (
            "DevFlow 真实 E2E 测试。请只做一个最小、可验证的 Echo Agent 变更："
            "在 agents/echo/prompts/orchestrator.md 末尾追加一行 "
            "'DevFlow Codex E2E marker'，并同步补充 echo 的 eval/test，"
            "不要修改无关模块。测试完可删除。"
        ),
    )
    work_item_id = None
    try:
        wi = await plane.create_work_item(
            plane_project_id,
            name=work_item_name,
            description=f"<p>{test_requirement}</p>",
        )
        work_item_id = wi.get("id")
        _check("工作项创建成功", bool(work_item_id), str(wi))
        print(f"  work_item_id: {work_item_id}")
    except Exception as e:
        _check("工作项创建成功", False, str(e))

    if not work_item_id:
        print("\n无法继续：工作项创建失败")
        sys.exit(1)

    # 步骤 4：触发 DevFlow 流水线（模拟 Plane Webhook）
    print("\n--- 步骤 4：触发 DevFlow 流水线 ---")
    runner = CodingAgentRunner(
        adapter=create_adapter(runner_adapter, codex_profile=codex_profile),
        workspace_manager=WorkspaceManager(
            base_dir=workspace_base,
            cleanup_on_success=cleanup_success,
            cleanup_on_failure=False,
        ),
        gitlab=gitlab,
        plane=plane,
        gitlab_project_id=gitlab_project_id,
        repo_url=repo_url,
        testing_state_id=testing_state_id,
        ai_developing_state_id=ai_developing_state_id,
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
            "description_stripped": test_requirement,
            "state_detail": {"name": "Ready for AI Dev"},
            "properties": {
                "agent_id": os.environ.get("DEVFLOW_TEST_AGENT_ID", "echo"),
                "task_type": os.environ.get("DEVFLOW_TEST_TASK_TYPE", "agent:change"),
            },
        }
    }

    result: DevFlowResult | None = None
    try:
        result = await orch.handle_webhook_event("work_item.updated", payload)
        _check("handle_webhook_event 返回结果", result is not None)
    except Exception as e:
        _check("handle_webhook_event 无异常", False, str(e))
        logger.exception("流水线异常")

    if result:
        _check("分支名正确格式", result.branch.startswith("feat/"))
        _check("Orchestrator 不创建 MR（mr_iid 为 None）", result.mr_iid is None)
        print(f"  branch: {result.branch}")

        job = result.coding_job
        _check("coding_job 不为 None", job is not None)
        if job:
            # Runner 完成后 MR 由 Runner 创建
            if job.mr_iid:
                print(f"  MR (by Runner): !{job.mr_iid} → {job.mr_url}")
            _check("job 状态为 SUCCEEDED", job.state == JobState.SUCCEEDED, f"got {job.state}")
            if job.result:
                print(f"  job.result.status: {job.result.status}")
                print(f"  job.result.commit_sha: {job.result.commit_sha}")
                _check(
                    "job.result.status 为 success",
                    getattr(job.result.status, "value", job.result.status) == "success",
                    f"got {job.result.status}",
                )
                if args.require_commit or runner_adapter != "mock":
                    _check("Runner commit_sha 非空", bool(job.result.commit_sha))

    # 步骤 5：验证 GitLab 上分支和 MR 存在（MR 由 Runner 创建）
    job_has_mr = result and result.coding_job and result.coding_job.mr_iid
    if job_has_mr:
        print("\n--- 步骤 5：验证 GitLab MR ---")
        try:
            mr = await gitlab.get_merge_request(gitlab_project_id, result.coding_job.mr_iid)
            _check("GitLab MR 存在", bool(mr.get("id") or mr.get("iid")))
            _check("MR source branch 匹配", mr.get("source_branch") == result.branch)
            _check("MR target branch 匹配", mr.get("target_branch") == default_branch)
            print(f"  MR title: {mr.get('title')}")
            print(f"  MR state: {mr.get('state')}")
            has_runner_commit = (
                result.coding_job
                and result.coding_job.result
                and result.coding_job.result.commit_sha
            )
            if has_runner_commit:
                _check(
                    "MR head sha 匹配 Runner commit",
                    mr.get("sha") == result.coding_job.result.commit_sha,
                    f"mr.sha={mr.get('sha')}, commit={result.coding_job.result.commit_sha}",
                )

            notes = await _get_gitlab_mr_notes(
                base_url=gitlab_base,
                token=gitlab_token,
                project_id=gitlab_project_id,
                mr_iid=result.coding_job.mr_iid,
            )
            note_bodies = "\n".join(_comment_text(note) for note in notes)
            _check("GitLab MR 有 Runner 报告评论", "DevFlow Runner" in note_bodies)
        except Exception as e:
            _check("GitLab MR 可查询", False, str(e))

    # 步骤 6：验证 Plane 工作项有评论
    print("\n--- 步骤 6：验证 Plane 评论 ---")
    try:
        comments = await _get_plane_comments(
            base_url=plane_base,
            api_key=plane_key,
            workspace_slug=plane_slug,
            project_id=plane_project_id,
            work_item_id=work_item_id,
        )
        comment_text = "\n".join(_comment_text(comment) for comment in comments)
        _check("Plane 工作项有评论（DevFlow 回写）", len(comments) > 0, f"comments={len(comments)}")
        _check("Plane 评论包含分支创建通知", "分支已创建" in comment_text or "MR" in comment_text)
        _check("Plane 评论包含 Runner 报告", "DevFlow Runner" in comment_text)
    except Exception as e:
        _check("Plane 评论可查询", False, str(e))

    if args.require_state_sync and work_item_id:
        print("\n--- 步骤 7：验证 Plane 状态 ---")
        try:
            detail = await plane.get_work_item(plane_project_id, work_item_id)
            state = detail.get("state")
            state_id = state.get("id") if isinstance(state, dict) else state
            _check(
                "Plane 状态已推进到 Testing",
                state_id == testing_state_id,
                f"state={state}",
            )
        except Exception as e:
            _check("Plane 状态可查询", False, str(e))

    # 关闭连接
    await plane.close()
    await gitlab.close()

    print("\n" + "=" * 60)
    print(f"结果: {passed} 通过, {failed} 失败, 共 {passed + failed} 项")
    print("=" * 60)

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
