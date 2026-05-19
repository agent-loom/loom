"""FeedbackIntelligenceService 单元测试。"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_platform.feedback.collector import FeedbackSignal
from agent_platform.feedback.gate import GateConfig, GateDecision, ProposalGate
from agent_platform.feedback.miner import FeedbackMiner, RequirementProposal
from agent_platform.feedback.service import FeedbackIntelligenceService


def _make_proposal(
    agent_id: str = "test_agent",
    proposal_type: str = "bug",
    severity: str = "medium",
    confidence: float = 0.8,
    affected_sessions: int = 5,
) -> RequirementProposal:
    return RequirementProposal(
        proposal_type=proposal_type,
        title=f"测试提案 [{agent_id}]",
        agent_id=agent_id,
        severity=severity,
        confidence=confidence,
        evidence=[{"run_id": "run_001", "summary": "测试错误"}],
        impact={
            "affected_tenants": 1,
            "affected_sessions": affected_sessions,
            "first_seen": datetime(2026, 5, 19, 0, 0).isoformat(),
            "last_seen": datetime(2026, 5, 19, 12, 0).isoformat(),
        },
        suggested_task_type="agent:change",
        suggested_acceptance=["修复后无相同错误"],
    )


def _make_signal(agent_id: str = "test_agent") -> FeedbackSignal:
    return FeedbackSignal(
        signal_type="error",
        agent_id=agent_id,
        tenant_id="tenant_a",
        run_id="run_001",
        tool_name=None,
        error_message="连接超时",
        confidence=None,
        session_id=None,
        occurred_at=datetime(2026, 5, 19, 8, 0),
    )


def _make_service(
    signals: list[FeedbackSignal] | None = None,
    proposals: list[RequirementProposal] | None = None,
    decisions: list[GateDecision] | None = None,
    created_items: list[dict] | None = None,
) -> tuple[FeedbackIntelligenceService, MagicMock, MagicMock, MagicMock, MagicMock]:
    collector = MagicMock()
    collector.collect_recent = AsyncMock(return_value=signals or [])
    miner = MagicMock()
    miner.mine = MagicMock(return_value=proposals or [])
    gate = MagicMock()
    gate.evaluate = MagicMock(return_value=decisions or [])
    publisher = MagicMock()
    publisher.publish = AsyncMock(return_value=created_items or [])

    svc = FeedbackIntelligenceService(
        collector=collector,
        miner=miner,
        gate=gate,
        publisher=publisher,
    )
    return svc, collector, miner, gate, publisher


@pytest.mark.asyncio
async def test_no_signals_returns_early() -> None:
    """无信号时不调用 miner 和后续组件。"""
    svc, _, miner, gate, publisher = _make_service(signals=[])
    result = await svc.run(hours=24)

    assert result.signals_collected == 0
    assert result.proposals_generated == 0
    miner.mine.assert_not_called()
    gate.evaluate.assert_not_called()
    publisher.publish.assert_not_called()


@pytest.mark.asyncio
async def test_no_proposals_skips_gate_and_publisher() -> None:
    """信号存在但 miner 未生成提案时，不调用 gate 和 publisher。"""
    svc, _, miner, gate, publisher = _make_service(
        signals=[_make_signal()],
        proposals=[],
    )
    result = await svc.run()

    assert result.signals_collected == 1
    assert result.proposals_generated == 0
    gate.evaluate.assert_not_called()
    publisher.publish.assert_not_called()


@pytest.mark.asyncio
async def test_happy_path_full_pipeline() -> None:
    """完整链路：信号→提案→批准→发布。"""
    proposal = _make_proposal()
    decision = GateDecision(proposal=proposal, approved=True, reason="approved")
    svc, _, miner, gate, publisher = _make_service(
        signals=[_make_signal()],
        proposals=[proposal],
        decisions=[decision],
        created_items=[{"id": "wi-001", "name": proposal.title}],
    )
    result = await svc.run()

    assert result.signals_collected == 1
    assert result.proposals_generated == 1
    assert result.proposals_approved == 1
    assert result.proposals_rejected == 0
    assert result.work_items_created == 1
    publisher.publish.assert_awaited_once_with([decision])


@pytest.mark.asyncio
async def test_rejected_proposals_counted() -> None:
    """被拒绝的提案计入 rejection_reasons。"""
    proposal = _make_proposal(confidence=0.3)
    decision = GateDecision(proposal=proposal, approved=False, reason="low_confidence")
    svc, _, _, _, publisher = _make_service(
        signals=[_make_signal()],
        proposals=[proposal],
        decisions=[decision],
    )
    result = await svc.run()

    assert result.proposals_approved == 0
    assert result.proposals_rejected == 1
    assert result.rejection_reasons == {"low_confidence": 1}
    publisher.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_collector_failure_returns_empty_result() -> None:
    """采集器抛异常时，返回空汇总而不向外传播。"""
    collector = MagicMock()
    collector.collect_recent = AsyncMock(side_effect=RuntimeError("DB 连接失败"))
    miner = MagicMock()
    gate = MagicMock()
    publisher = MagicMock()
    publisher.publish = AsyncMock(return_value=[])

    svc = FeedbackIntelligenceService(
        collector=collector, miner=miner, gate=gate, publisher=publisher
    )
    result = await svc.run()

    assert result.signals_collected == 0
    assert result.work_items_created == 0
    miner.mine.assert_not_called()


@pytest.mark.asyncio
async def test_publisher_failure_does_not_raise() -> None:
    """发布失败时，方法不向外抛异常，work_items_created 保持为 0。"""
    proposal = _make_proposal()
    decision = GateDecision(proposal=proposal, approved=True, reason="approved")
    svc, _, _, _, publisher = _make_service(
        signals=[_make_signal()],
        proposals=[proposal],
        decisions=[decision],
    )
    publisher.publish = AsyncMock(side_effect=RuntimeError("Plane API 超时"))

    result = await svc.run()

    assert result.proposals_approved == 1
    assert result.work_items_created == 0


@pytest.mark.asyncio
async def test_mixed_approved_rejected() -> None:
    """部分通过部分拒绝时，汇总计数正确。"""
    p1 = _make_proposal(agent_id="agent_a")
    p2 = _make_proposal(agent_id="agent_b")
    d1 = GateDecision(proposal=p1, approved=True, reason="approved")
    d2 = GateDecision(proposal=p2, approved=False, reason="insufficient_impact")
    svc, _, _, _, publisher = _make_service(
        signals=[_make_signal(), _make_signal()],
        proposals=[p1, p2],
        decisions=[d1, d2],
        created_items=[{"id": "wi-002"}],
    )
    result = await svc.run()

    assert result.proposals_approved == 1
    assert result.proposals_rejected == 1
    assert result.rejection_reasons == {"insufficient_impact": 1}
    assert result.work_items_created == 1
