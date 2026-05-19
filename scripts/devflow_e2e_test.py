#!/usr/bin/env python3
"""DevFlow 端到端集成验证脚本。

使用 MockTransport + MockRunnerAdapter + StubWorkspaceManager 验证完整
DevFlow 流水线：Webhook → Orchestrator → TaskPack → Branch → Runner → Commit → MR → Job。
无需任何外部基础设施（Plane / GitLab）。
"""

from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
from pathlib import Path

import httpx

from agent_platform.devflow.orchestrator import DevFlowOrchestrator, DevFlowResult
from agent_platform.devflow.runner.adapters.mock import MockRunnerAdapter
from agent_platform.devflow.runner.execution_log import InMemoryExecutionLogRepository
from agent_platform.devflow.runner.models import JobState, ResultStatus, ValidationResult
from agent_platform.devflow.runner.runner import CodingAgentRunner
from agent_platform.devflow.runner.workspace import WorkspaceManager
from agent_platform.integrations.gitlab.adapter import GitLabAdapter
from agent_platform.integrations.plane.adapter import PlaneAdapter

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
logger = logging.getLogger("devflow_e2e")

WORK_ITEM_ID = "wi-e2e-001"
PLANE_PROJECT_ID = "proj-e2e"
GITLAB_PROJECT_ID = "gl-proj-1"
MR_IID = 99


# ---------------------------------------------------------------------------
# Mock HTTP Transports
# ---------------------------------------------------------------------------

def _plane_transport() -> httpx.MockTransport:
    detail = {
        "id": WORK_ITEM_ID,
        "project": PLANE_PROJECT_ID,
        "name": "E2E 测试任务：实现用户注册",
        "description_stripped": "实现基本的用户注册功能，包括邮箱验证。",
        "state_detail": {"name": "Ready for AI Dev"},
        "properties": {"agent_id": "test-agent", "task_type": "platform:feature"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/work-items/" in path and request.method == "GET":
            return httpx.Response(200, json=detail)
        if "/comments/" in path and request.method == "POST":
            return httpx.Response(200, json={"id": "comment-1"})
        if "/work-items/" in path and request.method in ("PATCH", "PUT"):
            return httpx.Response(200, json={})
        if "/states/" in path:
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={})

    return httpx.MockTransport(handler)


def _gitlab_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "merge_requests" in path and "notes" in path and request.method == "POST":
            return httpx.Response(200, json={"id": "note-1"})
        if "merge_requests" in path and request.method == "POST":
            return httpx.Response(200, json={
                "iid": MR_IID,
                "web_url": f"https://gitlab.mock/project/mr/{MR_IID}",
                "source_branch": "feat/wi-e2e-001",
                "target_branch": "main",
            })
        if "branches" in path:
            return httpx.Response(200, json={"name": "feat/wi-e2e-001"})
        if "statuses" in path:
            return httpx.Response(200, json={})
        return httpx.Response(200, json={})

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# Stub WorkspaceManager — 不执行真实 git 操作
# ---------------------------------------------------------------------------

class StubWorkspaceManager(WorkspaceManager):
    """覆写所有 I/O 方法，在临时目录中运行，不执行真实 git 操作。"""

    def __init__(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="devflow-e2e-"))
        super().__init__(base_dir=self._tmp, cleanup_on_success=True)

    async def create(self, *, branch: str, repo_url: str) -> Path:
        ws = self._tmp / f"ws-{branch}"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / ".git").mkdir(exist_ok=True)
        return ws

    async def get_changed_files(self, workspace_dir: Path) -> list[str]:
        return ["src/agent_platform/new_feature.py", "docs/feature_spec.md"]

    async def run_validation(
        self, workspace_dir: Path, commands: list[str],
    ) -> ValidationResult:
        return ValidationResult(commands_executed=[], all_passed=True)

    async def commit_and_push(
        self, workspace_dir: Path, *, message: str, branch: str, changed_files: list[str],
    ) -> str | None:
        return "e2e0000fake0sha"

    async def cleanup(self, workspace_dir: Path, *, keep_on_failure: bool = False) -> None:
        pass


# ---------------------------------------------------------------------------
# 测试场景
# ---------------------------------------------------------------------------

def _webhook_payload(
    state: str = "Ready for AI Dev",
    work_item_id: str = WORK_ITEM_ID,
) -> dict:
    return {
        "data": {
            "id": work_item_id,
            "project": PLANE_PROJECT_ID,
            "name": "E2E 测试任务：实现用户注册",
            "state_detail": {"name": state},
        },
    }


async def _build_stack() -> tuple[DevFlowOrchestrator, CodingAgentRunner, PlaneAdapter, GitLabAdapter]:
    plane = PlaneAdapter(
        base_url="https://plane.mock",
        api_key="mock-key",
        workspace_slug="mock-ws",
        transport=_plane_transport(),
    )
    gitlab = GitLabAdapter(
        base_url="https://gitlab.mock",
        token="mock-token",
        transport=_gitlab_transport(),
    )
    mock_adapter = MockRunnerAdapter(should_fail=False)
    ws_manager = StubWorkspaceManager()
    log_repo = InMemoryExecutionLogRepository()

    runner = CodingAgentRunner(
        adapter=mock_adapter,
        workspace_manager=ws_manager,
        gitlab=gitlab,
        plane=plane,
        gitlab_project_id=GITLAB_PROJECT_ID,
        repo_url="https://mock.repo/test.git",
        log_repo=log_repo,
    )

    orch = DevFlowOrchestrator(
        plane=plane,
        gitlab=gitlab,
        gitlab_project_id=GITLAB_PROJECT_ID,
        coding_runner=runner,
    )
    return orch, runner, plane, gitlab


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


