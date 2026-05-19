"""InMemoryProposalRepository 单元测试。"""
import pytest

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


def _make_proposal(agent_id: str = "echo", **overrides) -> ImprovementProposal:
    defaults = {
        "title": f"[{agent_id}] test",
        "summary": "test summary",
        "agent_id": agent_id,
        "risk": RiskAssessment(level=RiskLevel.LOW, reason="test"),
        "root_cause": RootCause(
            category=RootCauseCategory.PROMPT_GAP,
            confidence=0.8,
            explanation="test",
        ),
        "evidence": [
            Evidence(type=EvidenceType.EVAL_FAILURE, id="e1", summary="test"),
        ],
    }
    defaults.update(overrides)
    return ImprovementProposal(**defaults)


@pytest.fixture
def repo() -> InMemoryProposalRepository:
    return InMemoryProposalRepository()


class TestCreate:
    @pytest.mark.asyncio
    async def test_create_and_get(self, repo):
        p = _make_proposal()
        await repo.create(p)
        got = await repo.get(p.proposal_id)
        assert got is not None
        assert got.proposal_id == p.proposal_id
        assert got.title == p.title

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, repo):
        got = await repo.get("nonexistent")
        assert got is None


class TestListByAgent:
    @pytest.mark.asyncio
    async def test_list_by_agent(self, repo):
        p1 = _make_proposal("echo")
        p2 = _make_proposal("code_review")
        p3 = _make_proposal("echo")
        await repo.create(p1)
        await repo.create(p2)
        await repo.create(p3)
        results = await repo.list_by_agent("echo")
        assert len(results) == 2
        assert all(p.agent_id == "echo" for p in results)

    @pytest.mark.asyncio
    async def test_list_by_agent_with_status_filter(self, repo):
        p1 = _make_proposal("echo")
        p2 = _make_proposal("echo")
        await repo.create(p1)
        await repo.create(p2)
        await repo.update_status(p2.proposal_id, ProposalStatus.READY)
        results = await repo.list_by_agent("echo", status=ProposalStatus.DRAFT)
        assert len(results) == 1
        assert results[0].proposal_id == p1.proposal_id

    @pytest.mark.asyncio
    async def test_list_by_agent_limit(self, repo):
        for _ in range(10):
            await repo.create(_make_proposal("echo"))
        results = await repo.list_by_agent("echo", limit=3)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_list_by_agent_empty(self, repo):
        results = await repo.list_by_agent("nonexistent")
        assert results == []


class TestListAll:
    @pytest.mark.asyncio
    async def test_list_all(self, repo):
        await repo.create(_make_proposal("echo"))
        await repo.create(_make_proposal("code_review"))
        results = await repo.list_all()
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_list_all_with_status_filter(self, repo):
        p1 = _make_proposal()
        p2 = _make_proposal()
        await repo.create(p1)
        await repo.create(p2)
        await repo.update_status(p1.proposal_id, ProposalStatus.DISPATCHED, plane_work_item_id="pw_1")
        results = await repo.list_all(status=ProposalStatus.DISPATCHED)
        assert len(results) == 1
        assert results[0].proposal_id == p1.proposal_id

    @pytest.mark.asyncio
    async def test_list_all_limit(self, repo):
        for _ in range(10):
            await repo.create(_make_proposal())
        results = await repo.list_all(limit=5)
        assert len(results) == 5

    @pytest.mark.asyncio
    async def test_list_all_sorted_by_created_at_desc(self, repo):
        p1 = _make_proposal()
        p2 = _make_proposal()
        await repo.create(p1)
        await repo.create(p2)
        results = await repo.list_all()
        assert results[0].created_at >= results[1].created_at


class TestUpdateStatus:
    @pytest.mark.asyncio
    async def test_update_to_dispatched(self, repo):
        p = _make_proposal()
        await repo.create(p)
        await repo.update_status(p.proposal_id, ProposalStatus.DISPATCHED, plane_work_item_id="pw_123")
        got = await repo.get(p.proposal_id)
        assert got.status == ProposalStatus.DISPATCHED
        assert got.plane_work_item_id == "pw_123"

    @pytest.mark.asyncio
    async def test_update_to_closed(self, repo):
        p = _make_proposal()
        await repo.create(p)
        await repo.update_status(p.proposal_id, ProposalStatus.CLOSED, outcome="已修复")
        got = await repo.get(p.proposal_id)
        assert got.status == ProposalStatus.CLOSED
        assert got.closed_at is not None
        assert got.outcome == "已修复"

    @pytest.mark.asyncio
    async def test_update_to_dismissed(self, repo):
        p = _make_proposal()
        await repo.create(p)
        await repo.update_status(p.proposal_id, ProposalStatus.DISMISSED)
        got = await repo.get(p.proposal_id)
        assert got.status == ProposalStatus.DISMISSED

    @pytest.mark.asyncio
    async def test_update_nonexistent_is_noop(self, repo):
        await repo.update_status("nonexistent", ProposalStatus.READY)

    @pytest.mark.asyncio
    async def test_updated_at_changes(self, repo):
        p = _make_proposal()
        await repo.create(p)
        original = p.updated_at
        await repo.update_status(p.proposal_id, ProposalStatus.READY)
        got = await repo.get(p.proposal_id)
        assert got.updated_at >= original
