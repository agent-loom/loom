#!/usr/bin/env python3
"""DevFlow 真实端到端集成脚本。

使用真实的 Plane + GitLab 实例验证完整 DevFlow 流水线。
需要 .env 中配置好以下变量：
  PLANE_BASE_URL, PLANE_API_KEY, PLANE_WORKSPACE_SLUG, PLANE_PROJECT_ID
  GITLAB_BASE_URL, GITLAB_TOKEN, GITLAB_PROJECT_ID
  DEVFLOW_REPO_URL, DEVFLOW_RUNNER_ADAPTER
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

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


async def main() -> None:
    load_dotenv(override=True)

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
    default_branch = os.environ.get("GITLAB_DEFAULT_BRANCH", "master")
    runner_adapter = os.environ.get("DEVFLOW_RUNNER_ADAPTER", "mock")
    repo_url = os.environ["DEVFLOW_REPO_URL"]
    workspace_base = os.environ.get("DEVFLOW_WORKSPACE_BASE_DIR")
    cleanup_success = os.environ.get("DEVFLOW_CLEANUP_ON_SUCCESS", "false").lower() == "true"

    print("\n配置:")
    print(f"  Plane:  {plane_base} / {plane_slug} / {plane_project_id}")
    print(f"  GitLab: {gitlab_base} / project {gitlab_project_id} (default: {default_branch})")
    print(f"  Runner: {runner_adapter}")
    print(f"  Workspace cleanup on success: {cleanup_success}")

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
        adapter=create_adapter(runner_adapter),
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
        _check("MR IID 已创建", result.mr_iid is not None, f"mr_iid={result.mr_iid}")
        _check("MR URL 非空", bool(result.mr_url))
        print(f"  branch: {result.branch}")
        print(f"  MR:     {result.mr_url}")

        job = result.coding_job
        _check("coding_job 不为 None", job is not None)
        if job:
            _check("job 状态为 SUCCEEDED", job.state == JobState.SUCCEEDED, f"got {job.state}")
            if job.result:
                print(f"  job.result.status: {job.result.status}")
                print(f"  job.result.commit_sha: {job.result.commit_sha}")

    # 步骤 5：验证 GitLab 上分支和 MR 存在
    if result and result.mr_iid:
        print("\n--- 步骤 5：验证 GitLab MR ---")
        try:
            mr = await gitlab.get_merge_request(gitlab_project_id, result.mr_iid)
            _check("GitLab MR 存在", bool(mr.get("id") or mr.get("iid")))
            print(f"  MR title: {mr.get('title')}")
            print(f"  MR state: {mr.get('state')}")
        except Exception as e:
            _check("GitLab MR 可查询", False, str(e))

    # 步骤 6：验证 Plane 工作项有评论
    print("\n--- 步骤 6：验证 Plane 评论 ---")
    try:
        import httpx
        async with httpx.AsyncClient(headers={"X-API-Key": plane_key}, timeout=10) as c:
            r = await c.get(
                f"{plane_base}/api/v1/workspaces/{plane_slug}/projects/{plane_project_id}"
                f"/work-items/{work_item_id}/comments/"
            )
            comments = r.json()
            count = comments.get("total_count", 0) if isinstance(comments, dict) else len(comments)
            _check("Plane 工作项有评论（DevFlow 回写）", count > 0, f"comments={count}")
    except Exception as e:
        _check("Plane 评论可查询", False, str(e))

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
