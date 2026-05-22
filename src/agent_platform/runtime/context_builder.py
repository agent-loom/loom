"""运行时上下文构建器：组装系统提示、消息历史、工具定义和知识片段。

设计定位：
  运行时数据面 (Runtime Data Plane) 的核心组件之一 (ContextBuilder)。
  负责在执行引擎 (Engine) 调度底层后端之前，完成全部 Prompt 资源和检索特征的汇聚与组装。
  具体管线时序见：docs/02-architecture/agent-platform-core-design.md §3.4 Runtime 执行管线。

系统提示词组装时序：
  [Manifest Orchestrator Prompt]
            │
            ▼
  [1. 注入运行时记忆 (Runtime Memory)]
       ├─ 按 Session, User, Tenant, Agent 范围筛选活跃的 (MemoryStatus.ACTIVE) 记忆快照
       └─ 进行去重，并在 2000 字符上限内截断注入系统 Prompt 尾部
            │
            ▼
  [2. 注入动态技能 (Agent Skills)]
       ├─ 通过 SkillSelector 从技能库中动态选出相关性最高的 Top 3 技能
       └─ 解析其 SKILL.md 运行指南，并在 6000 字符上限内截断注入系统 Prompt 尾部
            │
            ▼
  [3. 拼接 Knowledge Context] (如果提供了检索知识片段)
            │
            ▼
  最终汇聚成的 system_prompt
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import yaml

from pydantic import BaseModel, Field

from agent_platform.domain.models import AgentRequest, AgentSpec, SessionMessage
from agent_platform.evolution.memory_models import (
    MemoryStatus,
    RuntimeMemory,
    RuntimeMemoryScope,
    SkillEntry,
)
from agent_platform.evolution.memory_repository import (
    RuntimeMemoryRepository,
    SkillRepository,
)

logger = logging.getLogger(__name__)


class RuntimeContext(BaseModel):
    """运行时上下文数据，包含提示词、消息、工具和元数据。"""

    system_prompt: str = ""
    messages: list[dict[str, str]] = Field(default_factory=list)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    knowledge_snippets: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextBuilder:
    """运行时上下文组装器 (Context Builder)

    负责提取 Agent 的静态清单定义，并融合动态的租户会话上下文、运行时沉淀记忆与动态编排的技能卡片，
    构建符合大模型要求的多维度运行时输入 (RuntimeContext)。
    设计详见 docs/02-architecture/agent-platform-core-design.md §3.4。
    """

    def __init__(
        self,
        runtime_memory_repo: RuntimeMemoryRepository | None = None,
        skill_repo: SkillRepository | None = None,
        project_root: str | Path | None = None,
    ) -> None:
        self.runtime_memory_repo = runtime_memory_repo
        self.skill_repo = skill_repo
        self.project_root = Path(project_root) if project_root else Path.cwd()
        from agent_platform.evolution.skill_selector import SkillSelector
        self.skill_selector = SkillSelector(project_root=self.project_root)

    async def build(
        self,
        spec: AgentSpec,
        request: AgentRequest,
        session_history: list[SessionMessage] | None = None,
        knowledge_results: list[str] | None = None,
        run_id: str | None = None,
    ) -> RuntimeContext:
        """从请求参数、会话多轮历史和知识检索结果组装完整的 RuntimeContext。"""
        # 1. 载入 Manifest 中声明的基础系统 Orchestrator 提示词
        system_prompt = self._load_system_prompt(spec)

        # ── 9.9.3: Runtime Memory Injection ──
        if self.runtime_memory_repo:
            tenant_id = request.context.tenant.tenant_id if request.context.tenant else "default"
            user_id = request.context.user.user_id if request.context.user else None
            session_id = request.session_id

            memories: list[RuntimeMemory] = []

            # 1) Session scope
            if session_id:
                session_mems = await self.runtime_memory_repo.list_by_session(session_id)
                session_mems = [
                    m for m in session_mems
                    if m.status == MemoryStatus.ACTIVE and m.tenant_id == tenant_id
                ]
                memories.extend(session_mems[:5])

            # 2) User scope
            if user_id:
                user_mems = await self.runtime_memory_repo.list_by_user(user_id)
                user_mems = [
                    m for m in user_mems
                    if m.status == MemoryStatus.ACTIVE and m.tenant_id == tenant_id and m.agent_id == spec.agent_id
                ]
                memories.extend(user_mems[:5])

            # 3) Tenant scope
            tenant_mems = await self.runtime_memory_repo.list_by_tenant(tenant_id, scope=RuntimeMemoryScope.TENANT)
            tenant_mems = [
                m for m in tenant_mems
                if m.status == MemoryStatus.ACTIVE and m.agent_id == spec.agent_id
            ]
            memories.extend(tenant_mems[:5])

            # 4) Agent scope
            agent_mems = await self.runtime_memory_repo.list_by_agent(spec.agent_id, scope=RuntimeMemoryScope.AGENT)
            agent_mems = [
                m for m in agent_mems
                if m.status == MemoryStatus.ACTIVE and m.tenant_id == tenant_id
            ]
            memories.extend(agent_mems[:5])

            # 去重
            seen_ids = set()
            unique_memories = []
            for m in memories:
                if m.memory_id not in seen_ids:
                    seen_ids.add(m.memory_id)
                    unique_memories.append(m)

            # 注入限制
            total_chars = 0
            selected_memories = []
            for m in unique_memories:
                m_repr = f"- [{m.scope.upper()} MEMORY] {m.content}\n"
                if total_chars + len(m_repr) > 2000:
                    break
                selected_memories.append(m_repr)
                total_chars += len(m_repr)

            if selected_memories:
                memory_block = (
                    "\n\n# Injected Runtime Memories (context, not source of truth)\n"
                    "The following are memories retrieved based on the current user session/preference context:\n"
                    + "".join(selected_memories)
                )
                system_prompt += memory_block

        # ── 9.9.5: Skill Runtime Injection ──
        if self.skill_repo:
            all_skills = await self.skill_repo.list_by_agent(spec.agent_id)
            selected_skills = self.skill_selector.select(spec, request, all_skills, limit=3)

            injected_skills_info = []
            total_skill_chars = 0
            for skill in selected_skills:
                manifest_path = self.project_root / skill.path
                skill_dir = manifest_path.parent

                entrypoint = "SKILL.md"
                try:
                    with open(manifest_path, encoding="utf-8") as f:
                        manifest_data = yaml.safe_load(f) or {}
                        entrypoint = manifest_data.get("entrypoint", "SKILL.md")
                except Exception:
                    pass

                skill_file = skill_dir / entrypoint
                instructions = ""
                if skill_file.exists():
                    try:
                        instructions = skill_file.read_text(encoding="utf-8")
                    except Exception:
                        pass

                skill_desc = skill.description or "No description"
                skill_repr = f"- Skill: {skill.name}\n  Description: {skill_desc}\n"
                if instructions:
                    skill_repr += f"  Instructions:\n  {instructions}\n"

                # 限制注入字符数 (最大 6000)
                if total_skill_chars + len(skill_repr) > 6000:
                    skill_repr_fallback = f"- Skill: {skill.name}\n  Description: {skill_desc}\n"
                    if total_skill_chars + len(skill_repr_fallback) <= 6000:
                        injected_skills_info.append(skill_repr_fallback)
                        total_skill_chars += len(skill_repr_fallback)
                    continue

                injected_skills_info.append(skill_repr)
                total_skill_chars += len(skill_repr)

                # 更新计数和审计日志
                skill.use_count += 1
                skill.last_used_at = datetime.now(UTC)
                await self.skill_repo.update(skill)
                logger.info(
                    "skill.used: skill_id=%s, agent_id=%s, run_id=%s",
                    skill.skill_id, skill.agent_id, run_id or "unknown"
                )

            if injected_skills_info:
                skills_block = (
                    "\n\n# Injected Agent Skills\n"
                    "The following are specialized operational skills loaded for this agent to help execute tasks:\n"
                    + "\n".join(injected_skills_info)
                )
                system_prompt += skills_block

        # 2. Build message history from session
        messages = self._build_messages(session_history or [], request, spec)

        # 3. Build tool definitions from manifest
        tools = self._build_tool_defs(spec)

        # 4. Include knowledge snippets
        snippets = knowledge_results or []

        # 5. Build context metadata (tenant, store, channel, etc.)
        metadata = self._build_metadata(request, spec)

        return RuntimeContext(
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
            knowledge_snippets=snippets,
            metadata=metadata,
        )

    def _load_system_prompt(self, spec: AgentSpec) -> str:
        """Load orchestrator prompt from the agent package."""
        orchestrator_path = spec.manifest.prompts.get("orchestrator")
        if not orchestrator_path:
            return f"You are agent {spec.agent_id}."

        full_path = spec.package_path / orchestrator_path
        try:
            return full_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning("prompt file not found: %s", full_path)
            return f"You are agent {spec.agent_id}."

    def _build_messages(
        self,
        history: list[SessionMessage],
        request: AgentRequest,
        spec: AgentSpec,
    ) -> list[dict[str, str]]:
        window = spec.manifest.session.history_window
        recent = history[-window:] if window > 0 else []
        messages = [{"role": m.role, "content": m.content} for m in recent]
        messages.append({"role": "user", "content": request.input.query})
        return messages

    def _build_tool_defs(self, spec: AgentSpec) -> list[dict[str, Any]]:
        allowed = spec.manifest.tools.allow
        return [{"name": t, "type": "function"} for t in allowed]

    def _build_metadata(self, request: AgentRequest, spec: AgentSpec) -> dict[str, Any]:
        return {
            "agent_id": spec.agent_id,
            "tenant_id": request.context.tenant.tenant_id if request.context.tenant else "default",
            "org_id": request.context.tenant.org_id if request.context.tenant else None,
            "location_id": request.context.location.location_id if request.context.location else None,
            "channel_id": request.context.channel.channel_id if request.context.channel else None,
            "user_id": request.context.user.user_id if request.context.user else None,
            "locale": request.context.locale,
            "timezone": request.context.timezone,
            "session_scope": spec.manifest.session.scope,
        }
