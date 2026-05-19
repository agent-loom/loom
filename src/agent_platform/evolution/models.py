"""自进化系统核心数据模型。

ImprovementProposal 是自进化闭环的核心契约——从运行反馈到 Plane 工单的桥梁。
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _proposal_id() -> str:
    return f"evo_{datetime.now(UTC).strftime('%Y%m%d')}_{uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ProposalStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    DISPATCHED = "dispatched"
    DISMISSED = "dismissed"
    CLOSED = "closed"


class RootCauseCategory(StrEnum):
    PROMPT_GAP = "prompt_gap"
    EVAL_GAP = "eval_gap"
    KNOWLEDGE_GAP = "knowledge_gap"
    TOOL_SCHEMA_GAP = "tool_schema_gap"
    TOOL_RUNTIME_ERROR = "tool_runtime_error"
    ROUTING_ERROR = "routing_error"
    FRONTEND_CONTRACT_GAP = "frontend_contract_gap"
    PLATFORM_BUG = "platform_bug"
    PRODUCT_REQUIREMENT = "product_requirement"


class EvidenceType(StrEnum):
    AGENT_RUN = "agent_run"
    TRACE = "trace"
    EVAL_FAILURE = "eval_failure"
    USER_FEEDBACK = "user_feedback"
    TOOL_ERROR = "tool_error"
    LOG_PATTERN = "log_pattern"
    PLANE_ITEM = "plane_item"
    GITLAB_ISSUE = "gitlab_issue"


class ProposalSource(StrEnum):
    EVOLUTION_ENGINE = "evolution_engine"
    EVAL_RUNNER = "eval_runner"
    FEEDBACK_INTELLIGENCE = "feedback_intelligence"
    MANUAL = "manual"


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class Evidence(BaseModel):
    type: EvidenceType
    id: str
    summary: str
    url: str | None = None
    trace_id: str | None = None
    tool_name: str | None = None


class RootCause(BaseModel):
    category: RootCauseCategory
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str


class RiskAssessment(BaseModel):
    level: RiskLevel
    reason: str
    requires_human_confirmation_before_devflow: bool = True
    requires_human_review_before_merge: bool = True


class ProposedChange(BaseModel):
    type: str
    path: str
    description: str


class ValidationSpec(BaseModel):
    commands: list[str] = Field(default_factory=list)
    existing_eval_regression_allowed: bool = False


# ---------------------------------------------------------------------------
# ImprovementProposal — 核心契约
# ---------------------------------------------------------------------------


class ImprovementProposal(BaseModel):
    schema_version: int = 1
    proposal_id: str = Field(default_factory=_proposal_id)
    title: str
    summary: str

    tenant_id: str = "default"
    agent_id: str
    task_type: str = "agent:prompt_eval_improvement"
    source: ProposalSource = ProposalSource.EVOLUTION_ENGINE

    status: ProposalStatus = ProposalStatus.DRAFT

    risk: RiskAssessment
    root_cause: RootCause
    evidence: list[Evidence] = Field(min_length=1)

    proposed_changes: list[ProposedChange] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)
    blocked_paths: list[str] = Field(default_factory=lambda: [
        "src/agent_platform/**",
        "deploy/**",
        "infra/**",
        ".env",
        ".env.*",
        "secrets/**",
    ])

    validation: ValidationSpec = Field(default_factory=ValidationSpec)

    plane_work_item_id: str | None = None
    plane_project_id: str | None = None
    gitlab_mr_iid: int | None = None

    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    closed_at: datetime | None = None
    outcome: str | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 输入事件（归一化后）
# ---------------------------------------------------------------------------


class EvolutionEvent(BaseModel):
    event_type: str
    agent_id: str
    tenant_id: str = "default"
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
