"""自进化 Memory 和 Skill 数据模型。

EvolutionMemory：从进化循环中提取的知识条目，带有置信度和信任分。
SkillEntry：Agent 技能索引条目，带有使用统计和来源追踪。
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _memory_id() -> str:
    return f"mem_{uuid4().hex[:12]}"


def _skill_id() -> str:
    return f"skill_{uuid4().hex[:12]}"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    STALE = "stale"
    ARCHIVED = "archived"


class MemoryType(StrEnum):
    PATTERN = "pattern"
    CONSTRAINT = "constraint"
    PREFERENCE = "preference"
    FIX_RECIPE = "fix_recipe"
    KNOWLEDGE = "knowledge"


class EvolutionMemory(BaseModel):
    """从进化循环中提取的知识条目。"""
    memory_id: str = Field(default_factory=_memory_id)
    agent_id: str
    tenant_id: str = "default"
    type: MemoryType
    content: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.7)
    trust_score: float = Field(ge=0.0, le=1.0, default=0.5)
    status: MemoryStatus = MemoryStatus.ACTIVE
    source_proposal_id: str | None = None
    source_type: str = "evolution_engine"
    tags: list[str] = Field(default_factory=list)
    use_count: int = 0
    helpful_count: int = 0
    unhelpful_count: int = 0
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def record_feedback(self, helpful: bool) -> None:
        """记录反馈并调整信任分（借鉴 Hermes HRR trust_score 机制）。"""
        if helpful:
            self.helpful_count += 1
            self.trust_score = min(1.0, self.trust_score + 0.05)
        else:
            self.unhelpful_count += 1
            self.trust_score = max(0.0, self.trust_score - 0.10)
        self.updated_at = datetime.now(UTC)


class SkillProvenance(StrEnum):
    USER_CREATED = "user_created"
    AGENT_CREATED = "agent_created"
    EVOLUTION = "evolution"
    IMPORTED = "imported"


class SkillEntry(BaseModel):
    """Agent 技能索引条目。"""
    skill_id: str = Field(default_factory=_skill_id)
    agent_id: str
    name: str
    description: str = ""
    path: str
    provenance: SkillProvenance = SkillProvenance.USER_CREATED
    status: MemoryStatus = MemoryStatus.ACTIVE
    tags: list[str] = Field(default_factory=list)
    use_count: int = 0
    view_count: int = 0
    last_used_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)
