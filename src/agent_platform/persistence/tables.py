from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.storage.base import Base


class AuditMixin:
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
    __tablename__ = "agent_definitions"

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
    __tablename__ = "agent_deployments"

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
    __tablename__ = "deployment_audit_events"

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


class AgentRunRow(AuditMixin, Base):
    __tablename__ = "agent_runs"

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


class AgentSessionRow(AuditMixin, Base):
    __tablename__ = "agent_sessions"

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
