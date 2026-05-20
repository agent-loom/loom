"""S9 Phase 9: RuntimeMemory 与 Skill 注入单元测试。"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
import pytest
import yaml

from agent_platform.domain.models import (
    AgentInput,
    AgentManifest,
    AgentRequest,
    AgentSpec,
    ChannelContext,
    ManifestMetadata,
    ManifestOutput,
    ManifestSession,
    ManifestTools,
    ManifestVersion,
    RequestContext,
    SessionMessage,
    StoreContext,
    TenantContext,
    UserContext,
)
from agent_platform.evolution.memory_models import (
    MemoryStatus,
    RuntimeMemory,
    RuntimeMemoryScope,
    RuntimeMemoryType,
    SkillEntry,
    SkillProvenance,
)
from agent_platform.evolution.memory_repository import (
    InMemoryRuntimeMemoryRepository,
    InMemorySkillRepository,
)
from agent_platform.evolution.skill_selector import SkillSelector
from agent_platform.runtime.context_builder import ContextBuilder


def _make_spec(
    agent_id: str = "test-agent",
    prompts: dict[str, str] | None = None,
    tools_allow: list[str] | None = None,
    history_window: int = 20,
    package_path: Path = Path("/tmp/test-agent"),
) -> AgentSpec:
    return AgentSpec(
        manifest=AgentManifest(
            api_version="agent.platform/v1",
            kind="AgentPackage",
            metadata=ManifestMetadata(id=agent_id, name="Test Agent"),
            version=ManifestVersion(package_version="0.1.0"),
            prompts=prompts or {},
            tools=ManifestTools(allow=tools_allow or []),
            session=ManifestSession(history_window=history_window),
            output=ManifestOutput(),
        ),
        package_path=package_path,
    )


def _make_request(
    query: str = "hello",
    tenant_id: str = "t1",
    user_id: str = "u1",
    channel_id: str = "ch1",
    session_id: str = "sess-1",
) -> AgentRequest:
    return AgentRequest(
        request_id="req-1",
        session_id=session_id,
        context=RequestContext(
            tenant=TenantContext(tenant_id=tenant_id, retailer_id="r1"),
            store=StoreContext(store_id="s1"),
            channel=ChannelContext(channel_id=channel_id),
            user=UserContext(user_id=user_id),
            locale="en-US",
            timezone="America/New_York",
        ),
        input=AgentInput(query=query),
    )


class TestRuntimeMemoryCore:
    def test_runtime_memory_expiration(self):
        """测试 RuntimeMemory 模型过期时间计算。"""
        # 未过期
        m1 = RuntimeMemory(
            agent_id="test",
            scope=RuntimeMemoryScope.USER,
            type=RuntimeMemoryType.PREFERENCE,
            content="test",
            expires_at=datetime.now(UTC) + timedelta(seconds=60),
        )
        assert m1.is_expired() is False

        # 已过期
        m2 = RuntimeMemory(
            agent_id="test",
            scope=RuntimeMemoryScope.USER,
            type=RuntimeMemoryType.PREFERENCE,
            content="test",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        assert m2.is_expired() is True

    @pytest.mark.asyncio
    async def test_repository_crud_and_expiration(self):
        """测试仓储的基本 CRUD 及过期动态过滤。"""
        repo = InMemoryRuntimeMemoryRepository()
        m1 = RuntimeMemory(
            agent_id="agent1",
            tenant_id="t1",
            scope=RuntimeMemoryScope.USER,
            subject_id="u1",
            type=RuntimeMemoryType.PREFERENCE,
            content="memory 1",
        )
        await repo.create(m1)

        # 获取
        retrieved = await repo.get(m1.memory_id)
        assert retrieved is not None
        assert retrieved.content == "memory 1"

        # 写入另一个已过期的
        m2 = RuntimeMemory(
            agent_id="agent1",
            tenant_id="t1",
            scope=RuntimeMemoryScope.USER,
            subject_id="u1",
            type=RuntimeMemoryType.PREFERENCE,
            content="expired memory",
            expires_at=datetime.now(UTC) - timedelta(seconds=5),
        )
        await repo.create(m2)

        # 列出时，已过期的不应返回
        mems = await repo.list_by_user("u1")
        assert len(mems) == 1
        assert mems[0].memory_id == m1.memory_id

        # 更新
        m1.content = "updated memory 1"
        await repo.update(m1)
        retrieved = await repo.get(m1.memory_id)
        assert retrieved.content == "updated memory 1"

        # 删除
        deleted = await repo.delete(m1.memory_id)
        assert deleted is True
        assert await repo.get(m1.memory_id) is None


class TestSkillSelectorScope:
    def test_skill_selector_scope_matching(self, tmp_path: Path):
        """测试技能选择器的租户与渠道过滤机制。"""
        selector = SkillSelector(project_root=tmp_path)

        # 写入一个限制租户为 t1，渠道为 web 的技能 manifest.yaml
        skill_dir = tmp_path / "agents" / "myj" / "skills" / "skill1"
        skill_dir.mkdir(parents=True, exist_ok=True)
        manifest_file = skill_dir / "manifest.yaml"
        manifest_data = {
            "skill_id": "skill1",
            "title": "测试技能",
            "scope": {
                "tenant": "t1",
                "channels": ["web"]
            }
        }
        with open(manifest_file, "w", encoding="utf-8") as f:
            yaml.safe_dump(manifest_data, f)

        # 写入一个无限制的技能 manifest
        skill_dir2 = tmp_path / "agents" / "myj" / "skills" / "skill2"
        skill_dir2.mkdir(parents=True, exist_ok=True)
        manifest_file2 = skill_dir2 / "manifest.yaml"
        manifest_data2 = {
            "skill_id": "skill2",
            "title": "全局技能",
            "scope": {
                "tenant": "*",
                "channels": ["*"]
            }
        }
        with open(manifest_file2, "w", encoding="utf-8") as f:
            yaml.safe_dump(manifest_data2, f)

        skill_entry1 = SkillEntry(
            agent_id="myj",
            name="skill1",
            path="agents/myj/skills/skill1/manifest.yaml",
            status=MemoryStatus.ACTIVE,
        )
        skill_entry2 = SkillEntry(
            agent_id="myj",
            name="skill2",
            path="agents/myj/skills/skill2/manifest.yaml",
            status=MemoryStatus.ACTIVE,
        )

        spec = _make_spec(agent_id="myj")

        # 案例 1: 渠道、租户皆匹配 -> 两个都应被选中
        req1 = _make_request(tenant_id="t1", channel_id="web")
        results = selector.select(spec, req1, [skill_entry1, skill_entry2])
        assert len(results) == 2

        # 案例 2: 租户不匹配 -> 只有全局技能被选中
        req2 = _make_request(tenant_id="t2", channel_id="web")
        results = selector.select(spec, req2, [skill_entry1, skill_entry2])
        assert len(results) == 1
        assert results[0].name == "skill2"

        # 案例 3: 渠道不匹配 -> 只有全局技能被选中
        req3 = _make_request(tenant_id="t1", channel_id="mobile")
        results = selector.select(spec, req3, [skill_entry1, skill_entry2])
        assert len(results) == 1
        assert results[0].name == "skill2"


class TestContextBuilderInjection:
    @pytest.mark.asyncio
    async def test_runtime_memory_injection(self):
        """测试 ContextBuilder 能够检索出多种 Scope 的 Runtime 记忆，并正确合并和拼装。"""
        memory_repo = InMemoryRuntimeMemoryRepository()

        # 写入 4 种 Scope 记忆
        # 1. 租户隔离记忆
        await memory_repo.create(RuntimeMemory(
            agent_id="test-agent",
            tenant_id="t1",
            scope=RuntimeMemoryScope.SESSION,
            session_id="sess-1",
            type=RuntimeMemoryType.SESSION_SUMMARY,
            content="用户在会话一中问了天气",
        ))
        # 2. 属于另一个租户的记忆（应该被隔离过滤）
        await memory_repo.create(RuntimeMemory(
            agent_id="test-agent",
            tenant_id="t2",
            scope=RuntimeMemoryScope.SESSION,
            session_id="sess-1",
            type=RuntimeMemoryType.SESSION_SUMMARY,
            content="非当前租户的记忆，应当隔离",
        ))
        # 3. 用户级别偏好
        await memory_repo.create(RuntimeMemory(
            agent_id="test-agent",
            tenant_id="t1",
            scope=RuntimeMemoryScope.USER,
            subject_id="u1",
            type=RuntimeMemoryType.PREFERENCE,
            content="用户偏好简体中文",
        ))
        # 4. 租户级别默认提示
        await memory_repo.create(RuntimeMemory(
            agent_id="test-agent",
            tenant_id="t1",
            scope=RuntimeMemoryScope.TENANT,
            subject_id="t1",
            type=RuntimeMemoryType.CONTEXT_HINT,
            content="当前租户默认语言为中文",
        ))
        # 5. Agent 级别模式
        await memory_repo.create(RuntimeMemory(
            agent_id="test-agent",
            tenant_id="t1",
            scope=RuntimeMemoryScope.AGENT,
            subject_id="test-agent",
            type=RuntimeMemoryType.CONTEXT_HINT,
            content="该 Agent 核心负责测试用例",
        ))

        builder = ContextBuilder(runtime_memory_repo=memory_repo)
        spec = _make_spec()
        request = _make_request(tenant_id="t1", user_id="u1", session_id="sess-1")

        ctx = await builder.build(spec, request)

        # 验证提示词中是否包含了注入标志和内容
        assert "Injected Runtime Memories" in ctx.system_prompt
        assert "context, not source of truth" in ctx.system_prompt
        assert "用户在会话一中问了天气" in ctx.system_prompt
        assert "用户偏好简体中文" in ctx.system_prompt
        assert "当前租户默认语言为中文" in ctx.system_prompt
        assert "该 Agent 核心负责测试用例" in ctx.system_prompt
        assert "非当前租户的记忆，应当隔离" not in ctx.system_prompt

    @pytest.mark.asyncio
    async def test_runtime_memory_character_budget(self):
        """测试当记忆数量庞大时，受限于 token budget (2000 chars) 并会被截断。"""
        memory_repo = InMemoryRuntimeMemoryRepository()

        # 写入一条非常巨大的记忆，使得其超出限制
        big_content = "X" * 2100
        await memory_repo.create(RuntimeMemory(
            agent_id="test-agent",
            tenant_id="t1",
            scope=RuntimeMemoryScope.USER,
            subject_id="u1",
            type=RuntimeMemoryType.PREFERENCE,
            content=big_content,
        ))

        builder = ContextBuilder(runtime_memory_repo=memory_repo)
        spec = _make_spec()
        request = _make_request(tenant_id="t1", user_id="u1")

        ctx = await builder.build(spec, request)

        # 验证因为单条太大被 budget 过滤掉，从而系统提示词只含有基本描述，无此超大条目
        assert "Injected Runtime Memories" not in ctx.system_prompt

    @pytest.mark.asyncio
    async def test_skill_runtime_injection_and_usage_recording(self, tmp_path: Path):
        """测试技能 runtime 注入：正确拉取 SKILL.md、更新 use_count、并记录 last_used_at 审计。"""
        skill_repo = InMemorySkillRepository()

        # 创建本地 Skill 路径与 SKILL.md 声明
        skill_dir = tmp_path / "agents" / "test-agent" / "skills" / "math-helper"
        skill_dir.mkdir(parents=True, exist_ok=True)

        manifest_file = skill_dir / "manifest.yaml"
        manifest_data = {
            "skill_id": "math-helper",
            "title": "数学助手技能",
            "entrypoint": "SKILL.md",
            "scope": {
                "tenant": "*",
                "channels": ["*"]
            }
        }
        with open(manifest_file, "w", encoding="utf-8") as f:
            yaml.safe_dump(manifest_data, f)

        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("这是数学技能的操作指南内容。", encoding="utf-8")

        skill_entry = SkillEntry(
            agent_id="test-agent",
            name="math-helper",
            description="提供数学计算流程建议",
            path="agents/test-agent/skills/math-helper/manifest.yaml",
            status=MemoryStatus.ACTIVE,
        )
        await skill_repo.create(skill_entry)

        builder = ContextBuilder(skill_repo=skill_repo, project_root=tmp_path)
        spec = _make_spec()
        request = _make_request()

        # 注入前统计
        assert skill_entry.use_count == 0
        assert skill_entry.last_used_at is None

        ctx = await builder.build(spec, request, run_id="run-test-injection")

        # 注入后验证
        assert "Injected Agent Skills" in ctx.system_prompt
        assert "这是数学技能的操作指南内容。" in ctx.system_prompt

        # 验证 use_count 发生自增，且 last_used_at 发生更新
        updated_skill = await skill_repo.get(skill_entry.skill_id)
        assert updated_skill.use_count == 1
        assert updated_skill.last_used_at is not None
