"""自进化全链路 E2E 测试。

验证完整闭环：eval_failure 事件 → EvolutionEngine 生成提案
→ 自动分发到 Plane → Plane Webhook → DevFlow Orchestrator
→ CodingAgentRunner → commit+push → 创建 MR。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import httpx
import pytest

from agent_platform.devflow.orchestrator import DevFlowOrchestrator, DevFlowResult
from agent_platform.devflow.runner.adapters.mock import MockRunnerAdapter
from agent_platform.devflow.runner.models import JobState, ResultStatus, ValidationResult
from agent_platform.devflow.runner.runner import CodingAgentRunner
from agent_platform.devflow.runner.workspace import WorkspaceManager
from agent_platform.evolution.engine import EvolutionEngine
from agent_platform.evolution.models import (
    EvolutionEvent,
    ProposalStatus,
    RiskLevel,
)
from agent_platform.evolution.repository import InMemoryProposalRepository
from agent_platform.integrations.gitlab.adapter import GitLabAdapter
from agent_platform.integrations.plane.adapter import PlaneAdapter

AGENT_ID = "echo"
PLANE_PROJECT_ID = "proj-evo-e2e"
GITLAB_PROJECT_ID = "gl-proj-evo"
AI_DEV_STATE_ID = "state-ai-dev"
MR_IID = 42


# ---------------------------------------------------------------------------
# Mock Transports
# ---------------------------------------------------------------------------


def _plane_transport() -> httpx.MockTransport:
    created_work_item_id = "wi-evo-e2e-001"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method

        if "/work-items" in path and method == "POST":
            return httpx.Response(200, json={
                "id": created_work_item_id,
                "project": PLANE_PROJECT_ID,
                "name": "auto-created",
                "state_detail": {"name": "Backlog"},
            })
        if "/work-items/" in path and method == "GET":
            return httpx.Response(200, json={
                "id": created_work_item_id,
                "project": PLANE_PROJECT_ID,
                "name": "[echo] eval case 缺失导致质量回归",
                "description_stripped": "echo agent 在 greeting 场景下返回了错误格式",
                "state_detail": {"name": "Ready for AI Dev"},
                "properties": {"agent_id": AGENT_ID, "task_type": "agent:prompt_eval_improvement"},
            })
        if "/comments/" in path and method == "POST":
            return httpx.Response(200, json={"id": "cmt-1"})
        if "/work-items/" in path and method in ("PATCH", "PUT"):
            return httpx.Response(200, json={})
        if "/states/" in path:
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={})

    return httpx.MockTransport(handler)


def _gitlab_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method

        if "merge_requests" in path and "notes" in path and method == "POST":
            return httpx.Response(200, json={"id": "note-1"})
        if "merge_requests" in path and method == "POST":
            return httpx.Response(200, json={
                "iid": MR_IID,
                "web_url": f"https://gitlab.mock/project/mr/{MR_IID}",
                "source_branch": "evo/echo-test",
                "target_branch": "main",
            })
        if "branches" in path:
            return httpx.Response(200, json={"name": "evo/echo-test"})
        if "statuses" in path:
            return httpx.Response(200, json={})
        return httpx.Response(200, json={})

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# Stub WorkspaceManager
# ---------------------------------------------------------------------------


class StubWorkspaceManager(WorkspaceManager):
    def __init__(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="evo-e2e-"))
        super().__init__(base_dir=self._tmp, cleanup_on_success=True)

    async def create(self, *, branch: str, repo_url: str) -> Path:
        ws = self._tmp / f"ws-{branch}"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / ".git").mkdir(exist_ok=True)
        return ws

    async def get_changed_files(self, workspace_dir: Path) -> list[str]:
        return [
            f"agents/{AGENT_ID}/prompts/orchestrator.md",
            f"agents/{AGENT_ID}/evals/golden.yaml",
        ]

    async def run_validation(
        self, workspace_dir: Path, commands: list[str],
    ) -> ValidationResult:
        return ValidationResult(commands_executed=[], all_passed=True)

    async def commit_and_push(
        self, workspace_dir: Path, *, message: str, branch: str, changed_files: list[str],
    ) -> str | None:
        return "e2e0000abcd1234"

    async def cleanup(self, workspace_dir: Path, *, keep_on_failure: bool = False) -> None:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_plane(transport: httpx.MockTransport) -> PlaneAdapter:
    return PlaneAdapter(
        base_url="https://plane.mock",
        api_key="mock-key",
        workspace_slug="mock-ws",
        transport=transport,
    )


def _build_gitlab(transport: httpx.MockTransport) -> GitLabAdapter:
    return GitLabAdapter(
        base_url="https://gitlab.mock",
        token="mock-token",
        transport=transport,
    )


def _build_webhook_payload(work_item_id: str) -> dict:
    return {
        "data": {
            "id": work_item_id,
            "project": PLANE_PROJECT_ID,
            "name": "[echo] eval case 缺失导致质量回归",
            "state_detail": {"name": "Ready for AI Dev"},
            "properties": {
                "agent_id": AGENT_ID,
                "task_type": "agent:prompt_eval_improvement",
            },
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEvolutionE2E:
    """自进化全链路集成测试。"""

    @pytest.mark.asyncio
    async def test_eval_failure_to_mr(self):
        """eval_failure → proposal → auto-dispatch → DevFlow → Runner → MR 全链路。"""
        repo = InMemoryProposalRepository()
        plane_transport = _plane_transport()
        gitlab_transport = _gitlab_transport()
        plane = _build_plane(plane_transport)
        gitlab = _build_gitlab(gitlab_transport)

        # === 阶段 1：EvolutionEngine 接收 eval_failure 事件 ===
        engine = EvolutionEngine(
            repo,
            plane_adapter=plane,
            plane_project_id=PLANE_PROJECT_ID,
            ai_developing_state_id=AI_DEV_STATE_ID,
        )

        event = EvolutionEvent(
            event_type="eval_failure",
            agent_id=AGENT_ID,
            summary="echo agent 在 greeting 场景下返回了错误格式，缺少 eval case 覆盖",
            details={"id": "eval-001", "trace_id": "trace-abc"},
        )

        proposal = await engine.process_event(event)
        assert proposal is not None, "应生成 ImprovementProposal"
        assert proposal.agent_id == AGENT_ID
        assert proposal.status == ProposalStatus.DRAFT

        # 风险分类器应将纯 prompt+eval 修改判为 LOW
        assert proposal.risk.level == RiskLevel.LOW, (
            f"纯 prompt/eval 修改应为 LOW，实际: {proposal.risk.level}"
        )
        assert not proposal.risk.requires_human_confirmation_before_devflow

        # === 阶段 2：低风险自动分发到 Plane ===
        dispatch_result = await engine.auto_dispatch_if_low_risk(proposal)
        assert dispatch_result is not None, "LOW 风险应触发自动分发"
        assert dispatch_result.get("status") == "dispatched"

        work_item_id = dispatch_result["plane_work_item_id"]
        assert work_item_id, "应返回 Plane work_item_id"

        # 验证提案状态已更新
        updated_proposal = await repo.get(proposal.proposal_id)
        assert updated_proposal.status == ProposalStatus.DISPATCHED
        assert updated_proposal.plane_work_item_id == work_item_id

        # === 阶段 3：模拟 Plane Webhook → DevFlow Orchestrator ===
        mock_adapter = MockRunnerAdapter(should_fail=False)
        ws_manager = StubWorkspaceManager()

        runner = CodingAgentRunner(
            adapter=mock_adapter,
            workspace_manager=ws_manager,
            gitlab=gitlab,
            plane=plane,
            gitlab_project_id=GITLAB_PROJECT_ID,
            repo_url="https://mock.repo/test.git",
        )

        orch = DevFlowOrchestrator(
            plane=plane,
            gitlab=gitlab,
            gitlab_project_id=GITLAB_PROJECT_ID,
            coding_runner=runner,
            ai_developing_state_id=AI_DEV_STATE_ID,
        )

        webhook_payload = _build_webhook_payload(work_item_id)
        result = await orch.handle_webhook_event("work_item.updated", webhook_payload)

        # === 阶段 4：验证 DevFlow 执行结果 ===
        assert isinstance(result, DevFlowResult), "应返回 DevFlowResult"
        assert result.branch, "应创建分支"

        job = result.coding_job
        assert job is not None, "应有 CodingJob"
        assert job.state == JobState.SUCCEEDED, f"Job 应成功，实际: {job.state}"
        assert job.result is not None
        assert job.result.status == ResultStatus.SUCCESS
        assert job.result.commit_sha, "应有 commit sha"
        assert job.mr_iid == MR_IID, f"应创建 MR，实际 mr_iid: {job.mr_iid}"

    @pytest.mark.asyncio
    async def test_high_risk_not_auto_dispatched(self):
        """HIGH 风险提案不应自动分发。"""
        repo = InMemoryProposalRepository()
        plane = _build_plane(_plane_transport())

        engine = EvolutionEngine(
            repo,
            plane_adapter=plane,
            plane_project_id=PLANE_PROJECT_ID,
        )

        event = EvolutionEvent(
            event_type="tool_error",
            agent_id=AGENT_ID,
            summary="工具调用异常需要修改平台代码",
            details={"id": "tool-001"},
        )

        proposal = await engine.process_event(event)
        assert proposal is not None

        # tool_error → TOOL_RUNTIME_ERROR → MEDIUM risk
        assert proposal.risk.level in (RiskLevel.MEDIUM, RiskLevel.HIGH)

        dispatch_result = await engine.auto_dispatch_if_low_risk(proposal)
        assert dispatch_result is None, "非 LOW 风险不应自动分发"

        updated = await repo.get(proposal.proposal_id)
        assert updated.status == ProposalStatus.DRAFT, "应保持 DRAFT 状态"

    @pytest.mark.asyncio
    async def test_duplicate_event_deduplicated(self):
        """重复事件应被去重。"""
        repo = InMemoryProposalRepository()
        engine = EvolutionEngine(repo)

        event = EvolutionEvent(
            event_type="eval_failure",
            agent_id=AGENT_ID,
            summary="重复的 eval 失败事件",
        )

        p1 = await engine.process_event(event)
        p2 = await engine.process_event(event)

        assert p1 is not None
        assert p2 is None, "重复事件应返回 None"

        all_proposals = await repo.list_all()
        assert len(all_proposals) == 1, "仓库中应只有 1 个提案"

    @pytest.mark.asyncio
    async def test_proposal_to_devflow_with_runner_failure(self):
        """Runner 失败时 Job 状态为 FAILED，但不影响提案分发。"""
        repo = InMemoryProposalRepository()
        plane = _build_plane(_plane_transport())
        gitlab = _build_gitlab(_gitlab_transport())

        engine = EvolutionEngine(
            repo,
            plane_adapter=plane,
            plane_project_id=PLANE_PROJECT_ID,
            ai_developing_state_id=AI_DEV_STATE_ID,
        )

        event = EvolutionEvent(
            event_type="eval_failure",
            agent_id=AGENT_ID,
            summary="runner 失败场景测试",
            details={"id": "eval-fail-001"},
        )
        proposal = await engine.process_event(event)
        dispatch_result = await engine.auto_dispatch_if_low_risk(proposal)
        work_item_id = dispatch_result["plane_work_item_id"]

        # Runner 配置为失败
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
            plane=plane,
            gitlab=gitlab,
            gitlab_project_id=GITLAB_PROJECT_ID,
            coding_runner=runner,
        )

        result = await orch.handle_webhook_event(
            "work_item.updated", _build_webhook_payload(work_item_id),
        )
        assert isinstance(result, DevFlowResult)
        assert result.coding_job is not None
        assert result.coding_job.state == JobState.FAILED

        # 提案仍为 DISPATCHED（Runner 失败不回滚提案状态）
        updated = await repo.get(proposal.proposal_id)
        assert updated.status == ProposalStatus.DISPATCHED

    @pytest.mark.asyncio
    async def test_metrics_reflect_dispatched_proposal(self):
        """分发后的提案应反映在指标统计中。"""
        from agent_platform.evolution.metrics import EvolutionMetricsCollector

        repo = InMemoryProposalRepository()
        plane = _build_plane(_plane_transport())

        engine = EvolutionEngine(
            repo,
            plane_adapter=plane,
            plane_project_id=PLANE_PROJECT_ID,
            ai_developing_state_id=AI_DEV_STATE_ID,
        )

        event = EvolutionEvent(
            event_type="eval_failure",
            agent_id=AGENT_ID,
            summary="指标验证用的 eval 失败事件",
            details={"id": "eval-metrics-001"},
        )
        proposal = await engine.process_event(event)
        await engine.auto_dispatch_if_low_risk(proposal)

        collector = EvolutionMetricsCollector(repo)
        metrics = await collector.collect()

        assert metrics.total_proposals == 1
        assert metrics.dispatched_count == 1
        assert metrics.auto_dispatch_count == 1
        assert metrics.by_agent.get(AGENT_ID) == 1
        assert metrics.by_risk.get("low") == 1
