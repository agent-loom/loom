"""Skill Scanner 单元测试。"""
import pytest
from pathlib import Path

from agent_platform.evolution.memory_models import SkillProvenance
from agent_platform.evolution.memory_repository import InMemorySkillRepository
from agent_platform.evolution.skill_scanner import (
    scan_agent_skills,
    sync_skills_to_repo,
)


@pytest.fixture
def agents_dir(tmp_path):
    """创建临时 agents 目录结构用于测试。"""
    echo_skills = tmp_path / "echo" / "skills" / "greeting"
    echo_skills.mkdir(parents=True)
    (echo_skills / "manifest.yaml").write_text(
        "skill_id: greeting\n"
        "title: 问候技能\n"
        "description: 简单的问候技能\n"
        "version: 0.1.0\n"
        "risk_level: low\n"
        "status: active\n"
        "tags:\n  - demo\n  - greeting\n"
    )

    echo_debug = tmp_path / "echo" / "skills" / "debug-helper"
    echo_debug.mkdir(parents=True)
    (echo_debug / "manifest.yaml").write_text(
        "skill_id: debug-helper\n"
        "title: 调试辅助\n"
        "description: 帮助调试的技能\n"
        "version: 0.2.0\n"
        "created_by: evolution\n"
    )

    myj_skills = tmp_path / "myj" / "skills" / "promo-debug"
    myj_skills.mkdir(parents=True)
    (myj_skills / "manifest.yaml").write_text(
        "skill_id: promo-debug\n"
        "title: 促销排查\n"
        "description: 促销问题排查流程\n"
        "version: 0.1.0\n"
    )

    # agent 没有 skills 目录
    (tmp_path / "no-skills-agent").mkdir()
    (tmp_path / "no-skills-agent" / "manifest.yaml").write_text("kind: AgentPackage\n")

    return tmp_path


class TestScanAgentSkills:
    def test_scans_all_agents(self, agents_dir):
        entries = scan_agent_skills(agents_dir)
        assert len(entries) == 3

    def test_entry_fields(self, agents_dir):
        entries = scan_agent_skills(agents_dir)
        greeting = next(e for e in entries if e.name == "greeting")
        assert greeting.agent_id == "echo"
        assert greeting.description == "简单的问候技能"
        assert "demo" in greeting.tags
        assert greeting.metadata["version"] == "0.1.0"

    def test_provenance_from_manifest(self, agents_dir):
        entries = scan_agent_skills(agents_dir)
        debug = next(e for e in entries if e.name == "debug-helper")
        assert debug.provenance == SkillProvenance.EVOLUTION

    def test_default_provenance(self, agents_dir):
        entries = scan_agent_skills(agents_dir)
        greeting = next(e for e in entries if e.name == "greeting")
        assert greeting.provenance == SkillProvenance.USER_CREATED

    def test_nonexistent_dir(self):
        entries = scan_agent_skills(Path("/nonexistent"))
        assert entries == []

    def test_skips_hidden_dirs(self, agents_dir):
        hidden = agents_dir / ".hidden" / "skills" / "test"
        hidden.mkdir(parents=True)
        (hidden / "manifest.yaml").write_text("skill_id: hidden\ntitle: hidden\n")
        entries = scan_agent_skills(agents_dir)
        assert not any(e.name == "hidden" for e in entries)

    def test_skips_agent_without_skills_dir(self, agents_dir):
        entries = scan_agent_skills(agents_dir)
        assert not any(e.agent_id == "no-skills-agent" for e in entries)

    def test_handles_invalid_yaml(self, agents_dir):
        bad_skill = agents_dir / "echo" / "skills" / "bad"
        bad_skill.mkdir(parents=True)
        (bad_skill / "manifest.yaml").write_text(": invalid: yaml: [")
        entries = scan_agent_skills(agents_dir)
        assert len(entries) == 3

    def test_path_relative(self, agents_dir):
        entries = scan_agent_skills(agents_dir)
        for e in entries:
            assert not e.path.startswith("/")


class TestSyncSkillsToRepo:
    @pytest.mark.asyncio
    async def test_creates_new_skills(self, agents_dir):
        repo = InMemorySkillRepository()
        result = await sync_skills_to_repo(agents_dir, repo)
        assert result["scanned"] == 3
        assert result["created"] == 3
        assert result["updated"] == 0

    @pytest.mark.asyncio
    async def test_updates_existing_skills(self, agents_dir):
        repo = InMemorySkillRepository()
        await sync_skills_to_repo(agents_dir, repo)
        result = await sync_skills_to_repo(agents_dir, repo)
        assert result["created"] == 0
        assert result["updated"] == 3

    @pytest.mark.asyncio
    async def test_idempotent(self, agents_dir):
        repo = InMemorySkillRepository()
        await sync_skills_to_repo(agents_dir, repo)
        await sync_skills_to_repo(agents_dir, repo)
        skills = await repo.list_all()
        assert len(skills) == 3