async def test_happy_path() -> None:
    """场景 1：正常触发 → 全流程成功。"""
    print("\n--- 场景 1：Happy Path ---")
    orch, *_ = await _build_stack()

    result = await orch.handle_webhook_event("work_item.updated", _webhook_payload())

    _check("返回 DevFlowResult", isinstance(result, DevFlowResult))
    _check("分支名正确", result.branch == "feat/wi-e2e-001", f"got {result.branch}")
    _check("Orchestrator 不创建 MR（mr_iid 为 None）", result.mr_iid is None)
    _check("Orchestrator 不创建 MR（mr_url 为 None）", result.mr_url is None)
    _check("task_pack.metadata.task_id 正确", result.task_pack.metadata.task_id == WORK_ITEM_ID)
    _check("task_pack.metadata.title 正确", "用户注册" in result.task_pack.metadata.title)

    job = result.coding_job
    _check("coding_job 不为 None", job is not None)
    if job:
        _check("job 状态为 SUCCEEDED", job.state == JobState.SUCCEEDED, f"got {job.state}")
        _check("job.result 不为 None", job.result is not None)
        if job.result:
            _check("result.status 为 SUCCESS", job.result.status == ResultStatus.SUCCESS)
            _check("result.commit_sha 不为空", bool(job.result.commit_sha))
        _check("至少有 1 次 invocation", len(job.invocations) >= 1)


async def test_idempotency() -> None:
    """场景 2：重复发送同一事件 → 第二次被幂等去重。"""
    print("\n--- 场景 2：幂等去重 ---")
    orch, *_ = await _build_stack()

    result1 = await orch.handle_webhook_event("work_item.updated", _webhook_payload())
    _check("第一次触发返回结果", result1 is not None)

    result2 = await orch.handle_webhook_event("work_item.updated", _webhook_payload())
    _check("重复事件返回 None（被去重）", result2 is None)


async def test_state_filter() -> None:
    """场景 3：非触发状态 → 被过滤忽略。"""
    print("\n--- 场景 3：状态过滤 ---")
    orch, *_ = await _build_stack()

    result = await orch.handle_webhook_event(
        "work_item.updated", _webhook_payload(state="In Progress"),
    )
    _check("非触发状态返回 None", result is None)


async def test_event_type_filter() -> None:
    """场景 4：不支持的事件类型 → 被过滤忽略。"""
    print("\n--- 场景 4：事件类型过滤 ---")
    orch, *_ = await _build_stack()

    result = await orch.handle_webhook_event("project.updated", _webhook_payload())
    _check("不支持的事件类型返回 None", result is None)


async def test_runner_failure() -> None:
    """场景 5：Runner 执行失败 → Job 状态为 FAILED。"""
    print("\n--- 场景 5：Runner 失败 ---")
    plane = PlaneAdapter(
        base_url="https://plane.mock", api_key="k",
        workspace_slug="ws", transport=_plane_transport(),
    )
    gitlab = GitLabAdapter(
        base_url="https://gitlab.mock", token="t",
        transport=_gitlab_transport(),
    )
    fail_adapter = MockRunnerAdapter(should_fail=True)
    runner = CodingAgentRunner(
        adapter=fail_adapter,
        workspace_manager=StubWorkspaceManager(),
        gitlab=gitlab,
        plane=plane,
        gitlab_project_id=GITLAB_PROJECT_ID,
        repo_url="https://mock.repo/test.git",
    )
    orch = DevFlowOrchestrator(
        plane=plane, gitlab=gitlab,
        gitlab_project_id=GITLAB_PROJECT_ID,
        coding_runner=runner,
    )

    result = await orch.handle_webhook_event("work_item.updated", _webhook_payload())
    _check("失败场景仍返回 DevFlowResult", isinstance(result, DevFlowResult))
    job = result.coding_job
    _check("coding_job 不为 None", job is not None)
    if job:
        _check("job 状态为 FAILED", job.state == JobState.FAILED, f"got {job.state}")
        if job.result:
            _check("result.status 为 RUNNER_ERROR", job.result.status == ResultStatus.RUNNER_ERROR)


async def test_different_work_items() -> None:
    """场景 6：不同工作项 → 均可独立触发（非幂等）。"""
    print("\n--- 场景 6：不同工作项独立触发 ---")
    orch, *_ = await _build_stack()

    r1 = await orch.handle_webhook_event("work_item.updated", _webhook_payload(work_item_id="wi-001"))
    r2 = await orch.handle_webhook_event("work_item.updated", _webhook_payload(work_item_id="wi-002"))
    _check("第一个工作项返回结果", r1 is not None)
    _check("第二个工作项也返回结果", r2 is not None)
    if r1 and r2:
        _check("两个工作项分支不同", r1.branch != r2.branch)


async def main() -> None:
    print("=" * 60)
    print("DevFlow 端到端集成验证")
    print("=" * 60)

    await test_happy_path()
    await test_idempotency()
    await test_state_filter()
    await test_event_type_filter()
    await test_runner_failure()
    await test_different_work_items()

    print("\n" + "=" * 60)
    print(f"结果: {passed} 通过, {failed} 失败, 共 {passed + failed} 项")
    print("=" * 60)

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
