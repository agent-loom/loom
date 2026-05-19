"""PlanePublisher 单元测试（mock PlaneAdapter）。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_platform.feedback.gate import GateConfig, GateDecision, ProposalGate
from agent_platform.feedback.miner import RequirementProposal
from agent_platform.feedback.publisher import PlanePublisher


def _make_proposal(
    *,
    agent_id: str = "agent_a",
    severity: str = "high",
    confidence: float = 0.8,
    affected_sessions: int = 5,
    title: str = "测试提案",
    evidence: list[dict] | None = None,
) -> RequirementProposal:
    """构造测试用提案。"""
    return RequirementProposal(
        proposal_type="bug",
        title=title,
        agent_id=agent_id,
        severity=severity,
        confidence=confidence,
        evidence=evidence or [{"error": "TimeoutError", "count": 7}],
        impact={
            "affected_tenants": 3,
            "affected_sessions": affected_sessions,
            "first_seen": "2026-05-01T08:00:00Z",
            "last_seen": "2026-05-19T12:00:00Z",
        },
        suggested_task_type="fix",
        suggested_acceptance=["超时率降至 0.1% 以下", "P99 延迟 < 500ms"],
    )


def _make_approved_decision(proposal: RequirementProposal) -> GateDecision:
    """构造一个已通过的门控决策。"""
    return GateDecision(proposal=proposal, approved=True, reason="approved")


def _make_rejected_decision(
    proposal: RequirementProposal, reason: str = "low_confidence"
) -> GateDecision:
    """构造一个被拒绝的门控决策。"""
    return GateDecision(proposal=proposal, approved=False, reason=reason)


def _make_mock_plane() -> MagicMock:
    """构造 PlaneAdapter mock，create_work_item 返回预设字典。"""
    plane = MagicMock()
    plane.create_work_item = AsyncMock(
        return_value={"id": "wi-001", "name": "测试提案", "priority": "high"}
    )
    return plane


class TestPlanePublisher:
    """PlanePublisher.publish 的功能测试。"""

    @pytest.mark.asyncio
    async def test_只发布approved提案(self):
        """publish 应跳过 approved=False 的决策，只处理通过的提案。"""
        plane = _make_mock_plane()
        publisher = PlanePublisher(plane, project_id="proj-123")

        approved_proposal = _make_proposal(title="应发布的提案")
        rejected_proposal = _make_proposal(title="应跳过的提案")

        decisions = [
            _make_approved_decision(approved_proposal),
            _make_rejected_decision(rejected_proposal),
        ]

        result = await publisher.publish(decisions)

        # 只有 1 条 work item 被创建
        assert len(result) == 1
        assert plane.create_work_item.call_count == 1

    @pytest.mark.asyncio
    async def test_全部拒绝时返回空列表(self):
        """当所有决策都被拒绝时，返回空列表，create_work_item 不被调用。"""
        plane = _make_mock_plane()
        publisher = PlanePublisher(plane, project_id="proj-123")

        decisions = [
            _make_rejected_decision(_make_proposal(title=f"p{i}")) for i in range(3)
        ]

        result = await publisher.publish(decisions)

        assert result == []
        plane.create_work_item.assert_not_called()

    @pytest.mark.asyncio
    async def test_description包含证据和影响字段(self):
        """生成的 description 应包含证据和影响范围的关键信息。"""
        plane = _make_mock_plane()
        publisher = PlanePublisher(plane, project_id="proj-123")

        proposal = _make_proposal(
            evidence=[{"error": "NullPointerException", "file": "hermes.py"}],
        )
        decisions = [_make_approved_decision(proposal)]

        await publisher.publish(decisions)

        # 取出 create_work_item 调用时传入的 description 参数
        call_kwargs = plane.create_work_item.call_args.kwargs
        description = call_kwargs["description"]

        # 证据字段
        assert "NullPointerException" in description
        assert "hermes.py" in description

        # 影响范围字段
        assert "affected_sessions" in description or "受影响会话数" in description
        assert "5" in description  # affected_sessions 的值

    @pytest.mark.asyncio
    async def test_create_work_item被调用正确次数(self):
        """有 N 条 approved 决策时，create_work_item 应被调用恰好 N 次。"""
        plane = _make_mock_plane()
        publisher = PlanePublisher(plane, project_id="proj-123")

        approved_count = 4
        decisions = [
            _make_approved_decision(_make_proposal(title=f"提案{i}"))
            for i in range(approved_count)
        ]

        result = await publisher.publish(decisions)

        assert plane.create_work_item.call_count == approved_count
        assert len(result) == approved_count

    @pytest.mark.asyncio
    async def test_severity映射到正确priority(self):
        """severity 应正确映射到 Plane priority 值。"""
        plane = _make_mock_plane()
        publisher = PlanePublisher(plane, project_id="proj-123")

        severity_priority_pairs = [
            ("critical", "urgent"),
            ("high", "high"),
            ("medium", "medium"),
            ("low", "low"),
        ]

        for severity, expected_priority in severity_priority_pairs:
            plane.create_work_item.reset_mock()
            proposal = _make_proposal(severity=severity)
            decisions = [_make_approved_decision(proposal)]
            await publisher.publish(decisions)

            call_kwargs = plane.create_work_item.call_args.kwargs
            assert call_kwargs["priority"] == expected_priority, (
                f"severity={severity} 应映射为 priority={expected_priority}，"
                f"实际为 {call_kwargs['priority']}"
            )

    @pytest.mark.asyncio
    async def test_create_work_item传入正确参数(self):
        """create_work_item 应收到正确的 name、priority 和 properties。"""
        plane = _make_mock_plane()
        publisher = PlanePublisher(plane, project_id="proj-456")

        proposal = _make_proposal(
            title="关键 Bug 修复",
            agent_id="hermes",
            severity="critical",
            confidence=0.95,
            affected_sessions=20,
        )
        decisions = [_make_approved_decision(proposal)]

        await publisher.publish(decisions)

        call_args = plane.create_work_item.call_args
        # 第一个位置参数是 project_id
        assert call_args.args[0] == "proj-456"
        kwargs = call_args.kwargs
        assert kwargs["name"] == "关键 Bug 修复"
        assert kwargs["priority"] == "urgent"

        props = kwargs["properties"]
        assert props["source"] == "runtime_feedback"
        assert props["agent_id"] == "hermes"
        assert props["proposal_type"] == "bug"
        assert props["confidence"] == "0.95"
        assert props["affected_sessions"] == "20"
