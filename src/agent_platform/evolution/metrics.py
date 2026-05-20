"""自进化系统指标统计。"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from agent_platform.evolution.models import (
    ImprovementProposal,
    ProposalStatus,
    RiskLevel,
)
from agent_platform.evolution.repository import EvolutionProposalRepository


@dataclass(frozen=True)
class EvolutionMetrics:
    """自进化系统的汇总指标。"""

    total_proposals: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    by_risk: dict[str, int] = field(default_factory=dict)
    by_agent: dict[str, int] = field(default_factory=dict)
    by_root_cause: dict[str, int] = field(default_factory=dict)
    dispatched_count: int = 0
    dismissed_count: int = 0
    closed_count: int = 0
    auto_dispatch_count: int = 0
    outcome_merged: int = 0
    outcome_rejected: int = 0
    outcome_abandoned: int = 0
    avg_time_to_dispatch_hours: float | None = None
    avg_time_to_close_hours: float | None = None


class EvolutionMetricsCollector:
    """从 ProposalRepository 收集指标快照。"""

    def __init__(self, repo: EvolutionProposalRepository) -> None:
        self._repo = repo

    async def collect(self, *, limit: int = 500) -> EvolutionMetrics:
        proposals = await self._repo.list_all(limit=limit)
        if not proposals:
            return EvolutionMetrics()

        status_counter: Counter[str] = Counter()
        risk_counter: Counter[str] = Counter()
        agent_counter: Counter[str] = Counter()
        root_cause_counter: Counter[str] = Counter()

        dispatched = 0
        dismissed = 0
        closed = 0
        auto_dispatch = 0
        outcome_merged = 0
        outcome_rejected = 0
        outcome_abandoned = 0

        dispatch_deltas: list[float] = []
        close_deltas: list[float] = []

        for p in proposals:
            status_counter[p.status.value] += 1
            risk_counter[p.risk.level.value] += 1
            agent_counter[p.agent_id] += 1
            if p.root_cause:
                root_cause_counter[p.root_cause.category.value] += 1

            if p.status == ProposalStatus.DISPATCHED:
                dispatched += 1
            elif p.status == ProposalStatus.DISMISSED:
                dismissed += 1
            elif p.status == ProposalStatus.CLOSED:
                closed += 1

            if (
                p.risk.level == RiskLevel.LOW
                and p.status in {ProposalStatus.DISPATCHED, ProposalStatus.CLOSED}
            ):
                auto_dispatch += 1

            if p.outcome == "merged":
                outcome_merged += 1
            elif p.outcome == "rejected":
                outcome_rejected += 1
            elif p.outcome == "abandoned":
                outcome_abandoned += 1

            if p.status in {ProposalStatus.DISPATCHED, ProposalStatus.CLOSED}:
                delta = (p.updated_at - p.created_at).total_seconds() / 3600
                dispatch_deltas.append(delta)

            if p.closed_at:
                delta = (p.closed_at - p.created_at).total_seconds() / 3600
                close_deltas.append(delta)

        return EvolutionMetrics(
            total_proposals=len(proposals),
            by_status=dict(status_counter),
            by_risk=dict(risk_counter),
            by_agent=dict(agent_counter),
            by_root_cause=dict(root_cause_counter),
            dispatched_count=dispatched,
            dismissed_count=dismissed,
            closed_count=closed,
            auto_dispatch_count=auto_dispatch,
            outcome_merged=outcome_merged,
            outcome_rejected=outcome_rejected,
            outcome_abandoned=outcome_abandoned,
            avg_time_to_dispatch_hours=(
                sum(dispatch_deltas) / len(dispatch_deltas) if dispatch_deltas else None
            ),
            avg_time_to_close_hours=(
                sum(close_deltas) / len(close_deltas) if close_deltas else None
            ),
        )
