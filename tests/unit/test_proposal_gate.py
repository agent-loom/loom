"""ProposalGate 单元测试。"""

from __future__ import annotations

import pytest

from agent_platform.feedback.gate import GateConfig, GateDecision, ProposalGate
from agent_platform.feedback.miner import RequirementProposal


def _make_proposal(
    *,
    agent_id: str = "agent_a",
    severity: str = "high",
    confidence: float = 0.8,
    affected_sessions: int = 5,
    title: str = "测试提案",
) -> RequirementProposal:
    """构造测试用提案，提供合理默认值以减少样板代码。"""
    return RequirementProposal(
        proposal_type="bug",
        title=title,
        agent_id=agent_id,
        severity=severity,
        confidence=confidence,
        evidence=[{"error": "NullPointerException", "count": 10}],
        impact={
            "affected_tenants": 2,
            "affected_sessions": affected_sessions,
            "first_seen": "2026-05-01T00:00:00Z",
            "last_seen": "2026-05-19T00:00:00Z",
        },
        suggested_task_type="fix",
        suggested_acceptance=["复现率降为 0", "错误率 < 0.1%"],
    )


class TestProposalGate:
    """ProposalGate.evaluate / _check 的完整覆盖测试。"""

    def test_低置信度被拒绝(self):
        """置信度低于 min_confidence 时，reason 应为 low_confidence。"""
        gate = ProposalGate(GateConfig(min_confidence=0.7))
        proposal = _make_proposal(confidence=0.5)
        decisions = gate.evaluate([proposal])

        assert len(decisions) == 1
        d = decisions[0]
        assert not d.approved
        assert d.reason == "low_confidence"

    def test_影响不足被拒绝(self):
        """affected_sessions 低于 min_affected_sessions 时，reason 应为 insufficient_impact。"""
        gate = ProposalGate(GateConfig(min_affected_sessions=5))
        proposal = _make_proposal(affected_sessions=2)
        decisions = gate.evaluate([proposal])

        assert len(decisions) == 1
        d = decisions[0]
        assert not d.approved
        assert d.reason == "insufficient_impact"

    def test_severity不在允许列表被拒绝(self):
        """severity 不在 allowed_severities 中时，reason 应为 insufficient_impact。"""
        gate = ProposalGate(
            GateConfig(allowed_severities=["medium", "high", "critical"])
        )
        proposal = _make_proposal(severity="low")
        decisions = gate.evaluate([proposal])

        assert len(decisions) == 1
        d = decisions[0]
        assert not d.approved
        assert d.reason == "insufficient_impact"

    def test_日配额超限被拒绝(self):
        """同一 agent 当日批准数达到 max_daily_proposals 后，后续提案被 quota_exceeded 拒绝。"""
        config = GateConfig(max_daily_proposals=2)
        gate = ProposalGate(config)

        # 构造 3 个正常提案，前 2 个应通过，第 3 个应超额
        proposals = [
            _make_proposal(title=f"提案{i}", agent_id="agent_quota")
            for i in range(3)
        ]
        decisions = gate.evaluate(proposals)

        assert decisions[0].approved
        assert decisions[1].approved
        assert not decisions[2].approved
        assert decisions[2].reason == "quota_exceeded"

    def test_通过条件的提案被批准(self):
        """满足所有条件的提案 approved 应为 True，reason 应为 approved。"""
        gate = ProposalGate()
        proposal = _make_proposal()
        decisions = gate.evaluate([proposal])

        assert len(decisions) == 1
        d = decisions[0]
        assert d.approved
        assert d.reason == "approved"

    def test_屏蔽的agent被拒绝(self):
        """在 blocked_agents 列表中的 agent 提案，reason 应为 agent_blocked。"""
        gate = ProposalGate(GateConfig(blocked_agents=["bad_agent"]))
        proposal = _make_proposal(agent_id="bad_agent")
        decisions = gate.evaluate([proposal])

        assert len(decisions) == 1
        d = decisions[0]
        assert not d.approved
        assert d.reason == "agent_blocked"

    def test_agent_blocked优先于低置信度(self):
        """agent_blocked 的优先级高于 low_confidence，同时满足时应返回 agent_blocked。"""
        gate = ProposalGate(
            GateConfig(blocked_agents=["bad_agent"], min_confidence=0.9)
        )
        proposal = _make_proposal(agent_id="bad_agent", confidence=0.1)
        decisions = gate.evaluate([proposal])

        assert decisions[0].reason == "agent_blocked"

    def test_evaluate返回列表与输入等长(self):
        """evaluate 的返回列表长度应与输入列表严格一致。"""
        gate = ProposalGate()
        proposals = [_make_proposal(title=f"p{i}") for i in range(5)]
        decisions = gate.evaluate(proposals)
        assert len(decisions) == len(proposals)

    def test_不同agent配额独立计算(self):
        """不同 agent 的日配额应相互独立，互不干扰。"""
        gate = ProposalGate(GateConfig(max_daily_proposals=1))
        p_a = _make_proposal(agent_id="agent_a")
        p_b = _make_proposal(agent_id="agent_b")
        decisions = gate.evaluate([p_a, p_b])

        # 两个 agent 各自首条提案均应通过
        assert decisions[0].approved
        assert decisions[1].approved
