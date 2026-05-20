"""ImprovementProposal 持久化：Protocol + InMemory 实现。"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from .models import (
    Candidate,
    CandidateStatus,
    CandidateType,
    ImprovementProposal,
    ProposalStatus,
)


@runtime_checkable
class EvolutionProposalRepository(Protocol):
    async def create(self, proposal: ImprovementProposal) -> None: ...
    async def get(self, proposal_id: str) -> ImprovementProposal | None: ...
    async def list_by_agent(
        self,
        agent_id: str,
        status: ProposalStatus | None = None,
        limit: int = 50,
    ) -> list[ImprovementProposal]: ...
    async def list_all(
        self,
        status: ProposalStatus | None = None,
        limit: int = 100,
    ) -> list[ImprovementProposal]: ...
    async def update_status(
        self,
        proposal_id: str,
        status: ProposalStatus,
        **kwargs: object,
    ) -> None: ...


class InMemoryProposalRepository:
    def __init__(self) -> None:
        self._store: dict[str, ImprovementProposal] = {}
        self._by_agent: dict[str, list[str]] = defaultdict(list)

    async def create(self, proposal: ImprovementProposal) -> None:
        self._store[proposal.proposal_id] = proposal
        self._by_agent[proposal.agent_id].append(proposal.proposal_id)

    async def get(self, proposal_id: str) -> ImprovementProposal | None:
        return self._store.get(proposal_id)

    async def list_by_agent(
        self,
        agent_id: str,
        status: ProposalStatus | None = None,
        limit: int = 50,
    ) -> list[ImprovementProposal]:
        ids = self._by_agent.get(agent_id, [])
        result = [self._store[pid] for pid in ids if pid in self._store]
        if status is not None:
            result = [p for p in result if p.status == status]
        return sorted(result, key=lambda p: p.created_at, reverse=True)[:limit]

    async def list_all(
        self,
        status: ProposalStatus | None = None,
        limit: int = 100,
    ) -> list[ImprovementProposal]:
        result = list(self._store.values())
        if status is not None:
            result = [p for p in result if p.status == status]
        return sorted(result, key=lambda p: p.created_at, reverse=True)[:limit]

    async def update_status(
        self,
        proposal_id: str,
        status: ProposalStatus,
        **kwargs: object,
    ) -> None:
        proposal = self._store.get(proposal_id)
        if proposal is None:
            return
        proposal.status = status
        proposal.updated_at = datetime.now(UTC)
        if status == ProposalStatus.DISPATCHED:
            plane_work_item_id = kwargs.get("plane_work_item_id")
            if plane_work_item_id:
                proposal.plane_work_item_id = str(plane_work_item_id)
        elif status == ProposalStatus.CLOSED:
            proposal.closed_at = datetime.now(UTC)
            outcome = kwargs.get("outcome")
            if outcome:
                proposal.outcome = str(outcome)


# ---------------------------------------------------------------------------
# Candidate 持久化仓储
# ---------------------------------------------------------------------------


@runtime_checkable
class CandidateRepository(Protocol):
    async def create(self, candidate: Candidate) -> None: ...
    async def get(self, candidate_id: str) -> Candidate | None: ...
    async def list_all(
        self,
        *,
        candidate_type: CandidateType | None = None,
        agent_id: str | None = None,
        status: CandidateStatus | None = None,
        limit: int = 100,
    ) -> list[Candidate]: ...
    async def update_status(
        self,
        candidate_id: str,
        status: CandidateStatus,
        *,
        validation_errors: list[str] | None = None,
    ) -> None: ...
    async def delete(self, candidate_id: str) -> None: ...


class InMemoryCandidateRepository:
    def __init__(self) -> None:
        self._store: dict[str, Candidate] = {}

    async def create(self, candidate: Candidate) -> None:
        self._store[candidate.candidate_id] = candidate

    async def get(self, candidate_id: str) -> Candidate | None:
        return self._store.get(candidate_id)

    async def list_all(
        self,
        *,
        candidate_type: CandidateType | None = None,
        agent_id: str | None = None,
        status: CandidateStatus | None = None,
        limit: int = 100,
    ) -> list[Candidate]:
        result = list(self._store.values())
        if candidate_type is not None:
            result = [c for c in result if c.candidate_type == candidate_type]
        if agent_id is not None:
            result = [c for c in result if c.agent_id == agent_id]
        if status is not None:
            result = [c for c in result if c.status == status]
        return sorted(result, key=lambda c: c.created_at, reverse=True)[:limit]

    async def update_status(
        self,
        candidate_id: str,
        status: CandidateStatus,
        *,
        validation_errors: list[str] | None = None,
    ) -> None:
        candidate = self._store.get(candidate_id)
        if candidate is None:
            return
        candidate.status = status
        candidate.updated_at = datetime.now(UTC)
        if status == CandidateStatus.PROMOTED:
            candidate.promoted_at = datetime.now(UTC)
        if validation_errors is not None:
            candidate.validation_errors = validation_errors

    async def delete(self, candidate_id: str) -> None:
        if candidate_id in self._store:
            del self._store[candidate_id]

