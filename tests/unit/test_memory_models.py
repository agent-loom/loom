"""EvolutionMemory 和 SkillEntry 数据模型单元测试。"""
import pytest

from agent_platform.evolution.memory_models import (
    EvolutionMemory,
    MemoryStatus,
    MemoryType,
    SkillEntry,
    SkillProvenance,
)


class TestEvolutionMemory:
    def test_default_id_prefix(self):
        mem = EvolutionMemory(agent_id="echo", type=MemoryType.PATTERN, content="test")
        assert mem.memory_id.startswith("mem_")

    def test_default_values(self):
        mem = EvolutionMemory(agent_id="echo", type=MemoryType.PATTERN, content="test")
        assert mem.confidence == 0.7
        assert mem.trust_score == 0.5
        assert mem.status == MemoryStatus.ACTIVE
        assert mem.tenant_id == "default"
        assert mem.use_count == 0

    def test_record_feedback_helpful(self):
        mem = EvolutionMemory(agent_id="echo", type=MemoryType.PATTERN, content="test")
        initial = mem.trust_score
        mem.record_feedback(helpful=True)
        assert mem.helpful_count == 1
        assert mem.trust_score == pytest.approx(initial + 0.05)

    def test_record_feedback_unhelpful(self):
        mem = EvolutionMemory(agent_id="echo", type=MemoryType.PATTERN, content="test")
        initial = mem.trust_score
        mem.record_feedback(helpful=False)
        assert mem.unhelpful_count == 1
        assert mem.trust_score == pytest.approx(initial - 0.10)

    def test_trust_score_capped_at_1(self):
        mem = EvolutionMemory(
            agent_id="echo", type=MemoryType.PATTERN, content="test", trust_score=0.98,
        )
        mem.record_feedback(helpful=True)
        assert mem.trust_score == 1.0

    def test_trust_score_floor_at_0(self):
        mem = EvolutionMemory(
            agent_id="echo", type=MemoryType.PATTERN, content="test", trust_score=0.05,
        )
        mem.record_feedback(helpful=False)
        assert mem.trust_score == 0.0

    def test_all_memory_types(self):
        for mt in MemoryType:
            mem = EvolutionMemory(agent_id="echo", type=mt, content="test")
            assert mem.type == mt

    def test_confidence_range(self):
        with pytest.raises(Exception):
            EvolutionMemory(agent_id="echo", type=MemoryType.PATTERN, content="test", confidence=1.5)

    def test_tags(self):
        mem = EvolutionMemory(
            agent_id="echo", type=MemoryType.PATTERN, content="test", tags=["a", "b"],
        )
        assert mem.tags == ["a", "b"]

    def test_source_proposal_id(self):
        mem = EvolutionMemory(
            agent_id="echo", type=MemoryType.FIX_RECIPE, content="fix",
            source_proposal_id="prop_123",
        )
        assert mem.source_proposal_id == "prop_123"

    def test_json_roundtrip(self):
        mem = EvolutionMemory(agent_id="echo", type=MemoryType.KNOWLEDGE, content="知识条目")
        data = mem.model_dump(mode="json")
        restored = EvolutionMemory(**data)
        assert restored.memory_id == mem.memory_id
        assert restored.content == "知识条目"


class TestSkillEntry:
    def test_default_id_prefix(self):
        skill = SkillEntry(agent_id="echo", name="test-skill", path="agents/echo/skills/test")
        assert skill.skill_id.startswith("skill_")

    def test_default_values(self):
        skill = SkillEntry(agent_id="echo", name="test-skill", path="agents/echo/skills/test")
        assert skill.provenance == SkillProvenance.USER_CREATED
        assert skill.status == MemoryStatus.ACTIVE
        assert skill.use_count == 0
        assert skill.view_count == 0

    def test_all_provenances(self):
        for p in SkillProvenance:
            skill = SkillEntry(
                agent_id="echo", name="s", path="p", provenance=p,
            )
            assert skill.provenance == p

    def test_json_roundtrip(self):
        skill = SkillEntry(
            agent_id="myj", name="促销排查", path="agents/myj/skills/promo-debug",
            provenance=SkillProvenance.EVOLUTION,
        )
        data = skill.model_dump(mode="json")
        restored = SkillEntry(**data)
        assert restored.skill_id == skill.skill_id
        assert restored.name == "促销排查"
        assert restored.provenance == SkillProvenance.EVOLUTION
