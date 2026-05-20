"""S9 Phase 9: SkillSelector 用于根据运行时请求条件选择活跃技能。"""
from __future__ import annotations

import logging
from pathlib import Path
import yaml

from agent_platform.domain.models import AgentRequest, AgentSpec
from agent_platform.evolution.memory_models import MemoryStatus, SkillEntry

logger = logging.getLogger(__name__)


class SkillSelector:
    """Agent Skill 选择器。

    根据当前请求的 Agent ID、租户、渠道和相关条件，过滤并匹配出最符合的活跃技能（Skill）。
    """

    def __init__(self, project_root: str | Path | None = None) -> None:
        # 默认项目根路径为当前工作目录
        self._project_root = Path(project_root) if project_root else Path.cwd()

    def select(
        self,
        spec: AgentSpec,
        request: AgentRequest,
        skills: list[SkillEntry],
        limit: int = 3,
    ) -> list[SkillEntry]:
        """根据 agent, request 过滤选择最匹配的 active skills，最多返回 limit 个。"""
        # 1. 过滤：只处理属于该 agent 且状态为 active 的技能
        agent_skills = [
            s for s in skills
            if s.agent_id == spec.agent_id and s.status == MemoryStatus.ACTIVE
        ]

        matched_skills = []
        for skill in agent_skills:
            # 2. 匹配规则：通过读取技能的 manifest.yaml 校验 scope（租户、渠道）限制
            manifest_path = self._project_root / skill.path
            if not manifest_path.exists():
                logger.warning("技能 manifest.yaml 文件未找到: %s", manifest_path)
                # 无法找到清单时，作为兜底默认放行（只过滤 agent_id 和 status）
                matched_skills.append(skill)
                continue

            try:
                with open(manifest_path, encoding="utf-8") as f:
                    manifest_data = yaml.safe_load(f) or {}
            except Exception:
                logger.exception("无法解析技能清单文件: %s", manifest_path)
                # 解析失败时，作为兜底默认放行
                matched_skills.append(skill)
                continue
            if not isinstance(manifest_data, dict):
                logger.warning(
                    "技能清单文件不是 YAML object，跳过 scope 校验并默认放行: %s",
                    manifest_path,
                )
                matched_skills.append(skill)
                continue

            # 校验 scope (租户与渠道过滤)
            scope = manifest_data.get("scope", {})

            # A. 租户校验
            tenant_scope = scope.get("tenant", "*")
            current_tenant = request.context.tenant.tenant_id if request.context.tenant else "default"
            if tenant_scope != "*" and tenant_scope != current_tenant:
                logger.debug(
                    "技能 %s 由于租户不匹配被过滤 (expected=%s, actual=%s)",
                    skill.name, tenant_scope, current_tenant
                )
                continue

            # B. 渠道校验
            channels_scope = scope.get("channels")
            if channels_scope:
                current_channel = request.context.channel.channel_id if request.context.channel else None
                if current_channel and current_channel not in channels_scope and "*" not in channels_scope:
                    logger.debug(
                        "技能 %s 由于渠道不匹配被过滤 (expected=%s, actual=%s)",
                        skill.name, channels_scope, current_channel
                    )
                    continue

            matched_skills.append(skill)

        # 返回最新的前 limit 个匹配技能
        return matched_skills[:limit]
