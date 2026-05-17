"""补齐缺失表 + 审计哈希链 + 路由决策表

Revision ID: a7e3f2d01b84
Revises: 4dcf2bc95572
Create Date: 2026-05-17 16:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7e3f2d01b84"
down_revision: str | None = "4dcf2bc95572"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- 1. 审计事件表：新增完整性哈希列 --
    op.add_column(
        "deployment_audit_events",
        sa.Column("integrity_hash", sa.String(64), nullable=False, server_default=""),
    )
    op.add_column(
        "deployment_audit_events",
        sa.Column("prev_hash", sa.String(64), nullable=False, server_default=""),
    )

    # -- 2. 补齐工具审计表（ORM 已定义但上一版未迁移） --
    op.create_table(
        "tool_audit_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=True, index=True),
        sa.Column("created_by", sa.String(128), nullable=False, server_default="system"),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", sa.String(128), nullable=True, index=True),
        sa.Column("agent_id", sa.String(128), nullable=True, index=True),
        sa.Column("tool_name", sa.String(128), nullable=False, index=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.String(256), nullable=True),
        sa.Column("payload_json", sa.JSON, nullable=True),
        sa.Column("output_json", sa.JSON, nullable=True),
    )

    # -- 3. 补齐 API 密钥表 --
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=True, index=True),
        sa.Column("created_by", sa.String(128), nullable=False, server_default="system"),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("key_id", sa.String(128), nullable=False, unique=True, index=True),
        sa.Column("key_hash", sa.String(128), nullable=False, unique=True, index=True),
        sa.Column("role", sa.String(64), nullable=False, server_default="readonly"),
        sa.Column("scopes_json", sa.JSON, nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default="1"),
    )

    # -- 4. 新增路由决策表 --
    op.create_table(
        "routing_decisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=True, index=True),
        sa.Column("created_by", sa.String(128), nullable=False, server_default="system"),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", sa.String(128), nullable=False, index=True),
        sa.Column("agent_id", sa.String(128), nullable=False, index=True),
        sa.Column("reason", sa.String(256), nullable=False),
        sa.Column("deployment_id", sa.String(128), nullable=True),
        sa.Column("traffic_bucket", sa.Integer, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("context_json", sa.JSON, nullable=True),
    )

    # -- 5. 新增 coding_jobs 表 --
    op.create_table(
        "coding_jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=True, index=True),
        sa.Column("created_by", sa.String(128), nullable=False, server_default="system"),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("job_id", sa.String(128), nullable=False, unique=True, index=True),
        sa.Column("state", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("data_json", sa.JSON, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("coding_jobs")
    op.drop_table("routing_decisions")
    op.drop_table("api_keys")
    op.drop_table("tool_audit_events")
    op.drop_column("deployment_audit_events", "prev_hash")
    op.drop_column("deployment_audit_events", "integrity_hash")
