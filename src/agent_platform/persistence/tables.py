"""SQLAlchemy ORM 表定义，包含审计 Mixin 和各领域实体的行模型。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.storage.base import Base


class AuditMixin:
    """审计字段 Mixin，提供 id、tenant_id、操作人和时间戳列。"""
    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    tenant_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    created_by: Mapped[str] = mapped_column(
        String(128), default="system"
    )
    request_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class AgentDefinitionRow(AuditMixin, Base):
    """Agent 定义表行模型。"""
    __tablename__ = "agent_definitions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version", name="uq_agent_definition_id_version"),
    )

    agent_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    version: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", index=True
    )
    manifest_json: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )


class AgentDeploymentRow(AuditMixin, Base):
    """Agent 部署表行模型。"""
    __tablename__ = "agent_deployments"
    __table_args__ = (
        Index("ix_deployment_agent_channel", "agent_id", "channel"),
    )

    deployment_id: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )
    agent_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    version: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    channel: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="registered"
    )
    traffic_percent: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100
    )


class DeploymentAuditEventRow(AuditMixin, Base):
    """部署审计事件表行模型。"""
    __tablename__ = "deployment_audit_events"
    __table_args__ = (
        Index("ix_audit_agent_channel", "agent_id", "channel"),
    )

    event_type: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    agent_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    version: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    channel: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )
    traffic_percent: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False
    )
    previous_version: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    actor: Mapped[str] = mapped_column(
        String(128), nullable=False, default="system"
    )
    artifact_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    metadata_json: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )
    integrity_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default=""
    )
    prev_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default=""
    )


class AgentRunRow(AuditMixin, Base):
    """Agent 运行记录表行模型。"""
    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_run_agent_status", "agent_id", "status"),
        Index("ix_run_session", "session_id", "agent_id"),
    )

    run_id: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )
    request_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    session_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    agent_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    agent_version: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    route_reason: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    runtime_backend: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False
    )
    latency_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    tool_calls_json: Mapped[list | None] = mapped_column(
        JSON, nullable=True
    )
    error_json: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )
    metadata_json: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )
    trace_events_json: Mapped[list | None] = mapped_column(
        JSON, nullable=True
    )


class AgentSessionRow(AuditMixin, Base):
    """Agent 会话表行模型。"""
    __tablename__ = "agent_sessions"
    __table_args__ = (
        Index("ix_session_agent_user", "agent_id", "user_id"),
    )

    session_id: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )
    agent_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    location_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    user_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    channel_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    history_json: Mapped[list | None] = mapped_column(
        JSON, nullable=True
    )
    state_snapshot_json: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )


class WebhookDeliveryRow(AuditMixin, Base):
    """Webhook 投递记录表行模型。"""
    __tablename__ = "webhook_deliveries"

    delivery_id: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )
    source: Mapped[str] = mapped_column(
        String(128), nullable=False
    )
    event_type: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="accepted"
    )
    payload_json: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )


class EvalRunRow(AuditMixin, Base):
    """评估运行记录表行模型。"""
    __tablename__ = "eval_runs"

    agent_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    agent_version: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    total: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    passed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    pass_rate: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    required_pass_rate: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    gate_passed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    results_json: Mapped[list | None] = mapped_column(
        JSON, nullable=True
    )
    trigger: Mapped[str] = mapped_column(
        String(64), nullable=False, default="manual"
    )


class ToolAuditRow(AuditMixin, Base):
    """工具调用审计表行模型。"""
    __tablename__ = "tool_audit_events"

    run_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    agent_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    tool_name: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False
    )
    latency_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    error: Mapped[str | None] = mapped_column(
        String(256), nullable=True
    )
    payload_json: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )
    output_json: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )


class ApiKeyRow(AuditMixin, Base):
    """API 密钥持久化表行模型。"""
    __tablename__ = "api_keys"

    key_id: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )
    key_hash: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )
    role: Mapped[str] = mapped_column(
        String(64), nullable=False, default="readonly"
    )
    scopes_json: Mapped[list | None] = mapped_column(
        JSON, nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )


class RoutingDecisionRow(AuditMixin, Base):
    """路由决策记录表行模型。"""
    __tablename__ = "routing_decisions"

    run_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    agent_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(
        String(256), nullable=False
    )
    deployment_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    traffic_bucket: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    latency_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    context_json: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )


class CodingJobRow(AuditMixin, Base):
    """DevFlow coding job 表行模型。"""
    __tablename__ = "coding_jobs"

    job_id: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )
    data_json: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )

class DeadLetterEntryModel(AuditMixin, Base):
    """Dead Letter Queue 条目表行模型。"""
    __tablename__ = "dead_letter_entries"

    source: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")


class ExecutionLogRow(AuditMixin, Base):
    """Runner 执行日志表行模型。"""
    __tablename__ = "execution_logs"

    job_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    stream: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    adapter_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class EvolutionProposalRow(AuditMixin, Base):
    """自进化提案表行模型。"""
    __tablename__ = "evolution_proposals"
    __table_args__ = (
        Index("ix_evo_agent_status", "agent_id", "status"),
    )

    proposal_id: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    task_type: Mapped[str] = mapped_column(String(128), nullable=False, default="agent:prompt_eval_improvement")
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="evolution_engine")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", index=True)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False, default="medium")
    risk_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    root_cause_category: Mapped[str] = mapped_column(String(64), nullable=False)
    root_cause_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    root_cause_explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence_json: Mapped[str] = mapped_column(JSON, nullable=False, default=list)
    proposed_changes_json: Mapped[str] = mapped_column(JSON, nullable=False, default=list)
    allowed_paths_json: Mapped[str] = mapped_column(JSON, nullable=False, default=list)
    blocked_paths_json: Mapped[str] = mapped_column(JSON, nullable=False, default=list)
    validation_json: Mapped[str] = mapped_column(JSON, nullable=False, default=dict)
    plane_work_item_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    gitlab_mr_iid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str] = mapped_column(JSON, nullable=False, default=dict)


class EvolutionMemoryRow(AuditMixin, Base):
    """自进化提取的知识记忆表行模型。"""
    __tablename__ = "evolution_memories"
    __table_args__ = (
        Index("ix_evomem_agent_type", "agent_id", "type"),
    )

    memory_id: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    trust_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    source_proposal_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, default="evolution_engine")
    tags_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    helpful_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unhelpful_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class RuntimeMemoryRow(AuditMixin, Base):
    """在线运行对话隔离与 TTL 记忆表行模型。"""
    __tablename__ = "runtime_memories"
    __table_args__ = (
        Index("ix_runmem_agent_scope_subject", "agent_id", "scope", "subject_id"),
        Index("ix_runmem_session", "session_id"),
    )

    memory_id: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, default="user_input")
    source_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    privacy_level: Mapped[str] = mapped_column(String(64), nullable=False, default="internal")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    ttl_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class SkillEntryRow(AuditMixin, Base):
    """Agent 扩展技能索引表行模型。"""
    __tablename__ = "skill_entries"

    skill_id: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    path: Mapped[str] = mapped_column(String(256), nullable=False)
    provenance: Mapped[str] = mapped_column(String(64), nullable=False, default="user_created")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    tags_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class CandidateRow(AuditMixin, Base):
    """自进化候选资产结构化缓冲表行模型。"""
    __tablename__ = "evolution_candidates"
    __table_args__ = (
        Index("ix_cand_agent_status", "agent_id", "status"),
    )

    candidate_id: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )
    candidate_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    generated_by: Mapped[str] = mapped_column(String(64), nullable=False, default="hermes")
    generator_role: Mapped[str | None] = mapped_column(String(128), nullable=True)
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    environment: Mapped[str] = mapped_column(String(64), nullable=False, default="prod")
    source_event_ids_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    evidence_ids_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False, default="low")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", index=True)
    promotion_target: Mapped[str] = mapped_column(String(64), nullable=False, default="none")
    validation_errors_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewForkAuditRow(AuditMixin, Base):
    """后台评审分支异步审计表行模型。"""
    __tablename__ = "review_fork_audits"

    review_fork_id: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )
    source_event_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    input_evidence_ids_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    output_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    candidate_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    proposal_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_provider: Mapped[str] = mapped_column(String(64), nullable=False, default="stub")
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

