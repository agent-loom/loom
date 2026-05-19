"""DevFlow 全链路集成验证测试。

验证 GitLab webhook → 状态同步 → Reconciler → Feedback Intelligence 完整链路。
使用 mock adapter（不依赖真实 GitLab/Plane），验证组件间的接线正确性。
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_platform.devflow.reconcile import DevFlowReconciler
from agent_platform.devflow.state_sync import DevFlowStateSync
from agent_platform.feedback.collector import FeedbackSignal
from agent_platform.feedback.gate import GateDecision, ProposalGate
from agent_platform.feedback.miner import FeedbackMiner, RequirementProposal
from agent_platform.feedback.service import FeedbackIntelligenceService
from agent_platform.integrations.gitlab.webhook import GitLabEventHandler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_plane() -> MagicMock:
    plane = MagicMock()
    plane.update_work_item_state = AsyncMock()
    plane.add_comment = AsyncMock()
    plane.list_work_items = AsyncMock(return_value={"results": []})
    plane.get_work_item = AsyncMock(return_value={
        "id": "wi-001",
        "name": "test item",
        "state": "Ready for AI Dev",
        "custom_properties": {},
    })
    return plane


def _mock_webhook_repo() -> MagicMock:
    repo = MagicMock()
    repo.exists = AsyncMock(return_value=False)
    repo.record = AsyncMock()
    return repo


# ---------------------------------------------------------------------------
# GitLab Webhook → Plane 状态同步
# ---------------------------------------------------------------------------


class TestGitLabWebhookPipelineSync:
    """验证 GitLab pipeline 事件正确回写 Plane 状态。"""

    @pytest.mark.asyncio
    async def test_pipeline_running_syncs_testing(self):
        plane = _mock_plane()
        handler = GitLabEventHandler(
            plane=plane,
            webhook_repo=_mock_webhook_repo(),
            testing_state_id="state-testing",
            ai_developing_state_id="state-ai-dev",
            human_review_state_id="state-review",
        )

        payload = {
            "object_attributes": {"status": "running", "ref": "feat/ai-001"},
            "variables": [
                {"key": "PLANE_PROJECT_ID", "value": "proj-1"},
                {"key": "PLANE_WORK_ITEM_ID", "value": "wi-001"},
            ],
        }

        result = await handler.handle_event("pipeline", payload)
        assert result["status"] == "synced"
        assert "Testing" in result["action"]
        plane.update_work_item_state.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pipeline_success_syncs_human_review(self):
        plane = _mock_plane()
        handler = GitLabEventHandler(
            plane=plane,
            webhook_repo=_mock_webhook_repo(),
            testing_state_id="state-testing",
            ai_developing_state_id="state-ai-dev",
            human_review_state_id="state-review",
        )

        payload = {
            "object_attributes": {"status": "success", "ref": "feat/ai-001"},
            "variables": [
                {"key": "PLANE_PROJECT_ID", "value": "proj-1"},
                {"key": "PLANE_WORK_ITEM_ID", "value": "wi-001"},
            ],
        }

        result = await handler.handle_event("pipeline", payload)
        assert result["status"] == "synced"
        assert "Human Review" in result["action"]

    @pytest.mark.asyncio
    async def test_pipeline_failed_triggers_callback(self):
        """Pipeline 失败时触发 on_pipeline_failed 回调。"""
        plane = _mock_plane()
        callback = AsyncMock()

        handler = GitLabEventHandler(
            plane=plane,
            webhook_repo=_mock_webhook_repo(),
            testing_state_id="state-testing",
            ai_developing_state_id="state-ai-dev",
            human_review_state_id="state-review",
            on_pipeline_failed=callback,
        )

        payload = {
            "object_attributes": {"status": "failed", "ref": "feat/ai-001"},
            "variables": [
                {"key": "PLANE_PROJECT_ID", "value": "proj-1"},
                {"key": "PLANE_WORK_ITEM_ID", "value": "wi-001"},
            ],
        }

        result = await handler.handle_event("pipeline", payload)
        assert result["status"] == "synced"
        callback.assert_awaited_once_with(
            project_id="proj-1",
            work_item_id="wi-001",
            ref="feat/ai-001",
        )

    @pytest.mark.asyncio
    async def test_pipeline_failed_callback_error_does_not_propagate(self):
        """Pipeline 失败回调异常不影响主流程。"""
        plane = _mock_plane()
        callback = AsyncMock(side_effect=RuntimeError("回调失败"))

        handler = GitLabEventHandler(
            plane=plane,
            webhook_repo=_mock_webhook_repo(),
            testing_state_id="state-testing",
            ai_developing_state_id="state-ai-dev",
            on_pipeline_failed=callback,
        )

        payload = {
            "object_attributes": {"status": "failed", "ref": "feat/ai-001"},
            "variables": [
                {"key": "PLANE_PROJECT_ID", "value": "proj-1"},
                {"key": "PLANE_WORK_ITEM_ID", "value": "wi-001"},
            ],
        }

        result = await handler.handle_event("pipeline", payload)
        assert result["status"] == "synced"

    @pytest.mark.asyncio
    async def test_duplicate_event_ignored(self):
        """幂等：重复 delivery_id 被跳过。"""
        plane = _mock_plane()
        repo = _mock_webhook_repo()
        repo.exists = AsyncMock(return_value=True)

        handler = GitLabEventHandler(
            plane=plane,
            webhook_repo=repo,
            testing_state_id="state-testing",
        )

        payload = {
            "object_attributes": {"status": "running", "ref": "feat/ai-001"},
            "variables": [
                {"key": "PLANE_PROJECT_ID", "value": "proj-1"},
                {"key": "PLANE_WORK_ITEM_ID", "value": "wi-001"},
            ],
        }

        result = await handler.handle_event("pipeline", payload)
        assert result["status"] == "duplicate"
        plane.update_work_item_state.assert_not_awaited()


# ---------------------------------------------------------------------------
# GitLab Webhook → MR 状态同步
# ---------------------------------------------------------------------------


class TestGitLabWebhookMRSync:
    """验证 MR 事件正确同步 Plane 状态。"""

    @pytest.mark.asyncio
    async def test_mr_merged_syncs_staging(self):
        plane = _mock_plane()
        handler = GitLabEventHandler(
            plane=plane,
            webhook_repo=_mock_webhook_repo(),
            staging_state_id="state-staging",
        )

        payload = {
            "object_attributes": {
                "action": "merge",
                "state": "merged",
                "source_branch": "feat/ai-001",
                "description": "<!-- devflow:plane_project_id=proj-1 plane_work_item_id=wi-001 -->",
            },
        }

        result = await handler.handle_event("merge_request", payload)
        assert result["status"] == "synced"
        assert "Staging" in result["action"]

    @pytest.mark.asyncio
    async def test_mr_closed_syncs_ai_developing(self):
        plane = _mock_plane()
        handler = GitLabEventHandler(
            plane=plane,
            webhook_repo=_mock_webhook_repo(),
            ai_developing_state_id="state-ai-dev",
        )

        payload = {
            "object_attributes": {
                "action": "close",
                "state": "closed",
                "source_branch": "feat/ai-001",
                "description": "<!-- devflow:plane_project_id=proj-1 plane_work_item_id=wi-001 -->",
            },
        }

        result = await handler.handle_event("merge_request", payload)
        assert result["status"] == "synced"
        assert "AI Developing" in result["action"]


# ---------------------------------------------------------------------------
# Reconciler 全量对账
# ---------------------------------------------------------------------------


class TestReconcilerIntegration:
    """验证 Reconciler 全量对账流程。"""

    @pytest.mark.asyncio
    async def test_reconcile_empty_project(self):
        plane = _mock_plane()
        plane.list_work_items = AsyncMock(return_value={"results": []})
        gitlab = MagicMock()

        state_sync = MagicMock(spec=DevFlowStateSync)
        reconciler = DevFlowReconciler(
            state_sync=state_sync,
            plane=plane,
            gitlab=gitlab,
            gitlab_project_id="proj-gl-1",
        )

        result = await reconciler.run_reconciliation("proj-1")
        assert result["status"] == "completed"
        assert result["total_candidates"] == 0

    @pytest.mark.asyncio
    async def test_reconcile_returns_summary(self):
        """对账返回有意义的摘要字典。"""
        plane = _mock_plane()
        plane.list_work_items = AsyncMock(return_value={
            "results": [
                {"id": "wi-001", "state": "AI Developing", "custom_properties": {}},
                {"id": "wi-002", "state": "Done", "custom_properties": {}},
            ],
        })
        gitlab = MagicMock()
        gitlab.get_merge_request = AsyncMock()

        state_sync = MagicMock(spec=DevFlowStateSync)
        state_sync.from_plane_state = MagicMock(side_effect=ValueError("unmapped"))

        reconciler = DevFlowReconciler(
            state_sync=state_sync,
            plane=plane,
            gitlab=gitlab,
            gitlab_project_id="proj-gl-1",
        )

        result = await reconciler.run_reconciliation("proj-1")
        assert result["status"] == "completed"
        assert result["total_candidates"] == 0

    @pytest.mark.asyncio
    async def test_reconcile_plane_error_returns_error_status(self):
        plane = _mock_plane()
        plane.list_work_items = AsyncMock(side_effect=RuntimeError("Plane 不可用"))
        gitlab = MagicMock()
        state_sync = MagicMock(spec=DevFlowStateSync)

        reconciler = DevFlowReconciler(
            state_sync=state_sync,
            plane=plane,
            gitlab=gitlab,
            gitlab_project_id="proj-gl-1",
        )

        result = await reconciler.run_reconciliation("proj-1")
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Feedback Intelligence 全链路
# ---------------------------------------------------------------------------


class TestFeedbackIntelligenceIntegration:
    """验证反馈智能闭环完整流程。"""

    @pytest.mark.asyncio
    async def test_full_loop_collect_mine_gate_publish(self):
        """信号 → 提案 → 门控 → 发布完整链路。"""
        signals = [
            FeedbackSignal(
                signal_type="error",
                agent_id="agent_x",
                tenant_id="t1",
                run_id=f"run_{i}",
                tool_name=None,
                error_message="timeout error",
                confidence=None,
                session_id=f"sess_{i}",
                occurred_at=datetime(2026, 5, 19, i, 0, tzinfo=UTC),
            )
            for i in range(5)
        ]

        collector = MagicMock()
        collector.collect_recent = AsyncMock(return_value=signals)

        miner = FeedbackMiner()
        proposals = miner.mine(signals)
        assert len(proposals) >= 1

        gate = ProposalGate()
        decisions = gate.evaluate(proposals)
        approved = [d for d in decisions if d.approved]
        assert len(approved) >= 1

        publisher = MagicMock()
        publisher.publish = AsyncMock(return_value=[{"id": "wi-new"}])

        service = FeedbackIntelligenceService(
            collector=collector,
            miner=miner,
            gate=gate,
            publisher=publisher,
        )

        result = await service.run(hours=24)
        assert result.signals_collected == 5
        assert result.proposals_generated >= 1
        assert result.proposals_approved >= 1
        assert result.work_items_created == 1


# ---------------------------------------------------------------------------
# DevFlow 调度器
# ---------------------------------------------------------------------------


class TestDevFlowScheduler:
    """验证后台调度器的启停。"""

    @pytest.mark.asyncio
    async def test_start_stop_no_error(self):
        from agent_platform.devflow.scheduler import DevFlowScheduler

        scheduler = DevFlowScheduler(
            reconciler=None,
            feedback_service=None,
            reconcile_interval=60,
            feedback_interval=120,
        )
        await scheduler.start()
        assert scheduler._running is True
        await scheduler.stop()
        assert scheduler._running is False

    @pytest.mark.asyncio
    async def test_scheduler_creates_tasks_when_components_present(self):
        import asyncio
        from agent_platform.devflow.scheduler import DevFlowScheduler

        mock_reconciler = MagicMock()
        mock_reconciler.run_reconciliation = AsyncMock(
            return_value={"status": "completed", "total_candidates": 0, "processed": 0}
        )

        scheduler = DevFlowScheduler(
            reconciler=mock_reconciler,
            feedback_service=None,
            project_id="proj-1",
            reconcile_interval=60,
            feedback_interval=120,
        )

        await scheduler.start()
        assert len(scheduler._tasks) == 1
        assert scheduler._tasks[0].get_name() == "devflow-reconciler"
        await scheduler.stop()
        assert len(scheduler._tasks) == 0
