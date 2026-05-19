"""EvolutionMemory 和 Skill Repository 单元测试。"""
import pytest

from agent_platform.evolution.memory_models import (
    EvolutionMemory,
    MemoryStatus,
    MemoryType,
    SkillEntry,
    SkillProvenance,
)
from agent_platform.evolution.memory_repository import (
    EvolutionMemoryRepository,
    InMemoryEvolutionMemoryRepository,
    InMemorySkillRepository,
    SkillRepository,
)


def _make_memory(**overrides) -> EvolutionMemory:
    defaults = dict(agent_id="echo", tenant_id="default", type=MemoryType.PATTERN, content="测试内容")
    defaults.update(overrides)
    return EvolutionMemory(**defaults)


def _make_skill(**overrides) -> SkillEntry:
    defaults = dict(agent_id="echo", name="test-skill", path="agents/echo/skills/test")
    defaults.update(overrides)
    return SkillEntry(**defaults)


class TestEvolutionMemoryRepository:
    def test_implements_protocol(self):
        assert isinstance(InMemoryEvolutionMemoryRepository(), EvolutionMemoryRepository)

    @pytest.mark.asyncio
    async def test_create_and_get(self):
        repo = InMemoryEvolutionMemoryRepository()
        mem = _make_memory()
        await repo.create(mem)
        got = await repo.get(mem.memory_id)
        assert got is not None
        assert got.content == "测试内容"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self):
        repo = InMemoryEvolutionMemoryRepository()
        assert await repo.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_list_by_agent(self):
        repo = InMemoryEvolutionMemoryRepository()
        await repo.create(_make_memory(agent_id="echo"))
        await repo.create(_make_memory(agent_id="echo"))
        await repo.create(_make_memory(agent_id="myj"))
        result = await repo.list_by_agent("echo")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_list_by_agent_with_type_filter(self):
        repo = InMemoryEvolutionMemoryRepository()
        await repo.create(_make_memory(agent_id="echo", type=MemoryType.PATTERN))
        await repo.create(_make_memory(agent_id="echo", type=MemoryType.CONSTRAINT))
        result = await repo.list_by_agent("echo", memory_type=MemoryType.PATTERN)
        assert len(result) == 1
        assert result[0].type == MemoryType.PATTERN

    @pytest.mark.asyncio
    async def test_list_by_agent_with_status_filter(self):
        repo = InMemoryEvolutionMemoryRepository()
        m1 = _make_memory(agent_id="echo")
        m2 = _make_memory(agent_id="echo", status=MemoryStatus.STALE)
        await repo.create(m1)
        await repo.create(m2)
        result = await repo.list_by_agent("echo", status=MemoryStatus.ACTIVE)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_list_by_tenant(self):
        repo = InMemoryEvolutionMemoryRepository()
        await repo.create(_make_memory(tenant_id="tenant_a"))
        await repo.create(_make_memory(tenant_id="tenant_a"))
        await repo.create(_make_memory(tenant_id="tenant_b"))
        result = await repo.list_by_tenant("tenant_a")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_list_limit(self):
        repo = InMemoryEvolutionMemoryRepository()
        for _ in range(10):
            await repo.create(_make_memory())
        result = await repo.list_by_agent("echo", limit=3)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_list_sorted_by_created_at_desc(self):
        repo = InMemoryEvolutionMemoryRepository()
        m1 = _make_memory(content="first")
        m2 = _make_memory(content="second")
        await repo.create(m1)
        await repo.create(m2)
        result = await repo.list_by_agent("echo")
        assert result[0].created_at >= result[1].created_at

    @pytest.mark.asyncio
    async def test_update(self):
        repo = InMemoryEvolutionMemoryRepository()
        mem = _make_memory()
        await repo.create(mem)
        mem.content = "更新后内容"
        await repo.update(mem)
        got = await repo.get(mem.memory_id)
        assert got.content == "更新后内容"

    @pytest.mark.asyncio
    async def test_update_nonexistent_is_noop(self):
        repo = InMemoryEvolutionMemoryRepository()
        mem = _make_memory()
        await repo.update(mem)

    @pytest.mark.asyncio
    async def test_delete(self):
        repo = InMemoryEvolutionMemoryRepository()
        mem = _make_memory()
        await repo.create(mem)
        deleted = await repo.delete(mem.memory_id)
        assert deleted is True
        assert await repo.get(mem.memory_id) is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self):
        repo = InMemoryEvolutionMemoryRepository()
        assert await repo.delete("nonexistent") is False

    @pytest.mark.asyncio
    async def test_delete_removes_from_indexes(self):
        repo = InMemoryEvolutionMemoryRepository()
        mem = _make_memory()
        await repo.create(mem)
        await repo.delete(mem.memory_id)
        result = await repo.list_by_agent("echo")
        assert len(result) == 0
        result = await repo.list_by_tenant("default")
        assert len(result) == 0


class TestSkillRepository:
    def test_implements_protocol(self):
        assert isinstance(InMemorySkillRepository(), SkillRepository)

    @pytest.mark.asyncio
    async def test_create_and_get(self):
        repo = InMemorySkillRepository()
        skill = _make_skill()
        await repo.create(skill)
        got = await repo.get(skill.skill_id)
        assert got is not None
        assert got.name == "test-skill"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self):
        repo = InMemorySkillRepository()
        assert await repo.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_list_by_agent(self):
        repo = InMemorySkillRepository()
        await repo.create(_make_skill(agent_id="echo", name="s1", path="p1"))
        await repo.create(_make_skill(agent_id="echo", name="s2", path="p2"))
        await repo.create(_make_skill(agent_id="myj", name="s3", path="p3"))
        result = await repo.list_by_agent("echo")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_list_by_agent_with_status(self):
        repo = InMemorySkillRepository()
        await repo.create(_make_skill(agent_id="echo", name="s1", path="p1"))
        s2 = _make_skill(agent_id="echo", name="s2", path="p2", status=MemoryStatus.ARCHIVED)
        await repo.create(s2)
        result = await repo.list_by_agent("echo", status=MemoryStatus.ACTIVE)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_list_all(self):
        repo = InMemorySkillRepository()
        await repo.create(_make_skill(agent_id="echo", name="s1", path="p1"))
        await repo.create(_make_skill(agent_id="myj", name="s2", path="p2"))
        result = await repo.list_all()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_update(self):
        repo = InMemorySkillRepository()
        skill = _make_skill()
        await repo.create(skill)
        skill.description = "更新描述"
        await repo.update(skill)
        got = await repo.get(skill.skill_id)
        assert got.description == "更新描述"

    @pytest.mark.asyncio
    async def test_delete(self):
        repo = InMemorySkillRepository()
        skill = _make_skill()
        await repo.create(skill)
        deleted = await repo.delete(skill.skill_id)
        assert deleted is True
        assert await repo.get(skill.skill_id) is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self):
        repo = InMemorySkillRepository()
        assert await repo.delete("nonexistent") is False

    @pytest.mark.asyncio
    async def test_delete_removes_from_index(self):
        repo = InMemorySkillRepository()
        skill = _make_skill()
        await repo.create(skill)
        await repo.delete(skill.skill_id)
        result = await repo.list_by_agent("echo")
        assert len(result) == 0
