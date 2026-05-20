"""EvolutionMetrics 单元测试。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent_platform.evolution.metrics import EvolutionMetrics, EvolutionMetricsCollector
from agent_platform.evolution.models import (
    Evidence,
    EvidenceType,
    ImprovementProposal,
    ProposalStatus,
    RiskAssessment,
    RiskLevel,
    RootCause,
    RootCauseCategory,
)
from agent_platform.evolution.repository import InMemoryProposalRepository


def _make_proposal(
    *,
    agent_id: str = "echo",
    risk: RiskLevel = RiskLevel.LOW,
    status: ProposalStatus = ProposalStatus.DRAFT,
    root_cause: RootCauseCategory = RootCauseCategory.PROMPT_GAP,
    outcome: str | None = None,
    created_at: datetime | None = None,
    closed_at: datetime | None = None,
) -> ImprovementProposal:
    now = created_at or datetime.now(UTC)
    p = ImprovementProposal(
        title=f"[{agent_id}] test",
        summary="test",
        agent_id=agent_id,
        risk=RiskAssessment(level=risk, reason="test"),
        root_cause=RootCause(
            category=root_cause, confidence=0.8, explanation="test",
        ),
        evidence=[Evidence(type=EvidenceType.EVAL_FAILURE, id="e1", summary="test")],
    )
    p.status = status
    p.created_at = now
    p.updated_at = now + timedelta(hours=1)
    if outcome:
        p.outcome = outcome
    if closed_at:
        p.closed_at = closed_at
    return p


@pytest.fixture
def repo() -> InMemoryProposalRepository:
    return InMemoryProposalRepository()


class TestEvolutionMetricsCollector:
    @pytest.mark.asyncio
    async def test_empty_repo(self, repo):
        collector = EvolutionMetricsCollector(repo)
        metrics = await collector.collect()
        assert metrics.total_proposals == 0
        assert metrics.by_status == {}

    @pytest.mark.asyncio
    async def test_basic_counts(self, repo):
        await repo.create(_make_proposal(status=ProposalStatus.DRAFT))
        await repo.create(_make_proposal(status=ProposalStatus.DISPATCHED))
        await repo.create(_make_proposal(status=ProposalStatus.DISMISSED))

        collector = EvolutionMetricsCollector(repo)
        metrics = await collector.collect()
        assert metrics.total_proposals == 3
        assert metrics.by_status["draft"] == 1
        assert metrics.by_status["dispatched"] == 1
        assert metrics.by_status["dismissed"] == 1
        assert metrics.dispatched_count == 1
        assert metrics.dismissed_count == 1

    @pytest.mark.asyncio
    async def test_by_agent(self, repo):
        await repo.create(_make_proposal(agent_id="echo"))
        await repo.create(_make_proposal(agent_id="echo"))
        await repo.create(_make_proposal(agent_id="myj"))

        collector = EvolutionMetricsCollector(repo)
        metrics = await collector.collect()
        assert metrics.by_agent["echo"] == 2
        assert metrics.by_agent["myj"] == 1

    @pytest.mark.asyncio
    async def test_by_risk(self, repo):
        await repo.create(_make_proposal(risk=RiskLevel.LOW))
        await repo.create(_make_proposal(risk=RiskLevel.LOW))
        await repo.create(_make_proposal(risk=RiskLevel.MEDIUM))

        collector = EvolutionMetricsCollector(repo)
        metrics = await collector.collect()
        assert metrics.by_risk["low"] == 2
        assert metrics.by_risk["medium"] == 1

    @pytest.mark.asyncio
    async def test_by_root_cause(self, repo):
        await repo.create(_make_proposal(root_cause=RootCauseCategory.PROMPT_GAP))
        await repo.create(_make_proposal(root_cause=RootCauseCategory.EVAL_GAP))

        collector = EvolutionMetricsCollector(repo)
        metrics = await collector.collect()
        assert metrics.by_root_cause["prompt_gap"] == 1
        assert metrics.by_root_cause["eval_gap"] == 1

    @pytest.mark.asyncio
    async def test_outcome_counts(self, repo):
        await repo.create(_make_proposal(
            status=ProposalStatus.CLOSED, outcome="merged",
        ))
        await repo.create(_make_proposal(
            status=ProposalStatus.CLOSED, outcome="rejected",
        ))
        await repo.create(_make_proposal(
            status=ProposalStatus.CLOSED, outcome="abandoned",
        ))

        collector = EvolutionMetricsCollector(repo)
        metrics = await collector.collect()
        assert metrics.outcome_merged == 1
        assert metrics.outcome_rejected == 1
        assert metrics.outcome_abandoned == 1
        assert metrics.closed_count == 3

    @pytest.mark.asyncio
    async def test_auto_dispatch_count(self, repo):
        await repo.create(_make_proposal(
            risk=RiskLevel.LOW, status=ProposalStatus.DISPATCHED,
        ))
        await repo.create(_make_proposal(
            risk=RiskLevel.MEDIUM, status=ProposalStatus.DISPATCHED,
        ))

        collector = EvolutionMetricsCollector(repo)
        metrics = await collector.collect()
        assert metrics.auto_dispatch_count == 1

    @pytest.mark.asyncio
    async def test_avg_time_to_dispatch(self, repo):
        now = datetime.now(UTC)
        p = _make_proposal(
            status=ProposalStatus.DISPATCHED, created_at=now,
        )
        p.updated_at = now + timedelta(hours=2)
        await repo.create(p)

        collector = EvolutionMetricsCollector(repo)
        metrics = await collector.collect()
        assert metrics.avg_time_to_dispatch_hours is not None
        assert abs(metrics.avg_time_to_dispatch_hours - 2.0) < 0.1

    @pytest.mark.asyncio
    async def test_avg_time_to_close(self, repo):
        now = datetime.now(UTC)
        p = _make_proposal(
            status=ProposalStatus.CLOSED,
            created_at=now,
            closed_at=now + timedelta(hours=5),
        )
        await repo.create(p)

        collector = EvolutionMetricsCollector(repo)
        metrics = await collector.collect()
        assert metrics.avg_time_to_close_hours is not None
        assert abs(metrics.avg_time_to_close_hours - 5.0) < 0.1

    @pytest.mark.asyncio
    async def test_no_avg_when_no_data(self, repo):
        await repo.create(_make_proposal(status=ProposalStatus.DRAFT))

        collector = EvolutionMetricsCollector(repo)
        metrics = await collector.collect()
        assert metrics.avg_time_to_dispatch_hours is None
        assert metrics.avg_time_to_close_hours is None
