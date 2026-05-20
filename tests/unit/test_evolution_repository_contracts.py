"""S9 Phase 10: 自进化与记忆相关 Repository（InMemory 和 SQL 实现）契约一致性测试。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.evolution.models import (
    Candidate,
    CandidateStatus,
    CandidateType,
    PromotionTarget,
    RiskLevel,
)
from agent_platform.evolution.memory_models import (
    EvolutionMemory,
    MemoryStatus,
    MemoryType,
    RuntimeMemory,
    RuntimeMemoryScope,
    RuntimeMemoryType,
    SkillEntry,
    SkillProvenance,
)
from agent_platform.evolution.review_fork import (
    ReviewForkAudit,
)

# 内存实现
from agent_platform.evolution.memory_repository import (
    InMemoryEvolutionMemoryRepository,
    InMemoryRuntimeMemoryRepository,
    InMemorySkillRepository,
)
from agent_platform.evolution.repository import (
    InMemoryCandidateRepository,
)
from agent_platform.evolution.review_fork import (
    InMemoryReviewForkAuditRepository,
)

# SQL 实现
from agent_platform.persistence.sql import (
    SqlEvolutionMemoryRepository,
    SqlRuntimeMemoryRepository,
    SqlSkillRepository,
    SqlCandidateRepository,
    SqlReviewForkAuditRepository,
)

from agent_platform.storage.base import Base


async def _sql_session_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(params=["memory", "sql"])
async def runtime_memory_repo(request):
    if request.param == "memory":
        return InMemoryRuntimeMemoryRepository()
    sf = await _sql_session_factory()
    return SqlRuntimeMemoryRepository(sf)


@pytest_asyncio.fixture(params=["memory", "sql"])
async def evolution_memory_repo(request):
    if request.param == "memory":
        return InMemoryEvolutionMemoryRepository()
    sf = await _sql_session_factory()
    return SqlEvolutionMemoryRepository(sf)


@pytest_asyncio.fixture(params=["memory", "sql"])
async def skill_repo(request):
    if request.param == "memory":
        return InMemorySkillRepository()
    sf = await _sql_session_factory()
    return SqlSkillRepository(sf)


@pytest_asyncio.fixture(params=["memory", "sql"])
async def candidate_repo(request):
    if request.param == "memory":
        return InMemoryCandidateRepository()
    sf = await _sql_session_factory()
    return SqlCandidateRepository(sf)


@pytest_asyncio.fixture(params=["memory", "sql"])
async def audit_repo(request):
    if request.param == "memory":
        return InMemoryReviewForkAuditRepository()
    sf = await _sql_session_factory()
    return SqlReviewForkAuditRepository(sf)


# ---------------------------------------------------------------------------
# 1. RuntimeMemoryRepository
# ---------------------------------------------------------------------------

class TestRuntimeMemoryContract:
    @pytest.mark.asyncio
    async def test_runtime_memory_lifecycle(self, runtime_memory_repo):
        m1 = RuntimeMemory(
            agent_id="agent-1",
            tenant_id="t1",
            scope=RuntimeMemoryScope.USER,
            subject_id="u1",
            type=RuntimeMemoryType.PREFERENCE,
            content="喜欢中文",
        )
        await runtime_memory_repo.create(m1)

        # 1. get
        ret = await runtime_memory_repo.get(m1.memory_id)
        assert ret is not None
        assert ret.content == "喜欢中文"
        assert ret.tenant_id == "t1"

        # 2. list_by_agent
        mems = await runtime_memory_repo.list_by_agent("agent-1")
        assert len(mems) == 1
        assert mems[0].memory_id == m1.memory_id

        # 3. list_by_user
        mems = await runtime_memory_repo.list_by_user("u1")
        assert len(mems) == 1

        # 4. update
        m1.content = "改为喜欢英文"
        await runtime_memory_repo.update(m1)
        ret = await runtime_memory_repo.get(m1.memory_id)
        assert ret.content == "改为喜欢英文"

        # 5. delete
        deleted = await runtime_memory_repo.delete(m1.memory_id)
        assert deleted is True
        assert await runtime_memory_repo.get(m1.memory_id) is None

    @pytest.mark.asyncio
    async def test_runtime_memory_expiration(self, runtime_memory_repo):
        # 写入已过期的
        expired = RuntimeMemory(
            agent_id="agent-1",
            tenant_id="t1",
            scope=RuntimeMemoryScope.SESSION,
            session_id="sess-1",
            type=RuntimeMemoryType.SESSION_SUMMARY,
            content="过期的记忆",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        await runtime_memory_repo.create(expired)

        # get 和 list 时都不应该读出来
        assert await runtime_memory_repo.get(expired.memory_id) is None
        mems = await runtime_memory_repo.list_by_session("sess-1")
        assert len(mems) == 0


# ---------------------------------------------------------------------------
# 2. EvolutionMemoryRepository
# ---------------------------------------------------------------------------

class TestEvolutionMemoryContract:
    @pytest.mark.asyncio
    async def test_evolution_memory_lifecycle(self, evolution_memory_repo):
        m = EvolutionMemory(
            agent_id="agent-2",
            tenant_id="t2",
            type=MemoryType.PATTERN,
            content="高频失败模式说明",
            confidence=0.9,
            trust_score=0.6,
        )
        await evolution_memory_repo.create(m)

        ret = await evolution_memory_repo.get(m.memory_id)
        assert ret is not None
        assert ret.content == "高频失败模式说明"

        mems = await evolution_memory_repo.list_by_agent("agent-2")
        assert len(mems) == 1
        assert mems[0].memory_id == m.memory_id

        mems_all = await evolution_memory_repo.list_all()
        assert len(mems_all) == 1

        # update
        m.record_feedback(helpful=True)  # 提升信誉分
        await evolution_memory_repo.update(m)
        ret = await evolution_memory_repo.get(m.memory_id)
        assert ret.helpful_count == 1
        assert ret.trust_score > 0.6

        # delete
        deleted = await evolution_memory_repo.delete(m.memory_id)
        assert deleted is True
        assert await evolution_memory_repo.get(m.memory_id) is None


# ---------------------------------------------------------------------------
# 3. SkillRepository
# ---------------------------------------------------------------------------

class TestSkillContract:
    @pytest.mark.asyncio
    async def test_skill_lifecycle(self, skill_repo):
        s = SkillEntry(
            agent_id="agent-3",
            name="math_helper",
            description="数学助手",
            path="skills/math_helper/manifest.yaml",
            provenance=SkillProvenance.EVOLUTION,
        )
        await skill_repo.create(s)

        ret = await skill_repo.get(s.skill_id)
        assert ret is not None
        assert ret.name == "math_helper"

        skills = await skill_repo.list_by_agent("agent-3")
        assert len(skills) == 1
        assert skills[0].skill_id == s.skill_id

        # update
        s.use_count = 5
        await skill_repo.update(s)
        ret = await skill_repo.get(s.skill_id)
        assert ret.use_count == 5

        # delete
        deleted = await skill_repo.delete(s.skill_id)
        assert deleted is True
        assert await skill_repo.get(s.skill_id) is None


# ---------------------------------------------------------------------------
# 4. CandidateRepository
# ---------------------------------------------------------------------------

class TestCandidateContract:
    @pytest.mark.asyncio
    async def test_candidate_lifecycle(self, candidate_repo):
        c = Candidate(
            candidate_type=CandidateType.MEMORY_CANDIDATE,
            agent_id="agent-4",
            tenant_id="t4",
            payload={"summary": "测试候选记忆"},
            risk_level=RiskLevel.LOW,
            status=CandidateStatus.DRAFT,
            promotion_target=PromotionTarget.EVOLUTION_MEMORY,
        )
        await candidate_repo.create(c)

        ret = await candidate_repo.get(c.candidate_id)
        assert ret is not None
        assert ret.payload["summary"] == "测试候选记忆"

        cands = await candidate_repo.list_all(agent_id="agent-4")
        assert len(cands) == 1

        # update_status
        await candidate_repo.update_status(
            c.candidate_id,
            CandidateStatus.PROMOTED,
            validation_errors=["no errors"],
        )
        ret = await candidate_repo.get(c.candidate_id)
        assert ret.status == CandidateStatus.PROMOTED
        assert ret.promoted_at is not None
        assert ret.validation_errors == ["no errors"]

        # delete
        await candidate_repo.delete(c.candidate_id)
        assert await candidate_repo.get(c.candidate_id) is None


# ---------------------------------------------------------------------------
# 5. ReviewForkAuditRepository
# ---------------------------------------------------------------------------

class TestReviewForkAuditContract:
    @pytest.mark.asyncio
    async def test_audit_lifecycle(self, audit_repo):
        a = ReviewForkAudit(
            source_event_id="evt-100",
            source_event_type="agent_run_completed",
            agent_id="agent-5",
            tenant_id="t5",
            input_evidence_ids=["evt-100"],
            status="success",
        )
        await audit_repo.create(a)

        ret = await audit_repo.get(a.review_fork_id)
        assert ret is not None
        assert ret.status == "success"

        audits = await audit_repo.list_all(agent_id="agent-5")
        assert len(audits) == 1
        assert audits[0].review_fork_id == a.review_fork_id
