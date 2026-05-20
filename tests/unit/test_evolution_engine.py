"""EvolutionEngine 单元测试。"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_platform.evolution.engine import EvolutionEngine
from agent_platform.evolution.models import (
    EvolutionEvent,
    ProposalStatus,
    RiskLevel,
)
from agent_platform.evolution.repository import InMemoryProposalRepository


def _event(
    event_type: str = "eval_failure",
    agent_id: str = "echo",
    summary: str = "eval 准确率下降",
    **details,
) -> EvolutionEvent:
    return EvolutionEvent(
        event_type=event_type,
        agent_id=agent_id,
        summary=summary,
        details=details,
    )


@pytest.fixture
def repo() -> InMemoryProposalRepository:
    return InMemoryProposalRepository()


@pytest.fixture
def engine(repo) -> EvolutionEngine:
    return EvolutionEngine(repo=repo)


@pytest.fixture
def engine_with_plane(repo) -> tuple[EvolutionEngine, AsyncMock]:
    plane = AsyncMock()
    plane.create_work_item = AsyncMock(return_value={"id": "pw_mock_123"})
    plane.update_work_item_state = AsyncMock()
    engine = EvolutionEngine(
        repo=repo,
        plane_adapter=plane,
        plane_project_id="proj_1",
        ai_developing_state_id="state_ai_dev",
    )
    return engine, plane


class TestProcessEvent:
    @pytest.mark.asyncio
    async def test_creates_proposal(self, engine, repo):
        event = _event()
        proposal = await engine.process_event(event)
        assert proposal is not None
        assert proposal.agent_id == "echo"
        assert proposal.status == ProposalStatus.DRAFT
        stored = await repo.get(proposal.proposal_id)
        assert stored is not None

    @pytest.mark.asyncio
    async def test_proposal_has_evidence(self, engine):
        proposal = await engine.process_event(_event())
        assert len(proposal.evidence) >= 1
        assert proposal.evidence[0].summary == "eval 准确率下降"

    @pytest.mark.asyncio
    async def test_proposal_has_risk(self, engine):
        proposal = await engine.process_event(_event())
        assert proposal.risk is not None
        assert proposal.risk.level in {RiskLevel.LOW, RiskLevel.MEDIUM}

    @pytest.mark.asyncio
    async def test_eval_failure_generates_prompt_and_eval_changes(self, engine):
        proposal = await engine.process_event(_event(event_type="eval_failure"))
        paths = [c.path for c in proposal.proposed_changes]
        assert any("prompts" in p for p in paths)
        assert any("evals" in p for p in paths)

    @pytest.mark.asyncio
    async def test_tool_error_no_changes(self, engine):
        proposal = await engine.process_event(_event(event_type="tool_error"))
        assert proposal.proposed_changes == []

    @pytest.mark.asyncio
    async def test_user_feedback_generates_changes(self, engine):
        proposal = await engine.process_event(_event(event_type="user_feedback"))
        assert len(proposal.proposed_changes) > 0


class TestDedup:
    @pytest.mark.asyncio
    async def test_duplicate_returns_none(self, engine):
        event = _event()
        p1 = await engine.process_event(event)
        p2 = await engine.process_event(event)
        assert p1 is not None
        assert p2 is None

    @pytest.mark.asyncio
    async def test_different_summary_not_deduped(self, engine):
        p1 = await engine.process_event(_event(summary="问题A"))
        p2 = await engine.process_event(_event(summary="问题B"))
        assert p1 is not None
        assert p2 is not None

    @pytest.mark.asyncio
    async def test_different_agent_not_deduped(self, engine):
        p1 = await engine.process_event(_event(agent_id="echo"))
        p2 = await engine.process_event(_event(agent_id="code_review"))
        assert p1 is not None
        assert p2 is not None


class TestDispatchToPlane:
    @pytest.mark.asyncio
    async def test_dispatch_success(self, engine_with_plane, repo):
        engine, plane = engine_with_plane
        proposal = await engine.process_event(_event())
        result = await engine.dispatch_to_plane(proposal.proposal_id)
        assert result["status"] == "dispatched"
        assert result["plane_work_item_id"] == "pw_mock_123"
        stored = await repo.get(proposal.proposal_id)
        assert stored.status == ProposalStatus.DISPATCHED

    @pytest.mark.asyncio
    async def test_dispatch_not_found(self, engine_with_plane):
        engine, _ = engine_with_plane
        result = await engine.dispatch_to_plane("nonexistent")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_dispatch_already_dispatched(self, engine_with_plane, repo):
        engine, _ = engine_with_plane
        proposal = await engine.process_event(_event())
        await engine.dispatch_to_plane(proposal.proposal_id)
        result = await engine.dispatch_to_plane(proposal.proposal_id)
        assert "error" in result
        assert "already dispatched" in result["error"]

    @pytest.mark.asyncio
    async def test_dispatch_high_risk_blocked(self, repo):
        plane = AsyncMock()
        engine = EvolutionEngine(repo=repo, plane_adapter=plane, plane_project_id="p1")
        event = _event(event_type="eval_failure")
        proposal = await engine.process_event(event)
        # 强制设置 HIGH 风险
        proposal.risk.level = RiskLevel.HIGH
        result = await engine.dispatch_to_plane(proposal.proposal_id)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_dispatch_no_plane_adapter(self, engine):
        proposal = await engine.process_event(_event())
        result = await engine.dispatch_to_plane(proposal.proposal_id)
        assert "error" in result
        assert "Plane adapter" in result["error"]

    @pytest.mark.asyncio
    async def test_dispatch_plane_api_failure(self, repo):
        plane = AsyncMock()
        plane.create_work_item = AsyncMock(side_effect=Exception("网络超时"))
        engine = EvolutionEngine(repo=repo, plane_adapter=plane, plane_project_id="p1")
        proposal = await engine.process_event(_event())
        result = await engine.dispatch_to_plane(proposal.proposal_id)
        assert "error" in result
        assert "Plane API" in result["error"]


class TestAutoDispatch:
    @pytest.mark.asyncio
    async def test_auto_dispatch_low_risk(self, engine_with_plane):
        engine, plane = engine_with_plane
        proposal = await engine.process_event(_event())
        result = await engine.auto_dispatch_if_low_risk(proposal)
        if proposal.risk.level == RiskLevel.LOW:
            assert result is not None
        else:
            assert result is None

    @pytest.mark.asyncio
    async def test_no_auto_dispatch_without_plane(self, engine):
        proposal = await engine.process_event(_event())
        result = await engine.auto_dispatch_if_low_risk(proposal)
        assert result is None


class TestDismiss:
    @pytest.mark.asyncio
    async def test_dismiss(self, engine, repo):
        proposal = await engine.process_event(_event())
        await engine.dismiss(proposal.proposal_id, "不需要修复")
        stored = await repo.get(proposal.proposal_id)
        assert stored.status == ProposalStatus.DISMISSED

    @pytest.mark.asyncio
    async def test_dismiss_without_reason(self, engine, repo):
        proposal = await engine.process_event(_event())
        await engine.dismiss(proposal.proposal_id)
        stored = await repo.get(proposal.proposal_id)
        assert stored.status == ProposalStatus.DISMISSED


class TestBuildPlaneBody:
    def test_plane_body_format(self):
        from agent_platform.evolution.models import (
            Evidence,
            EvidenceType,
            ImprovementProposal,
            ProposedChange,
            RiskAssessment,
            RiskLevel,
            RootCause,
            RootCauseCategory,
            ValidationSpec,
        )

        proposal = ImprovementProposal(
            title="[echo] test",
            summary="测试摘要",
            agent_id="echo",
            risk=RiskAssessment(level=RiskLevel.LOW, reason="低风险"),
            root_cause=RootCause(
                category=RootCauseCategory.PROMPT_GAP,
                confidence=0.8,
                explanation="prompt 不足",
            ),
            evidence=[Evidence(type=EvidenceType.EVAL_FAILURE, id="e1", summary="eval 失败")],
            proposed_changes=[
                ProposedChange(type="prompt_update", path="agents/echo/prompts/x.md", description="优化"),
            ],
            validation=ValidationSpec(commands=["pytest tests/ -x"]),
        )
        body = EvolutionEngine._build_plane_body(proposal)
        assert "# Evolution Proposal" in body
        assert proposal.proposal_id in body
        assert "echo" in body
        assert "eval 失败" in body
        assert "agents/echo/prompts/x.md" in body
        assert "pytest tests/ -x" in body


class TestAutoDevFlowTransition:
    @pytest.mark.asyncio
    async def test_low_risk_auto_transitions_to_ai_dev(self, engine_with_plane):
        engine, plane = engine_with_plane
        proposal = await engine.process_event(_event())
        if proposal.risk.level == RiskLevel.LOW:
            result = await engine.dispatch_to_plane(proposal.proposal_id)
            assert result.get("auto_devflow") is True
            plane.update_work_item_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_medium_risk_no_auto_transition(self, repo):
        plane = AsyncMock()
        plane.create_work_item = AsyncMock(return_value={"id": "pw_1"})
        plane.update_work_item_state = AsyncMock()
        engine = EvolutionEngine(
            repo=repo,
            plane_adapter=plane,
            plane_project_id="p1",
            ai_developing_state_id="state_1",
        )
        proposal = await engine.process_event(_event(event_type="tool_error"))
        result = await engine.dispatch_to_plane(proposal.proposal_id)
        assert result.get("auto_devflow") is None
        plane.update_work_item_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_transition_failure_non_blocking(self, repo):
        plane = AsyncMock()
        plane.create_work_item = AsyncMock(return_value={"id": "pw_1"})
        plane.update_work_item_state = AsyncMock(side_effect=Exception("网络错误"))
        engine = EvolutionEngine(
            repo=repo,
            plane_adapter=plane,
            plane_project_id="p1",
            ai_developing_state_id="state_1",
        )
        proposal = await engine.process_event(_event())
        if proposal.risk.level == RiskLevel.LOW:
            result = await engine.dispatch_to_plane(proposal.proposal_id)
            assert result["status"] == "dispatched"
            assert result.get("auto_devflow") is None

    @pytest.mark.asyncio
    async def test_no_transition_without_state_id(self, repo):
        plane = AsyncMock()
        plane.create_work_item = AsyncMock(return_value={"id": "pw_1"})
        plane.update_work_item_state = AsyncMock()
        engine = EvolutionEngine(
            repo=repo,
            plane_adapter=plane,
            plane_project_id="p1",
        )
        proposal = await engine.process_event(_event())
        await engine.dispatch_to_plane(proposal.proposal_id)
        plane.update_work_item_state.assert_not_called()


class TestDedupWindowFix:
    """验证去重 key 不含 hour，24h 窗口内同一事件一律去重。"""

    @pytest.mark.asyncio
    async def test_same_event_different_hour_still_deduped(self, engine):
        e1 = EvolutionEvent(
            event_type="eval_failure", agent_id="echo", summary="同一问题",
            created_at=datetime(2026, 5, 20, 10, 0, tzinfo=UTC),
        )
        e2 = EvolutionEvent(
            event_type="eval_failure", agent_id="echo", summary="同一问题",
            created_at=datetime(2026, 5, 20, 11, 0, tzinfo=UTC),
        )
        p1 = await engine.process_event(e1)
        p2 = await engine.process_event(e2)
        assert p1 is not None
        assert p2 is None


class TestAutoDispatchPersistence:
    """验证 auto_dispatch_if_low_risk 正确持久化 status。"""

    @pytest.mark.asyncio
    async def test_auto_dispatch_persists_ready_status(self, repo):
        plane = AsyncMock()
        plane.create_work_item = AsyncMock(return_value={"id": "pw_1"})
        plane.update_work_item_state = AsyncMock()
        engine = EvolutionEngine(
            repo=repo,
            plane_adapter=plane,
            plane_project_id="p1",
            ai_developing_state_id="state_1",
        )
        proposal = await engine.process_event(_event())
        if proposal.risk.level == RiskLevel.LOW:
            await engine.auto_dispatch_if_low_risk(proposal)
            stored = await repo.get(proposal.proposal_id)
            assert stored.status == ProposalStatus.DISPATCHED


class TestMemoryRepoListAll:
    """验证 EvolutionMemoryRepository.list_all 覆盖所有租户。"""

    @pytest.mark.asyncio
    async def test_list_all_across_tenants(self):
        from agent_platform.evolution.memory_models import EvolutionMemory, MemoryType
        from agent_platform.evolution.memory_repository import InMemoryEvolutionMemoryRepository

        repo = InMemoryEvolutionMemoryRepository()
        await repo.create(EvolutionMemory(
            agent_id="echo", tenant_id="tenant_a", type=MemoryType.PATTERN, content="a",
        ))
        await repo.create(EvolutionMemory(
            agent_id="myj", tenant_id="tenant_b", type=MemoryType.KNOWLEDGE, content="b",
        ))
        result = await repo.list_all()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_list_all_with_type_filter(self):
        from agent_platform.evolution.memory_models import EvolutionMemory, MemoryType
        from agent_platform.evolution.memory_repository import InMemoryEvolutionMemoryRepository

        repo = InMemoryEvolutionMemoryRepository()
        await repo.create(EvolutionMemory(
            agent_id="echo", type=MemoryType.PATTERN, content="a",
        ))
        await repo.create(EvolutionMemory(
            agent_id="echo", type=MemoryType.KNOWLEDGE, content="b",
        ))
        result = await repo.list_all(memory_type=MemoryType.PATTERN)
        assert len(result) == 1
        assert result[0].type == MemoryType.PATTERN


class TestEvolutionCircuitBreakers:
    """验证 Phase 10 自进化引擎熔断与降级策略。"""

    @pytest.mark.asyncio
    async def test_manual_suspension_isolation(self, engine, repo):
        agent_id = "test_suspend_agent"

        # 1. 正常运行
        p1 = await engine.process_event(_event(agent_id=agent_id, summary="测试手动挂起1"))
        assert p1 is not None

        # 2. 手动挂起
        engine.suspend_agent(agent_id)
        assert await engine.is_agent_suspended(agent_id) is True

        p2 = await engine.process_event(_event(agent_id=agent_id, summary="测试手动挂起2"))
        assert p2 is None

        # 3. 手动恢复
        engine.resume_agent(agent_id)
        assert await engine.is_agent_suspended(agent_id) is False

        p3 = await engine.process_event(_event(agent_id=agent_id, summary="测试手动挂起3"))
        assert p3 is not None

    @pytest.mark.asyncio
    async def test_regression_auto_circuit_breaker(self, engine, repo):
        agent_id = "regression_agent"

        # 注入两次回归 outcome
        p1 = await engine.process_event(_event(agent_id=agent_id, summary="测试连续回归熔断1"))
        assert p1 is not None
        await repo.update_status(p1.proposal_id, ProposalStatus.CLOSED, outcome="introduced regression: latency spike")

        p2 = await engine.process_event(_event(agent_id=agent_id, summary="测试连续回归熔断2"))
        assert p2 is not None
        await repo.update_status(p2.proposal_id, ProposalStatus.CLOSED, outcome="introduced regression: bad accuracy")

        # 验证自动熔断
        assert await engine.is_agent_suspended(agent_id) is True

        # 下一次触发应该直接跳过并返回 None
        p3 = await engine.process_event(_event(agent_id=agent_id, summary="测试连续回归熔断3"))
        assert p3 is None

    @pytest.mark.asyncio
    async def test_consecutive_rejection_degradation(self, engine, repo):
        agent_id = "rejection_agent"

        # 连续两次被人类拒绝（dismissed）
        p1 = await engine.process_event(_event(agent_id=agent_id, summary="测试连续被拒降级1"))
        assert p1 is not None
        await repo.update_status(p1.proposal_id, ProposalStatus.DISMISSED, outcome="rejected by human reviewer")

        # 此时再来一次
        p2 = await engine.process_event(_event(agent_id=agent_id, summary="测试连续被拒降级2"))
        assert p2 is not None
        await repo.update_status(p2.proposal_id, ProposalStatus.DISMISSED, outcome="dismissed by product owner")

        # 第三次生成时，应该自动降级为需要人工确认
        p3 = await engine.process_event(_event(agent_id=agent_id, summary="测试连续被拒降级3"))
        assert p3 is not None
        assert p3.risk.requires_human_confirmation_before_devflow is True
