"""FeedbackMiner 单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_platform.feedback.collector import FeedbackSignal
from agent_platform.feedback.miner import FeedbackMiner, RequirementProposal


# ------------------------------------------------------------------
# 工厂函数
# ------------------------------------------------------------------


def _make_error_signal(
    agent_id: str = "agent-a",
    run_id: str = "run-1",
    tenant_id: str = "tenant-1",
    session_id: str | None = "sess-1",
    error_message: str = "NullPointerException in handler",
    occurred_at: datetime | None = None,
) -> FeedbackSignal:
    """生成一条 error 类型的反馈信号。"""
    return FeedbackSignal(
        signal_type="error",
        agent_id=agent_id,
        tenant_id=tenant_id,
        run_id=run_id,
        tool_name=None,
        error_message=error_message,
        confidence=None,
        session_id=session_id,
        occurred_at=occurred_at or datetime.now(UTC),
    )


def _make_fallback_signal(
    agent_id: str = "agent-b",
    run_id: str = "run-fb-1",
    tenant_id: str = "tenant-1",
    session_id: str | None = "sess-fb-1",
    occurred_at: datetime | None = None,
) -> FeedbackSignal:
    """生成一条 fallback 类型的反馈信号。"""
    return FeedbackSignal(
        signal_type="fallback",
        agent_id=agent_id,
        tenant_id=tenant_id,
        run_id=run_id,
        tool_name=None,
        error_message="fallback triggered",
        confidence=None,
        session_id=session_id,
        occurred_at=occurred_at or datetime.now(UTC),
    )


def _make_run_signal(
    agent_id: str = "agent-a",
    run_id: str = "run-ok-1",
) -> FeedbackSignal:
    """生成一条普通 run 类型的反馈信号。"""
    return FeedbackSignal(
        signal_type="run",
        agent_id=agent_id,
        tenant_id="tenant-1",
        run_id=run_id,
        tool_name=None,
        error_message=None,
        confidence=None,
        session_id=None,
        occurred_at=datetime.now(UTC),
    )


# ------------------------------------------------------------------
# 测试用例
# ------------------------------------------------------------------


class TestFeedbackMinerBug:
    """验证 bug 提案的生成逻辑。"""

    def test_3_same_errors_produce_1_bug_proposal(self):
        """3 个同类错误应生成 1 个 bug 提案。"""
        miner = FeedbackMiner()
        signals = [
            _make_error_signal(run_id=f"run-{i}", error_message="Connection timeout")
            for i in range(3)
        ]
        proposals = miner.mine(signals)

        bug_proposals = [p for p in proposals if p.proposal_type == "bug"]
        assert len(bug_proposals) == 1

        proposal = bug_proposals[0]
        assert proposal.agent_id == "agent-a"
        assert "Connection timeout" in proposal.title

    def test_2_same_errors_produce_no_proposal(self):
        """2 个同类错误低于阈值（3），不应生成提案。"""
        miner = FeedbackMiner()
        signals = [
            _make_error_signal(run_id=f"run-{i}", error_message="Connection timeout")
            for i in range(2)
        ]
        proposals = miner.mine(signals)

        bug_proposals = [p for p in proposals if p.proposal_type == "bug"]
        assert len(bug_proposals) == 0

    def test_different_error_types_produce_separate_proposals(self):
        """两种不同错误分别满足阈值时，各自生成独立提案。"""
        miner = FeedbackMiner()
        err_a = [
            _make_error_signal(run_id=f"a-{i}", error_message="TimeoutError")
            for i in range(3)
        ]
        err_b = [
            _make_error_signal(run_id=f"b-{i}", error_message="AuthFailure")
            for i in range(4)
        ]
        proposals = miner.mine(err_a + err_b)

        bug_proposals = [p for p in proposals if p.proposal_type == "bug"]
        assert len(bug_proposals) == 2

    def test_evidence_does_not_contain_session_or_tenant(self):
        """evidence 字段只能包含 run_id 和 summary，不得含 session_id / tenant_id。"""
        miner = FeedbackMiner()
        signals = [
            _make_error_signal(
                run_id=f"run-{i}",
                tenant_id="secret-tenant",
                session_id="secret-session",
                error_message="NullPointerException",
            )
            for i in range(3)
        ]
        proposals = miner.mine(signals)
        assert len(proposals) == 1

        for ev in proposals[0].evidence:
            assert "session_id" not in ev
            assert "tenant_id" not in ev
            assert "run_id" in ev


class TestFeedbackMinerFallback:
    """验证 optimization 提案的生成逻辑。"""

    def test_5_fallbacks_produce_1_optimization_proposal(self):
        """5 个 fallback 信号应生成 1 个 optimization 提案。"""
        miner = FeedbackMiner()
        signals = [
            _make_fallback_signal(run_id=f"fb-{i}") for i in range(5)
        ]
        proposals = miner.mine(signals)

        opt_proposals = [p for p in proposals if p.proposal_type == "optimization"]
        assert len(opt_proposals) == 1

        proposal = opt_proposals[0]
        assert proposal.agent_id == "agent-b"
        assert "fallback" in proposal.title.lower()

    def test_4_fallbacks_produce_no_proposal(self):
        """4 个 fallback 低于阈值（5），不应生成提案。"""
        miner = FeedbackMiner()
        signals = [
            _make_fallback_signal(run_id=f"fb-{i}") for i in range(4)
        ]
        proposals = miner.mine(signals)

        opt_proposals = [p for p in proposals if p.proposal_type == "optimization"]
        assert len(opt_proposals) == 0


class TestFeedbackMinerConfidence:
    """验证 confidence 计算逻辑。"""

    def test_confidence_equals_count_over_total(self):
        """confidence = 出现次数 / 总信号数，且上限为 0.95。"""
        miner = FeedbackMiner()
        # 3 个 error + 7 个普通 run = 10 个信号
        error_signals = [
            _make_error_signal(run_id=f"err-{i}", error_message="DivisionByZero")
            for i in range(3)
        ]
        run_signals = [_make_run_signal(run_id=f"ok-{i}") for i in range(7)]

        proposals = miner.mine(error_signals + run_signals)
        bug_proposals = [p for p in proposals if p.proposal_type == "bug"]
        assert len(bug_proposals) == 1

        # confidence = 3 / 10 = 0.3
        assert abs(bug_proposals[0].confidence - 0.3) < 1e-9

    def test_confidence_capped_at_0_95(self):
        """当出现次数占总数比例超过 95% 时，confidence 应被截断为 0.95。"""
        miner = FeedbackMiner()
        # 10 个 error，总信号也是 10 → 比例 = 1.0，期望截断为 0.95
        signals = [
            _make_error_signal(run_id=f"run-{i}", error_message="CriticalFailure")
            for i in range(10)
        ]
        proposals = miner.mine(signals)
        bug_proposals = [p for p in proposals if p.proposal_type == "bug"]
        assert len(bug_proposals) == 1
        assert bug_proposals[0].confidence == 0.95

    def test_mixed_signals_no_proposal_below_threshold(self):
        """只有 2 个 error + 大量 run 时，不满足 bug 阈值，不应生成提案。"""
        miner = FeedbackMiner()
        error_signals = [
            _make_error_signal(run_id=f"err-{i}", error_message="SomeError")
            for i in range(2)
        ]
        run_signals = [_make_run_signal(run_id=f"ok-{i}") for i in range(20)]

        proposals = miner.mine(error_signals + run_signals)
        bug_proposals = [p for p in proposals if p.proposal_type == "bug"]
        assert len(bug_proposals) == 0


class TestFeedbackMinerImpact:
    """验证 impact 字段的计算逻辑。"""

    def test_affected_tenants_counts_distinct_tenant_ids(self):
        """受影响租户数应等于不同 tenant_id 的去重计数。"""
        miner = FeedbackMiner()
        signals = [
            _make_error_signal(
                run_id=f"run-{i}",
                tenant_id=f"tenant-{i % 2}",  # 2 个不同租户
                error_message="SameError",
            )
            for i in range(3)
        ]
        proposals = miner.mine(signals)
        assert len(proposals) == 1
        assert proposals[0].impact["affected_tenants"] == 2

    def test_proposal_type_and_task_type(self):
        """bug 提案的 suggested_task_type 应为 agent:change。"""
        miner = FeedbackMiner()
        signals = [
            _make_error_signal(run_id=f"run-{i}", error_message="NullRef")
            for i in range(3)
        ]
        proposals = miner.mine(signals)
        assert proposals[0].suggested_task_type == "agent:change"
        assert len(proposals[0].suggested_acceptance) > 0
