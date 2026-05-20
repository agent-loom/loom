"""各 Repository 协议的 SQLAlchemy 异步实现。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_platform.domain.models import (
    AgentDefinition,
    AgentDefinitionStatus,
    AgentDeployment,
    AgentDeploymentStatus,
    AgentError,
    AgentRun,
    AgentRunStatus,
    AgentSession,
    SessionMessage,
    ToolCallTrace,
    TraceEvent,
)
from agent_platform.persistence.context import get_audit_context
from agent_platform.persistence.tables import (
    AgentDefinitionRow,
    AgentDeploymentRow,
    AgentRunRow,
    AgentSessionRow,
    ApiKeyRow,
    CodingJobRow,
    DeploymentAuditEventRow,
    EvalRunRow,
    ExecutionLogRow,
    EvolutionProposalRow,
    RoutingDecisionRow,
    WebhookDeliveryRow,
    DeadLetterEntryModel,
    EvolutionMemoryRow,
    RuntimeMemoryRow,
    SkillEntryRow,
    CandidateRow,
    ReviewForkAuditRow,
)
from agent_platform.registry.deployment import DeploymentEvent
from agent_platform.webhooks.dead_letter import DeadLetterEntry, DeadLetterQueue
from agent_platform.devflow.runner.execution_log import (
    ExecutionLogEntry,
    LogStream,
)


def _fill_audit(row: Any) -> None:
    """Fill audit fields from the current AuditContext."""
    ctx = get_audit_context()
    row.created_by = ctx.actor
    row.request_id = ctx.request_id
    if ctx.tenant_id is not None and getattr(row, "tenant_id", None) is None:
        row.tenant_id = ctx.tenant_id


# ------------------------------------------------------------------
# AgentDefinition
# ------------------------------------------------------------------


class SqlAgentDefinitionRepository:
    """Agent 定义的 SQL 存储实现。"""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """初始化，注入异步数据库会话工厂。"""
        self._sf = session_factory

    async def save(
        self, definition: AgentDefinition
    ) -> None:
        """将 Agent 定义持久化到数据库（upsert 语义）。"""
        async with self._sf() as session:
            stmt = select(AgentDefinitionRow).where(
                AgentDefinitionRow.agent_id == definition.agent_id,
                AgentDefinitionRow.version == definition.version,
            )
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()
            if existing is not None:
                existing.status = definition.status.value
                existing.manifest_json = definition.manifest.model_dump(mode="json")
                existing.updated_at = definition.updated_at
            else:
                row = AgentDefinitionRow(
                    agent_id=definition.agent_id,
                    version=definition.version,
                    status=definition.status.value,
                    manifest_json=definition.manifest.model_dump(mode="json"),
                    created_at=definition.created_at,
                    updated_at=definition.updated_at,
                )
                _fill_audit(row)
                session.add(row)
            await session.commit()

    async def get(
        self, agent_id: str, version: str
    ) -> AgentDefinition | None:
        """按 agent_id 和版本查询定义。"""
        stmt = select(AgentDefinitionRow).where(
            AgentDefinitionRow.agent_id == agent_id,
            AgentDefinitionRow.version == version,
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return self._to_domain(row)

    async def get_latest(
        self, agent_id: str
    ) -> AgentDefinition | None:
        """获取指定 agent 的最新版本定义。"""
        stmt = (
            select(AgentDefinitionRow)
            .where(AgentDefinitionRow.agent_id == agent_id)
            .order_by(AgentDefinitionRow.created_at.desc())
            .limit(1)
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return self._to_domain(row)

    async def list_all(
        self, *, status: str | None = None
    ) -> list[AgentDefinition]:
        """列出所有定义，可按状态过滤。"""
        stmt = select(AgentDefinitionRow)
        if status is not None:
            stmt = stmt.where(
                AgentDefinitionRow.status == status
            )
        async with self._sf() as session:
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [self._to_domain(r) for r in rows]

    async def update_status(
        self, agent_id: str, version: str, status: str
    ) -> bool:
        """更新指定定义的状态字段，返回是否成功找到并更新。"""
        stmt = select(AgentDefinitionRow).where(
            AgentDefinitionRow.agent_id == agent_id,
            AgentDefinitionRow.version == version,
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return False
            row.status = status
            row.updated_at = datetime.now(UTC)
            await session.commit()
            return True

    @staticmethod
    def _to_domain(
        row: AgentDefinitionRow,
    ) -> AgentDefinition:
        from agent_platform.domain.models import (
            AgentManifest,
        )

        return AgentDefinition(
            agent_id=row.agent_id,
            version=row.version,
            status=AgentDefinitionStatus(row.status),
            manifest=AgentManifest.model_validate(
                row.manifest_json
            ),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


# ------------------------------------------------------------------
# AgentDeployment
# ------------------------------------------------------------------


class SqlAgentDeploymentRepository:
    """Agent 部署记录的 SQL 存储实现。"""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """初始化，注入异步数据库会话工厂。"""
        self._sf = session_factory

    async def save(
        self, deployment: AgentDeployment
    ) -> None:
        """将部署记录持久化到数据库，按 deployment_id 提供 upsert 语义。"""
        async with self._sf() as session:
            stmt = select(AgentDeploymentRow).where(
                AgentDeploymentRow.deployment_id == deployment.deployment_id
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                row = AgentDeploymentRow(
                    deployment_id=deployment.deployment_id,
                    agent_id=deployment.agent_id,
                    version=deployment.version,
                    channel=deployment.channel,
                    status=deployment.status.value,
                    traffic_percent=deployment.traffic_percent,
                    tenant_id=deployment.tenant_id,
                )
                _fill_audit(row)
                session.add(row)
            else:
                row.agent_id = deployment.agent_id
                row.version = deployment.version
                row.channel = deployment.channel
                row.status = deployment.status.value
                row.traffic_percent = deployment.traffic_percent
                row.tenant_id = deployment.tenant_id
                row.updated_at = datetime.now(UTC)
            await session.commit()

    async def get(
        self, deployment_id: str
    ) -> AgentDeployment | None:
        """按 deployment_id 获取部署记录。"""
        stmt = select(AgentDeploymentRow).where(
            AgentDeploymentRow.deployment_id == deployment_id
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return self._to_domain(row)

    async def resolve(
        self,
        *,
        agent_id: str,
        channel: str,
        tenant_id: str | None = None,
    ) -> AgentDeployment | None:
        """按 agent_id、渠道和租户解析部署记录。"""
        stmt = select(AgentDeploymentRow).where(
            AgentDeploymentRow.agent_id == agent_id,
            AgentDeploymentRow.channel == channel,
        )
        if tenant_id is not None:
            stmt = stmt.where(
                AgentDeploymentRow.tenant_id == tenant_id
            )
        else:
            stmt = stmt.where(AgentDeploymentRow.tenant_id.is_(None))
        stmt = stmt.limit(1)
        async with self._sf() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return self._to_domain(row)

    async def list_all(
        self,
        *,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[AgentDeployment]:
        """列出所有部署，可按 agent_id 或租户过滤。"""
        stmt = select(AgentDeploymentRow)
        if agent_id is not None:
            stmt = stmt.where(
                AgentDeploymentRow.agent_id == agent_id
            )
        if tenant_id is not None:
            stmt = stmt.where(
                AgentDeploymentRow.tenant_id == tenant_id
            )
        async with self._sf() as session:
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [self._to_domain(r) for r in rows]

    async def delete(self, deployment_id: str) -> None:
        """删除指定部署记录。"""
        stmt = select(AgentDeploymentRow).where(
            AgentDeploymentRow.deployment_id == deployment_id
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is not None:
                await session.delete(row)
                await session.commit()

    @staticmethod
    def _to_domain(
        row: AgentDeploymentRow,
    ) -> AgentDeployment:
        return AgentDeployment(
            deployment_id=row.deployment_id,
            agent_id=row.agent_id,
            version=row.version,
            channel=row.channel,
            status=AgentDeploymentStatus(row.status),
            tenant_id=row.tenant_id,
            traffic_percent=row.traffic_percent,
        )


# ------------------------------------------------------------------
# DeploymentAudit
# ------------------------------------------------------------------


class SqlDeploymentAuditRepository:
    """部署审计事件的 SQL 存储实现。"""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """初始化，注入异步数据库会话工厂。"""
        self._sf = session_factory

    async def record(
        self, event: DeploymentEvent
    ) -> None:
        """将审计事件持久化到数据库。"""
        row = DeploymentAuditEventRow(
            event_type=event.event_type,
            agent_id=event.agent_id,
            version=event.version,
            channel=event.channel,
            traffic_percent=event.traffic_percent,
            status=event.status.value,
            previous_version=event.previous_version,
            actor=event.actor,
            artifact_id=event.artifact_id,
            metadata_json=event.metadata,
            integrity_hash=event.integrity_hash,
            prev_hash=event.prev_hash,
            created_at=event.timestamp,
        )
        _fill_audit(row)
        async with self._sf() as session:
            session.add(row)
            await session.commit()

    async def list_events(
        self,
        *,
        agent_id: str | None = None,
        channel: str | None = None,
        tenant_id: str | None = None,
        limit: int = 50,
    ) -> list[DeploymentEvent]:
        """列出审计事件，可按条件过滤。"""
        stmt = select(DeploymentAuditEventRow)
        if agent_id is not None:
            stmt = stmt.where(
                DeploymentAuditEventRow.agent_id == agent_id
            )
        if channel is not None:
            stmt = stmt.where(
                DeploymentAuditEventRow.channel == channel
            )
        if tenant_id is not None:
            stmt = stmt.where(
                DeploymentAuditEventRow.tenant_id == tenant_id
            )
        stmt = stmt.order_by(
            DeploymentAuditEventRow.created_at.desc()
        ).limit(limit)
        async with self._sf() as session:
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [self._to_domain(r) for r in rows]

    async def get_rollback_version(
        self, agent_id: str, channel: str
    ) -> str | None:
        """查询可回滚的上一版本号。"""
        stmt = (
            select(DeploymentAuditEventRow)
            .where(
                DeploymentAuditEventRow.agent_id == agent_id,
                DeploymentAuditEventRow.channel == channel,
                DeploymentAuditEventRow.previous_version.isnot(
                    None
                ),
            )
            .order_by(
                DeploymentAuditEventRow.created_at.desc()
            )
            .limit(1)
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return row.previous_version

    @staticmethod
    def _to_domain(
        row: DeploymentAuditEventRow,
    ) -> DeploymentEvent:
        return DeploymentEvent(
            timestamp=row.created_at,
            event_type=row.event_type,
            agent_id=row.agent_id,
            version=row.version,
            channel=row.channel,
            traffic_percent=row.traffic_percent,
            status=AgentDeploymentStatus(row.status),
            previous_version=row.previous_version,
            actor=row.actor,
            artifact_id=row.artifact_id,
            metadata=row.metadata_json or {},
            integrity_hash=row.integrity_hash or "",
            prev_hash=row.prev_hash or "",
        )


# ------------------------------------------------------------------
# AgentRun
# ------------------------------------------------------------------


class SqlAgentRunRepository:
    """Agent 运行记录的 SQL 存储实现。"""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """初始化，注入异步数据库会话工厂。"""
        self._sf = session_factory

    async def record(self, run: AgentRun) -> None:
        """将运行记录持久化到数据库。"""
        row = AgentRunRow(
            run_id=run.run_id,
            request_id=run.request_id,
            session_id=run.session_id,
            tenant_id=run.tenant_id,
            agent_id=run.agent_id,
            agent_version=run.agent_version,
            route_reason=run.route_reason,
            runtime_backend=run.runtime_backend,
            status=run.status.value,
            latency_ms=run.latency_ms,
            tool_calls_json=[
                tc.model_dump(mode="json")
                for tc in run.tool_calls
            ],
            error_json=(
                run.error.model_dump(mode="json")
                if run.error
                else None
            ),
            metadata_json=run.metadata or None,
            trace_events_json=[
                te.model_dump(mode="json")
                for te in (run.trace_events or [])
            ] or None,
        )
        _fill_audit(row)
        async with self._sf() as session:
            session.add(row)
            await session.commit()

    async def get(
        self, run_id: str
    ) -> AgentRun | None:
        """按 run_id 获取运行记录。"""
        stmt = select(AgentRunRow).where(
            AgentRunRow.run_id == run_id
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return self._to_domain(row)

    async def list_runs(
        self,
        *,
        agent_id: str | None = None,
        session_id: str | None = None,
        tenant_id: str | None = None,
        limit: int = 100,
    ) -> list[AgentRun]:
        """列出运行记录，可按条件过滤。"""
        stmt = select(AgentRunRow)
        if agent_id is not None:
            stmt = stmt.where(
                AgentRunRow.agent_id == agent_id
            )
        if session_id is not None:
            stmt = stmt.where(
                AgentRunRow.session_id == session_id
            )
        if tenant_id is not None:
            stmt = stmt.where(
                AgentRunRow.tenant_id == tenant_id
            )
        stmt = stmt.order_by(
            AgentRunRow.created_at.desc()
        ).limit(limit)
        async with self._sf() as session:
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [self._to_domain(r) for r in rows]

    @staticmethod
    def _to_domain(row: AgentRunRow) -> AgentRun:
        tool_calls = [
            ToolCallTrace.model_validate(tc)
            for tc in (row.tool_calls_json or [])
        ]
        error = (
            AgentError.model_validate(row.error_json)
            if row.error_json
            else None
        )
        trace_events = [
            TraceEvent.model_validate(te)
            for te in (row.trace_events_json or [])
        ]
        return AgentRun(
            run_id=row.run_id,
            request_id=row.request_id,
            session_id=row.session_id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            agent_version=row.agent_version,
            route_reason=row.route_reason,
            runtime_backend=row.runtime_backend,
            status=AgentRunStatus(row.status),
            latency_ms=row.latency_ms,
            tool_calls=tool_calls,
            trace_events=trace_events,
            error=error,
            metadata=row.metadata_json or {},
        )


# ------------------------------------------------------------------
# AgentSession
# ------------------------------------------------------------------


class SqlAgentSessionRepository:
    """Agent 会话的 SQL 存储实现。"""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """初始化，注入异步数据库会话工厂。"""
        self._sf = session_factory

    async def save(
        self, session: AgentSession
    ) -> None:
        """将会话持久化到数据库（支持 upsert 语义）。"""
        history = [
            m.model_dump(mode="json")
            for m in session.history
        ]
        async with self._sf() as db:
            # 使用 select + 条件更新/插入实现 upsert，避免依赖数据库方言特定的 ON CONFLICT 语法
            stmt = select(AgentSessionRow).where(
                AgentSessionRow.session_id
                == session.session_id
            )
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()
            if existing is not None:
                # 已存在：仅更新可变字段，保留原始创建时间和审计信息
                existing.agent_id = session.agent_id
                existing.location_id = session.location_id
                existing.user_id = session.user_id
                existing.channel_id = session.channel_id
                existing.history_json = history
                existing.state_snapshot_json = (
                    session.state_snapshot
                )
                existing.updated_at = session.updated_at
                if session.tenant_id is not None:
                    existing.tenant_id = session.tenant_id
            else:
                # 首次创建：插入新行并填充审计字段
                row = AgentSessionRow(
                    session_id=session.session_id,
                    agent_id=session.agent_id,
                    location_id=session.location_id,
                    user_id=session.user_id,
                    channel_id=session.channel_id,
                    history_json=history,
                    state_snapshot_json=(
                        session.state_snapshot
                    ),
                    created_at=session.created_at,
                    updated_at=session.updated_at,
                )
                if session.tenant_id is not None:
                    row.tenant_id = session.tenant_id
                _fill_audit(row)
                db.add(row)
            await db.commit()

    async def load(
        self, session_id: str
    ) -> AgentSession | None:
        """按 session_id 加载会话。"""
        stmt = select(AgentSessionRow).where(
            AgentSessionRow.session_id == session_id
        )
        async with self._sf() as db:
            result = await db.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return self._to_domain(row)

    async def delete(self, session_id: str) -> None:
        """删除指定会话。"""
        stmt = select(AgentSessionRow).where(
            AgentSessionRow.session_id == session_id
        )
        async with self._sf() as db:
            result = await db.execute(stmt)
            row = result.scalar_one_or_none()
            if row is not None:
                await db.delete(row)
                await db.commit()

    async def list_sessions(
        self,
        *,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[AgentSession]:
        """列出会话，可按 agent_id 或租户过滤。"""
        stmt = select(AgentSessionRow)
        if agent_id is not None:
            stmt = stmt.where(
                AgentSessionRow.agent_id == agent_id
            )
        if tenant_id is not None:
            stmt = stmt.where(
                AgentSessionRow.tenant_id == tenant_id
            )
        async with self._sf() as db:
            result = await db.execute(stmt)
            rows = result.scalars().all()
            return [self._to_domain(r) for r in rows]

    async def count_sessions(
        self,
        *,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> int:
        """使用 COUNT(*) 高效统计会话数。"""
        stmt = select(func.count(AgentSessionRow.id))
        if agent_id is not None:
            stmt = stmt.where(
                AgentSessionRow.agent_id == agent_id
            )
        if tenant_id is not None:
            stmt = stmt.where(
                AgentSessionRow.tenant_id == tenant_id
            )
        async with self._sf() as db:
            result = await db.execute(stmt)
            return result.scalar_one()

    @staticmethod
    def _to_domain(
        row: AgentSessionRow,
    ) -> AgentSession:
        history = [
            SessionMessage.model_validate(m)
            for m in (row.history_json or [])
        ]
        return AgentSession(
            session_id=row.session_id,
            agent_id=row.agent_id,
            tenant_id=row.tenant_id,
            location_id=row.location_id,
            user_id=row.user_id,
            channel_id=row.channel_id,
            history=history,
            state_snapshot=row.state_snapshot_json or {},
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


# ------------------------------------------------------------------
# WebhookDelivery
# ------------------------------------------------------------------


class SqlWebhookDeliveryRepository:
    """Webhook 投递记录的 SQL 存储实现。"""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """初始化，注入异步数据库会话工厂。"""
        self._sf = session_factory

    async def exists(self, delivery_id: str) -> bool:
        """判断投递记录是否已存在。"""
        stmt = select(WebhookDeliveryRow).where(
            WebhookDeliveryRow.delivery_id == delivery_id
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            return result.scalar_one_or_none() is not None

    async def record(
        self,
        *,
        delivery_id: str,
        source: str,
        event_type: str | None = None,
        status: str = "accepted",
        payload: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None:
        """将 Webhook 投递记录持久化到数据库。"""
        row = WebhookDeliveryRow(
            delivery_id=delivery_id,
            source=source,
            event_type=event_type,
            status=status,
            payload_json=payload,
            error_message=error_message,
        )
        _fill_audit(row)
        async with self._sf() as session:
            session.add(row)
            await session.commit()


# ------------------------------------------------------------------
# EvalRun
# ------------------------------------------------------------------


class SqlEvalRunRepository:
    """评估运行记录的 SQL 存储实现。"""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """初始化，注入异步数据库会话工厂。"""
        self._sf = session_factory

    async def record(
        self,
        *,
        agent_id: str,
        agent_version: str,
        total: int,
        passed: int,
        pass_rate: float,
        required_pass_rate: float,
        gate_passed: bool,
        results: list[dict[str, Any]],
        trigger: str = "manual",
    ) -> None:
        """将评估记录持久化到数据库。"""
        row = EvalRunRow(
            agent_id=agent_id,
            agent_version=agent_version,
            total=total,
            passed=passed,
            pass_rate=pass_rate,
            required_pass_rate=required_pass_rate,
            gate_passed=gate_passed,
            results_json=results,
            trigger=trigger,
        )
        _fill_audit(row)
        async with self._sf() as session:
            session.add(row)
            await session.commit()

    async def get_latest(
        self, agent_id: str
    ) -> dict[str, Any] | None:
        """获取指定 agent 最近一次评估结果。"""
        stmt = (
            select(EvalRunRow)
            .where(EvalRunRow.agent_id == agent_id)
            .order_by(EvalRunRow.created_at.desc())
            .limit(1)
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return self._to_dict(row)

    async def list_runs(
        self,
        *,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """列出评估记录，可按条件过滤。"""
        stmt = select(EvalRunRow)
        if agent_id is not None:
            stmt = stmt.where(
                EvalRunRow.agent_id == agent_id
            )
        if tenant_id is not None:
            stmt = stmt.where(
                EvalRunRow.tenant_id == tenant_id
            )
        stmt = stmt.order_by(
            EvalRunRow.created_at.desc()
        ).limit(limit)
        async with self._sf() as session:
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [self._to_dict(r) for r in rows]

    @staticmethod
    def _to_dict(row: EvalRunRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "agent_id": row.agent_id,
            "agent_version": row.agent_version,
            "total": row.total,
            "passed": row.passed,
            "pass_rate": row.pass_rate,
            "required_pass_rate": row.required_pass_rate,
            "gate_passed": row.gate_passed,
            "results": row.results_json or [],
            "trigger": row.trigger,
            "created_at": (
                row.created_at.isoformat()
                if row.created_at
                else None
            ),
        }


# ------------------------------------------------------------------
# ToolAudit
# ------------------------------------------------------------------


class SqlToolAuditRepository:
    """工具调用审计的 SQL 存储实现。"""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self._sf = session_factory

    async def record(
        self,
        *,
        tool_name: str,
        status: str,
        latency_ms: int,
        error: str | None = None,
        payload: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
        run_id: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        from agent_platform.persistence.tables import ToolAuditRow
        row = ToolAuditRow(
            tool_name=tool_name,
            status=status,
            latency_ms=latency_ms,
            error=error,
            payload_json=payload,
            output_json=output,
            run_id=run_id,
            agent_id=agent_id,
        )
        _fill_audit(row)
        async with self._sf() as session:
            session.add(row)
            await session.commit()

    async def list_events(
        self,
        *,
        tool_name: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        from agent_platform.persistence.tables import ToolAuditRow
        stmt = select(ToolAuditRow)
        if tool_name is not None:
            stmt = stmt.where(ToolAuditRow.tool_name == tool_name)
        if agent_id is not None:
            stmt = stmt.where(ToolAuditRow.agent_id == agent_id)
        if run_id is not None:
            stmt = stmt.where(ToolAuditRow.run_id == run_id)
        if status is not None:
            stmt = stmt.where(ToolAuditRow.status == status)
        stmt = stmt.order_by(ToolAuditRow.created_at.desc()).limit(limit)
        async with self._sf() as session:
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [self._to_dict(r) for r in rows]

    @staticmethod
    def _to_dict(row: Any) -> dict[str, Any]:
        return {
            "id": row.id,
            "tool_name": row.tool_name,
            "status": row.status,
            "latency_ms": row.latency_ms,
            "error": row.error,
            "payload": row.payload_json,
            "output": row.output_json,
            "run_id": row.run_id,
            "agent_id": row.agent_id,
            "created_at": (
                row.created_at.isoformat() if row.created_at else None
            ),
        }


# ------------------------------------------------------------------
# ApiKeyStore (SQL)
# ------------------------------------------------------------------


class SqlApiKeyStore:
    """SQL-backed API key store using SHA-256 hash lookup."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self._sf = session_factory

    @staticmethod
    def _hash(key_plaintext: str) -> str:
        import hashlib
        return hashlib.sha256(key_plaintext.encode()).hexdigest()

    async def add_key(
        self,
        key_plaintext: str,
        *,
        key_id: str,
        tenant_id: str = "default",
        role: str = "platform_admin",
        scopes: list[str] | None = None,
        created_by: str = "system",
        expires_at: datetime | None = None,
    ) -> None:
        h = self._hash(key_plaintext)
        async with self._sf() as session:
            row = ApiKeyRow(
                key_id=key_id,
                key_hash=h,
                tenant_id=tenant_id,
                role=role,
                scopes_json=scopes or [
                    "chat", "deploy", "admin", "eval",
                    "register", "rollback", "read",
                ],
                created_by=created_by,
                expires_at=expires_at,
                active=True,
            )
            session.add(row)
            await session.commit()

    def verify(self, key_plaintext: str) -> None:
        raise RuntimeError(
            "SqlApiKeyStore 仅支持异步调用，请使用 verify_async()"
        )

    async def verify_async(self, key_plaintext: str):
        from agent_platform.api.auth import ApiKeyRecord
        h = self._hash(key_plaintext)
        async with self._sf() as session:
            stmt = select(ApiKeyRow).where(
                ApiKeyRow.key_hash == h,
                ApiKeyRow.active.is_(True),
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            if row.expires_at is not None:
                exp = row.expires_at
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=UTC)
                if exp <= datetime.now(UTC):
                    return None
            return ApiKeyRecord(
                key_id=row.key_id,
                key_hash=row.key_hash,
                tenant_id=row.tenant_id or "default",
                role=row.role,
                scopes=row.scopes_json or [],
                created_by=row.created_by,
                expires_at=row.expires_at,
            )

    async def list_keys(self, *, tenant_id: str | None = None) -> list[dict]:
        async with self._sf() as session:
            stmt = select(ApiKeyRow).where(ApiKeyRow.active.is_(True))
            if tenant_id:
                stmt = stmt.where(ApiKeyRow.tenant_id == tenant_id)
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                {
                    "key_id": r.key_id,
                    "tenant_id": r.tenant_id,
                    "role": r.role,
                    "scopes": r.scopes_json,
                    "created_by": r.created_by,
                    "expires_at": (
                        r.expires_at.isoformat() if r.expires_at else None
                    ),
                }
                for r in rows
            ]

    async def revoke_key(self, key_id: str) -> bool:
        async with self._sf() as session:
            stmt = select(ApiKeyRow).where(ApiKeyRow.key_id == key_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return False
            row.active = False
            await session.commit()
            return True


# ------------------------------------------------------------------
# RoutingDecision
# ------------------------------------------------------------------


class SqlRoutingDecisionRepository:
    """路由决策记录的 SQL 存储实现。"""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self._sf = session_factory

    async def record(
        self,
        *,
        run_id: str,
        agent_id: str,
        reason: str,
        deployment_id: str | None = None,
        traffic_bucket: int | None = None,
        latency_ms: int = 0,
        context: dict[str, Any] | None = None,
    ) -> None:
        row = RoutingDecisionRow(
            run_id=run_id,
            agent_id=agent_id,
            reason=reason,
            deployment_id=deployment_id,
            traffic_bucket=traffic_bucket,
            latency_ms=latency_ms,
            context_json=context,
        )
        _fill_audit(row)
        async with self._sf() as session:
            session.add(row)
            await session.commit()

    async def get(self, run_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            stmt = select(RoutingDecisionRow).where(
                RoutingDecisionRow.run_id == run_id
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return self._to_dict(row)

    async def list_decisions(
        self,
        *,
        agent_id: str | None = None,
        reason: str | None = None,
        tenant_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        stmt = select(RoutingDecisionRow)
        if agent_id is not None:
            stmt = stmt.where(RoutingDecisionRow.agent_id == agent_id)
        if reason is not None:
            stmt = stmt.where(RoutingDecisionRow.reason == reason)
        if tenant_id is not None:
            stmt = stmt.where(RoutingDecisionRow.tenant_id == tenant_id)
        stmt = stmt.order_by(
            RoutingDecisionRow.created_at.desc()
        ).limit(limit)
        async with self._sf() as session:
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [self._to_dict(r) for r in rows]

    @staticmethod
    def _to_dict(row: RoutingDecisionRow) -> dict[str, Any]:
        return {
            "run_id": row.run_id,
            "agent_id": row.agent_id,
            "reason": row.reason,
            "deployment_id": row.deployment_id,
            "traffic_bucket": row.traffic_bucket,
            "latency_ms": row.latency_ms,
            "context": row.context_json or {},
            "tenant_id": row.tenant_id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }


# ------------------------------------------------------------------
# CodingJob
# ------------------------------------------------------------------


class SqlCodingJobRepository:
    """DevFlow coding job 的 SQL 存储实现。"""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self._sf = session_factory

    async def save(self, job_data: dict[str, Any]) -> None:
        job_id = job_data.get("job_id", "")
        async with self._sf() as session:
            stmt = select(CodingJobRow).where(CodingJobRow.job_id == job_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                row = CodingJobRow(
                    job_id=job_id,
                    state=job_data.get("state", "pending"),
                    data_json=job_data,
                )
                _fill_audit(row)
                session.add(row)
            else:
                row.state = job_data.get("state", row.state)
                row.data_json = job_data
            await session.commit()

    async def get(self, job_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            stmt = select(CodingJobRow).where(CodingJobRow.job_id == job_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return row.data_json or {}

    async def list_jobs(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        stmt = select(CodingJobRow)
        if status is not None:
            stmt = stmt.where(CodingJobRow.state == status)
        stmt = stmt.order_by(CodingJobRow.created_at.desc()).limit(limit)
        async with self._sf() as session:
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [r.data_json or {} for r in rows]


# ------------------------------------------------------------------
# DeadLetterQueue
# ------------------------------------------------------------------


class SqlDeadLetterQueue:
    """基于 SQLAlchemy 的 Dead Letter Queue 实现。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def enqueue(self, entry: DeadLetterEntry) -> None:
        """将失败条目入队。"""
        row = DeadLetterEntryModel(
            id=entry.id,
            source=entry.source,
            event_type=entry.event_type,
            payload=entry.payload,
            error_message=entry.error_message,
            retry_count=entry.retry_count,
            max_retries=entry.max_retries,
            next_retry_at=entry.next_retry_at,
            status=entry.status,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
        )
        _fill_audit(row)
        async with self._sf() as session:
            session.add(row)
            await session.commit()

    async def dequeue_ready(self) -> list[DeadLetterEntry]:
        """获取已到达重试时间的条目。"""
        now = datetime.now(UTC)
        async with self._sf() as session:
            stmt = select(DeadLetterEntryModel).where(
                DeadLetterEntryModel.status.in_(("pending", "retrying")),
                DeadLetterEntryModel.next_retry_at <= now,
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()

            entries = []
            for row in rows:
                entries.append(
                    DeadLetterEntry(
                        id=row.id,
                        source=row.source,
                        event_type=row.event_type,
                        payload=row.payload or {},
                        error_message=row.error_message,
                        retry_count=row.retry_count,
                        max_retries=row.max_retries,
                        next_retry_at=row.next_retry_at,
                        status=row.status,
                        created_at=row.created_at,
                        updated_at=row.updated_at,
                    )
                )
            return entries

    async def mark_resolved(self, entry_id: str) -> None:
        """标记条目为已解决。"""
        async with self._sf() as session:
            stmt = select(DeadLetterEntryModel).where(DeadLetterEntryModel.id == entry_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row:
                row.status = "resolved"
                row.updated_at = datetime.now(UTC)
                await session.commit()

    async def mark_exhausted(self, entry_id: str) -> None:
        """标记条目为已耗尽重试次数。"""
        async with self._sf() as session:
            stmt = select(DeadLetterEntryModel).where(DeadLetterEntryModel.id == entry_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row:
                row.status = "exhausted"
                row.updated_at = datetime.now(UTC)
                await session.commit()

    async def update_retry(
        self, entry_id: str, next_retry_at: datetime, retry_count: int
    ) -> None:
        """更新条目的重试信息。"""
        async with self._sf() as session:
            stmt = select(DeadLetterEntryModel).where(DeadLetterEntryModel.id == entry_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row:
                row.next_retry_at = next_retry_at
                row.retry_count = retry_count
                row.status = "retrying"
                row.updated_at = datetime.now(UTC)
                await session.commit()

    async def list_entries(
        self, status: str | None = None, limit: int = 100
    ) -> list[DeadLetterEntry]:
        """列出条目，可按状态过滤。"""
        async with self._sf() as session:
            stmt = select(DeadLetterEntryModel)
            if status:
                stmt = stmt.where(DeadLetterEntryModel.status == status)
            stmt = stmt.order_by(DeadLetterEntryModel.created_at.desc()).limit(limit)
            result = await session.execute(stmt)
            rows = result.scalars().all()

            entries = []
            for row in rows:
                entries.append(
                    DeadLetterEntry(
                        id=row.id,
                        source=row.source,
                        event_type=row.event_type,
                        payload=row.payload or {},
                        error_message=row.error_message,
                        retry_count=row.retry_count,
                        max_retries=row.max_retries,
                        next_retry_at=row.next_retry_at,
                        status=row.status,
                        created_at=row.created_at,
                        updated_at=row.updated_at,
                    )
                )
            return entries

    async def purge_resolved(self, older_than_days: int = 7) -> int:
        """清除已解决的旧条目，返回清除数量。"""
        from datetime import timedelta
        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        
        async with self._sf() as session:
            # We select first to count, then delete, or use delete with returning, but sqlite support varies.
            # Using select and delete for simplicity and compatibility
            from sqlalchemy import delete
            stmt = delete(DeadLetterEntryModel).where(
                DeadLetterEntryModel.status == "resolved",
                DeadLetterEntryModel.updated_at < cutoff
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount

    async def get_entry(self, entry_id: str) -> DeadLetterEntry | None:
        """按 ID 获取单条条目。"""
        async with self._sf() as session:
            stmt = select(DeadLetterEntryModel).where(DeadLetterEntryModel.id == entry_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if not row:
                return None
            return DeadLetterEntry(
                id=row.id,
                source=row.source,
                event_type=row.event_type,
                payload=row.payload or {},
                error_message=row.error_message,
                retry_count=row.retry_count,
                max_retries=row.max_retries,
                next_retry_at=row.next_retry_at,
                status=row.status,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )


# ------------------------------------------------------------------
# ExecutionLog
# ------------------------------------------------------------------


class SqlExecutionLogRepository:
    """Runner 执行日志的 SQL 存储实现。"""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self._sf = session_factory

    async def record(self, entry: ExecutionLogEntry) -> None:
        row = ExecutionLogRow(
            job_id=entry.job_id,
            stream=entry.stream.value,
            content=entry.content,
            adapter_name=entry.adapter_name,
            logged_at=entry.timestamp,
        )
        _fill_audit(row)
        async with self._sf() as session:
            session.add(row)
            await session.commit()

    async def get_logs(
        self,
        job_id: str,
        stream: LogStream | None = None,
    ) -> list[ExecutionLogEntry]:
        stmt = (
            select(ExecutionLogRow)
            .where(ExecutionLogRow.job_id == job_id)
        )
        if stream is not None:
            stmt = stmt.where(ExecutionLogRow.stream == stream.value)
        stmt = stmt.order_by(ExecutionLogRow.logged_at.asc())

        async with self._sf() as session:
            result = await session.execute(stmt)
            rows = result.scalars().all()

        return [
            ExecutionLogEntry(
                job_id=r.job_id,
                timestamp=r.logged_at,
                stream=LogStream(r.stream),
                content=r.content,
                adapter_name=r.adapter_name,
            )
            for r in rows
        ]

    async def list_jobs_with_logs(self, limit: int = 50) -> list[str]:
        stmt = (
            select(
                ExecutionLogRow.job_id,
                func.max(ExecutionLogRow.logged_at).label("last_active"),
            )
            .group_by(ExecutionLogRow.job_id)
            .order_by(func.max(ExecutionLogRow.logged_at).desc())
            .limit(limit)
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]


class SqlProposalRepository:
    """ImprovementProposal 的 SQL 持久化实现。"""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sf = session_factory

    async def create(self, proposal: "ImprovementProposal") -> None:
        row = EvolutionProposalRow(
            id=proposal.proposal_id,
            proposal_id=proposal.proposal_id,
            title=proposal.title,
            summary=proposal.summary,
            agent_id=proposal.agent_id,
            tenant_id=proposal.tenant_id,
            task_type=proposal.task_type,
            source=proposal.source,
            status=proposal.status,
            risk_level=proposal.risk.level,
            risk_reason=proposal.risk.reason,
            root_cause_category=proposal.root_cause.category,
            root_cause_confidence=proposal.root_cause.confidence,
            root_cause_explanation=proposal.root_cause.explanation,
            evidence_json=[e.model_dump(mode="json") for e in proposal.evidence],
            proposed_changes_json=[c.model_dump(mode="json") for c in proposal.proposed_changes],
            allowed_paths_json=proposal.allowed_paths,
            blocked_paths_json=proposal.blocked_paths,
            validation_json=proposal.validation.model_dump(mode="json"),
            plane_work_item_id=proposal.plane_work_item_id,
            gitlab_mr_iid=proposal.gitlab_mr_iid,
            metadata_json=proposal.metadata,
        )
        _fill_audit(row)
        async with self._sf() as session:
            session.add(row)
            await session.commit()

    async def get(self, proposal_id: str) -> "ImprovementProposal | None":
        stmt = select(EvolutionProposalRow).where(
            EvolutionProposalRow.proposal_id == proposal_id,
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return self._to_model(row)

    async def list_by_agent(
        self,
        agent_id: str,
        status: "ProposalStatus | None" = None,
        limit: int = 50,
    ) -> "list[ImprovementProposal]":
        stmt = select(EvolutionProposalRow).where(
            EvolutionProposalRow.agent_id == agent_id,
        )
        if status is not None:
            stmt = stmt.where(EvolutionProposalRow.status == str(status))
        stmt = stmt.order_by(EvolutionProposalRow.created_at.desc()).limit(limit)
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._to_model(r) for r in result.scalars().all()]

    async def list_all(
        self,
        status: Any | None = None,
        limit: int = 100,
    ) -> list[Any]:
        stmt = select(EvolutionProposalRow)
        if status is not None:
            stmt = stmt.where(EvolutionProposalRow.status == str(status))
        stmt = stmt.order_by(EvolutionProposalRow.created_at.desc()).limit(limit)
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._to_model(r) for r in result.scalars().all()]

    async def update_status(
        self,
        proposal_id: str,
        status: "ProposalStatus",
        **kwargs: object,
    ) -> None:
        async with self._sf() as session:
            stmt = select(EvolutionProposalRow).where(
                EvolutionProposalRow.proposal_id == proposal_id,
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return
            row.status = str(status)
            row.updated_at = datetime.now(UTC)
            if str(status) == "dispatched":
                pwi = kwargs.get("plane_work_item_id")
                if pwi:
                    row.plane_work_item_id = str(pwi)
            elif str(status) in ("closed", "dismissed"):
                row.closed_at = datetime.now(UTC)
                outcome = kwargs.get("outcome")
                if outcome:
                    row.outcome = str(outcome)
            await session.commit()

    @staticmethod
    def _to_model(row: EvolutionProposalRow) -> "ImprovementProposal":
        from agent_platform.evolution.models import (
            Evidence,
            ImprovementProposal,
            ProposalSource,
            ProposalStatus,
            RiskAssessment,
            RiskLevel,
            RootCause,
            RootCauseCategory,
            ProposedChange,
            ValidationSpec,
        )
        return ImprovementProposal(
            proposal_id=row.proposal_id,
            title=row.title,
            summary=row.summary,
            agent_id=row.agent_id,
            tenant_id=row.tenant_id or "default",
            task_type=row.task_type,
            source=ProposalSource(row.source),
            status=ProposalStatus(row.status),
            risk=RiskAssessment(
                level=RiskLevel(row.risk_level),
                reason=row.risk_reason,
            ),
            root_cause=RootCause(
                category=RootCauseCategory(row.root_cause_category),
                confidence=row.root_cause_confidence,
                explanation=row.root_cause_explanation,
            ),
            evidence=[Evidence(**e) for e in (row.evidence_json or [])],
            proposed_changes=[ProposedChange(**c) for c in (row.proposed_changes_json or [])],
            allowed_paths=row.allowed_paths_json or [],
            blocked_paths=row.blocked_paths_json or [],
            validation=ValidationSpec(**(row.validation_json or {})),
            plane_work_item_id=row.plane_work_item_id,
            gitlab_mr_iid=row.gitlab_mr_iid,
            created_at=row.created_at,
            updated_at=row.updated_at,
            closed_at=row.closed_at,
            outcome=row.outcome,
            metadata=row.metadata_json or {},
        )


class SqlEvolutionMemoryRepository:
    """EvolutionMemory 的 SQL 持久化实现。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(self, memory: "EvolutionMemory") -> None:
        row = EvolutionMemoryRow(
            id=memory.memory_id,
            memory_id=memory.memory_id,
            agent_id=memory.agent_id,
            tenant_id=memory.tenant_id,
            type=memory.type.value,
            content=memory.content,
            confidence=memory.confidence,
            trust_score=memory.trust_score,
            status=memory.status.value,
            source_proposal_id=memory.source_proposal_id,
            source_type=memory.source_type,
            tags_json=memory.tags,
            use_count=memory.use_count,
            helpful_count=memory.helpful_count,
            unhelpful_count=memory.unhelpful_count,
            metadata_json=memory.metadata,
            created_at=memory.created_at,
            updated_at=memory.updated_at,
        )
        _fill_audit(row)
        async with self._sf() as session:
            session.add(row)
            await session.commit()

    async def get(self, memory_id: str) -> "EvolutionMemory | None":
        stmt = select(EvolutionMemoryRow).where(EvolutionMemoryRow.memory_id == memory_id)
        async with self._sf() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return self._to_model(row)

    async def list_by_agent(
        self,
        agent_id: str,
        *,
        memory_type: Any | None = None,
        status: Any | None = None,
        limit: int = 50,
    ) -> "list[EvolutionMemory]":
        stmt = select(EvolutionMemoryRow).where(EvolutionMemoryRow.agent_id == agent_id)
        if memory_type is not None:
            stmt = stmt.where(EvolutionMemoryRow.type == str(memory_type))
        if status is not None:
            stmt = stmt.where(EvolutionMemoryRow.status == str(status))
        stmt = stmt.order_by(EvolutionMemoryRow.created_at.desc()).limit(limit)
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._to_model(r) for r in result.scalars().all()]

    async def list_by_tenant(
        self,
        tenant_id: str,
        *,
        memory_type: Any | None = None,
        status: Any | None = None,
        limit: int = 50,
    ) -> "list[EvolutionMemory]":
        stmt = select(EvolutionMemoryRow).where(EvolutionMemoryRow.tenant_id == tenant_id)
        if memory_type is not None:
            stmt = stmt.where(EvolutionMemoryRow.type == str(memory_type))
        if status is not None:
            stmt = stmt.where(EvolutionMemoryRow.status == str(status))
        stmt = stmt.order_by(EvolutionMemoryRow.created_at.desc()).limit(limit)
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._to_model(r) for r in result.scalars().all()]

    async def list_all(
        self,
        *,
        memory_type: Any | None = None,
        status: Any | None = None,
        limit: int = 100,
    ) -> "list[EvolutionMemory]":
        stmt = select(EvolutionMemoryRow)
        if memory_type is not None:
            stmt = stmt.where(EvolutionMemoryRow.type == str(memory_type))
        if status is not None:
            stmt = stmt.where(EvolutionMemoryRow.status == str(status))
        stmt = stmt.order_by(EvolutionMemoryRow.created_at.desc()).limit(limit)
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._to_model(r) for r in result.scalars().all()]

    async def update(self, memory: "EvolutionMemory") -> None:
        async with self._sf() as session:
            stmt = select(EvolutionMemoryRow).where(EvolutionMemoryRow.memory_id == memory.memory_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return
            row.type = memory.type.value
            row.content = memory.content
            row.confidence = memory.confidence
            row.trust_score = memory.trust_score
            row.status = memory.status.value
            row.source_proposal_id = memory.source_proposal_id
            row.source_type = memory.source_type
            row.tags_json = memory.tags
            row.use_count = memory.use_count
            row.helpful_count = memory.helpful_count
            row.unhelpful_count = memory.unhelpful_count
            row.metadata_json = memory.metadata
            row.updated_at = datetime.now(UTC)
            await session.commit()

    async def delete(self, memory_id: str) -> bool:
        async with self._sf() as session:
            stmt = select(EvolutionMemoryRow).where(EvolutionMemoryRow.memory_id == memory_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    @staticmethod
    def _to_model(row: EvolutionMemoryRow) -> "EvolutionMemory":
        from agent_platform.evolution.memory_models import EvolutionMemory, MemoryStatus, MemoryType
        return EvolutionMemory(
            memory_id=row.memory_id,
            agent_id=row.agent_id,
            tenant_id=row.tenant_id or "default",
            type=MemoryType(row.type),
            content=row.content,
            confidence=row.confidence,
            trust_score=row.trust_score,
            status=MemoryStatus(row.status),
            source_proposal_id=row.source_proposal_id,
            source_type=row.source_type,
            tags=row.tags_json or [],
            use_count=row.use_count,
            helpful_count=row.helpful_count,
            unhelpful_count=row.unhelpful_count,
            created_at=row.created_at,
            updated_at=row.updated_at,
            metadata=row.metadata_json or {},
        )


class SqlRuntimeMemoryRepository:
    """RuntimeMemory 的 SQL 持久化实现。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(self, memory: "RuntimeMemory") -> None:
        row = RuntimeMemoryRow(
            id=memory.memory_id,
            memory_id=memory.memory_id,
            agent_id=memory.agent_id,
            tenant_id=memory.tenant_id,
            scope=memory.scope.value,
            subject_id=memory.subject_id,
            session_id=memory.session_id,
            type=memory.type.value,
            content=memory.content,
            source_type=memory.source_type,
            source_id=memory.source_id,
            confidence=memory.confidence,
            privacy_level=memory.privacy_level,
            status=memory.status.value,
            ttl_seconds=memory.ttl_seconds,
            expires_at=memory.expires_at,
            metadata_json=memory.metadata,
            created_at=memory.created_at,
        )
        _fill_audit(row)
        async with self._sf() as session:
            session.add(row)
            await session.commit()

    async def get(self, memory_id: str) -> "RuntimeMemory | None":
        stmt = select(RuntimeMemoryRow).where(RuntimeMemoryRow.memory_id == memory_id)
        async with self._sf() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            model = self._to_model(row)
            if model.is_expired():
                return None
            return model

    async def list_by_agent(
        self,
        agent_id: str,
        *,
        scope: Any | None = None,
        status: Any | None = None,
        limit: int = 50,
    ) -> "list[RuntimeMemory]":
        stmt = select(RuntimeMemoryRow).where(RuntimeMemoryRow.agent_id == agent_id)
        if scope is not None:
            stmt = stmt.where(RuntimeMemoryRow.scope == str(scope))
        if status is not None:
            stmt = stmt.where(RuntimeMemoryRow.status == str(status))
        async with self._sf() as session:
            result = await session.execute(stmt)
            models = [self._to_model(r) for r in result.scalars().all()]
            # 动态排除已过期的
            valid_models = [m for m in models if not m.is_expired()]
            return sorted(valid_models, key=lambda m: m.created_at, reverse=True)[:limit]

    async def list_by_user(
        self,
        user_id: str,
        *,
        status: Any | None = None,
        limit: int = 50,
    ) -> "list[RuntimeMemory]":
        from agent_platform.evolution.memory_models import RuntimeMemoryScope
        stmt = select(RuntimeMemoryRow).where(
            RuntimeMemoryRow.scope == RuntimeMemoryScope.USER.value,
            RuntimeMemoryRow.subject_id == user_id,
        )
        if status is not None:
            stmt = stmt.where(RuntimeMemoryRow.status == str(status))
        async with self._sf() as session:
            result = await session.execute(stmt)
            models = [self._to_model(r) for r in result.scalars().all()]
            valid_models = [m for m in models if not m.is_expired()]
            return sorted(valid_models, key=lambda m: m.created_at, reverse=True)[:limit]

    async def list_by_session(
        self,
        session_id: str,
        *,
        status: Any | None = None,
        limit: int = 50,
    ) -> "list[RuntimeMemory]":
        stmt = select(RuntimeMemoryRow).where(RuntimeMemoryRow.session_id == session_id)
        if status is not None:
            stmt = stmt.where(RuntimeMemoryRow.status == str(status))
        async with self._sf() as session:
            result = await session.execute(stmt)
            models = [self._to_model(r) for r in result.scalars().all()]
            valid_models = [m for m in models if not m.is_expired()]
            return sorted(valid_models, key=lambda m: m.created_at, reverse=True)[:limit]

    async def list_by_tenant(
        self,
        tenant_id: str,
        *,
        scope: Any | None = None,
        status: Any | None = None,
        limit: int = 50,
    ) -> "list[RuntimeMemory]":
        stmt = select(RuntimeMemoryRow).where(RuntimeMemoryRow.tenant_id == tenant_id)
        if scope is not None:
            stmt = stmt.where(RuntimeMemoryRow.scope == str(scope))
        if status is not None:
            stmt = stmt.where(RuntimeMemoryRow.status == str(status))
        async with self._sf() as session:
            result = await session.execute(stmt)
            models = [self._to_model(r) for r in result.scalars().all()]
            valid_models = [m for m in models if not m.is_expired()]
            return sorted(valid_models, key=lambda m: m.created_at, reverse=True)[:limit]

    async def list_all(
        self,
        *,
        scope: Any | None = None,
        status: Any | None = None,
        limit: int = 100,
    ) -> "list[RuntimeMemory]":
        stmt = select(RuntimeMemoryRow)
        if scope is not None:
            stmt = stmt.where(RuntimeMemoryRow.scope == str(scope))
        if status is not None:
            stmt = stmt.where(RuntimeMemoryRow.status == str(status))
        async with self._sf() as session:
            result = await session.execute(stmt)
            models = [self._to_model(r) for r in result.scalars().all()]
            valid_models = [m for m in models if not m.is_expired()]
            return sorted(valid_models, key=lambda m: m.created_at, reverse=True)[:limit]

    async def update(self, memory: "RuntimeMemory") -> None:
        async with self._sf() as session:
            stmt = select(RuntimeMemoryRow).where(RuntimeMemoryRow.memory_id == memory.memory_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return
            row.scope = memory.scope.value
            row.subject_id = memory.subject_id
            row.session_id = memory.session_id
            row.type = memory.type.value
            row.content = memory.content
            row.source_type = memory.source_type
            row.source_id = memory.source_id
            row.confidence = memory.confidence
            row.privacy_level = memory.privacy_level
            row.status = memory.status.value
            row.ttl_seconds = memory.ttl_seconds
            row.expires_at = memory.expires_at
            row.metadata_json = memory.metadata
            row.updated_at = datetime.now(UTC)
            await session.commit()

    async def delete(self, memory_id: str) -> bool:
        async with self._sf() as session:
            stmt = select(RuntimeMemoryRow).where(RuntimeMemoryRow.memory_id == memory_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    @staticmethod
    def _to_model(row: RuntimeMemoryRow) -> "RuntimeMemory":
        from agent_platform.evolution.memory_models import RuntimeMemory, MemoryStatus, RuntimeMemoryScope, RuntimeMemoryType
        return RuntimeMemory(
            memory_id=row.memory_id,
            tenant_id=row.tenant_id or "default",
            agent_id=row.agent_id,
            scope=RuntimeMemoryScope(row.scope),
            subject_id=row.subject_id,
            session_id=row.session_id,
            type=RuntimeMemoryType(row.type),
            content=row.content,
            source_type=row.source_type,
            source_id=row.source_id,
            confidence=row.confidence,
            privacy_level=row.privacy_level,
            status=MemoryStatus(row.status),
            ttl_seconds=row.ttl_seconds,
            created_by=row.created_by,
            created_at=row.created_at,
            expires_at=row.expires_at,
            metadata=row.metadata_json or {},
        )


class SqlSkillRepository:
    """SkillEntry 的 SQL 持久化实现。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(self, skill: "SkillEntry") -> None:
        row = SkillEntryRow(
            id=skill.skill_id,
            skill_id=skill.skill_id,
            agent_id=skill.agent_id,
            name=skill.name,
            description=skill.description,
            path=skill.path,
            provenance=skill.provenance.value,
            status=skill.status.value,
            tags_json=skill.tags,
            use_count=skill.use_count,
            view_count=skill.view_count,
            last_used_at=skill.last_used_at,
            metadata_json=skill.metadata,
            created_at=skill.created_at,
            updated_at=skill.updated_at,
        )
        _fill_audit(row)
        async with self._sf() as session:
            session.add(row)
            await session.commit()

    async def get(self, skill_id: str) -> "SkillEntry | None":
        stmt = select(SkillEntryRow).where(SkillEntryRow.skill_id == skill_id)
        async with self._sf() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return self._to_model(row)

    async def list_by_agent(
        self,
        agent_id: str,
        *,
        status: Any | None = None,
        limit: int = 50,
    ) -> "list[SkillEntry]":
        stmt = select(SkillEntryRow).where(SkillEntryRow.agent_id == agent_id)
        if status is not None:
            stmt = stmt.where(SkillEntryRow.status == str(status))
        stmt = stmt.order_by(SkillEntryRow.created_at.desc()).limit(limit)
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._to_model(r) for r in result.scalars().all()]

    async def list_all(
        self,
        *,
        status: Any | None = None,
        limit: int = 100,
    ) -> "list[SkillEntry]":
        stmt = select(SkillEntryRow)
        if status is not None:
            stmt = stmt.where(SkillEntryRow.status == str(status))
        stmt = stmt.order_by(SkillEntryRow.created_at.desc()).limit(limit)
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._to_model(r) for r in result.scalars().all()]

    async def update(self, skill: "SkillEntry") -> None:
        async with self._sf() as session:
            stmt = select(SkillEntryRow).where(SkillEntryRow.skill_id == skill.skill_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return
            row.name = skill.name
            row.description = skill.description
            row.path = skill.path
            row.provenance = skill.provenance.value
            row.status = skill.status.value
            row.tags_json = skill.tags
            row.use_count = skill.use_count
            row.view_count = skill.view_count
            row.last_used_at = skill.last_used_at
            row.metadata_json = skill.metadata
            row.updated_at = datetime.now(UTC)
            await session.commit()

    async def delete(self, skill_id: str) -> bool:
        async with self._sf() as session:
            stmt = select(SkillEntryRow).where(SkillEntryRow.skill_id == skill_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    @staticmethod
    def _to_model(row: SkillEntryRow) -> "SkillEntry":
        from agent_platform.evolution.memory_models import SkillEntry, MemoryStatus, SkillProvenance
        return SkillEntry(
            skill_id=row.skill_id,
            agent_id=row.agent_id,
            name=row.name,
            description=row.description,
            path=row.path,
            provenance=SkillProvenance(row.provenance),
            status=MemoryStatus(row.status),
            tags=row.tags_json or [],
            use_count=row.use_count,
            view_count=row.view_count,
            last_used_at=row.last_used_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
            metadata=row.metadata_json or {},
        )


class SqlCandidateRepository:
    """Candidate 的 SQL 持久化实现。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(self, candidate: "Candidate") -> None:
        row = CandidateRow(
            id=candidate.candidate_id,
            candidate_id=candidate.candidate_id,
            candidate_type=candidate.candidate_type.value,
            generated_by=candidate.generated_by,
            generator_role=candidate.generator_role,
            agent_id=candidate.agent_id,
            tenant_id=candidate.tenant_id,
            environment=candidate.environment,
            source_event_ids_json=candidate.source_event_ids,
            evidence_ids_json=candidate.evidence_ids,
            payload_json=candidate.payload,
            risk_level=candidate.risk_level.value,
            status=candidate.status.value,
            promotion_target=candidate.promotion_target.value,
            validation_errors_json=candidate.validation_errors,
            created_at=candidate.created_at,
            updated_at=candidate.updated_at,
            promoted_at=candidate.promoted_at,
        )
        _fill_audit(row)
        async with self._sf() as session:
            session.add(row)
            await session.commit()

    async def get(self, candidate_id: str) -> "Candidate | None":
        stmt = select(CandidateRow).where(CandidateRow.candidate_id == candidate_id)
        async with self._sf() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return self._to_model(row)

    async def list_all(
        self,
        *,
        candidate_type: Any | None = None,
        agent_id: str | None = None,
        status: Any | None = None,
        limit: int = 100,
    ) -> "list[Candidate]":
        stmt = select(CandidateRow)
        if candidate_type is not None:
            stmt = stmt.where(CandidateRow.candidate_type == str(candidate_type))
        if agent_id is not None:
            stmt = stmt.where(CandidateRow.agent_id == agent_id)
        if status is not None:
            stmt = stmt.where(CandidateRow.status == str(status))
        stmt = stmt.order_by(CandidateRow.created_at.desc()).limit(limit)
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._to_model(r) for r in result.scalars().all()]

    async def update_status(
        self,
        candidate_id: str,
        status: "CandidateStatus",
        *,
        validation_errors: list[str] | None = None,
    ) -> None:
        async with self._sf() as session:
            stmt = select(CandidateRow).where(CandidateRow.candidate_id == candidate_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return
            row.status = status.value
            row.updated_at = datetime.now(UTC)
            from agent_platform.evolution.models import CandidateStatus
            if status == CandidateStatus.PROMOTED:
                row.promoted_at = datetime.now(UTC)
            if validation_errors is not None:
                row.validation_errors_json = validation_errors
            await session.commit()

    async def delete(self, candidate_id: str) -> None:
        async with self._sf() as session:
            stmt = select(CandidateRow).where(CandidateRow.candidate_id == candidate_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is not None:
                await session.delete(row)
                await session.commit()

    @staticmethod
    def _to_model(row: CandidateRow) -> "Candidate":
        from agent_platform.evolution.models import Candidate, CandidateType, CandidateStatus, PromotionTarget, RiskLevel
        return Candidate(
            candidate_id=row.candidate_id,
            candidate_type=CandidateType(row.candidate_type),
            generated_by=row.generated_by,
            generator_role=row.generator_role,
            tenant_id=row.tenant_id or "default",
            agent_id=row.agent_id,
            environment=row.environment,
            source_event_ids=row.source_event_ids_json or [],
            evidence_ids=row.evidence_ids_json or [],
            payload=row.payload_json or {},
            risk_level=RiskLevel(row.risk_level),
            status=CandidateStatus(row.status),
            promotion_target=PromotionTarget(row.promotion_target),
            validation_errors=row.validation_errors_json or [],
            created_at=row.created_at,
            updated_at=row.updated_at,
            promoted_at=row.promoted_at,
        )


class SqlReviewForkAuditRepository:
    """ReviewForkAudit 的 SQL 持久化实现。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(self, audit: "ReviewForkAudit") -> None:
        row = ReviewForkAuditRow(
            id=audit.review_fork_id,
            review_fork_id=audit.review_fork_id,
            source_event_id=audit.source_event_id,
            source_event_type=audit.source_event_type,
            agent_id=audit.agent_id,
            tenant_id=audit.tenant_id,
            input_evidence_ids_json=audit.input_evidence_ids,
            output_type=audit.output_type,
            candidate_id=audit.candidate_id,
            proposal_id=audit.proposal_id,
            risk_level=audit.risk_level,
            model_provider=audit.model_provider,
            status=audit.status,
            error_message=audit.error_message,
            created_at=audit.created_at,
        )
        _fill_audit(row)
        async with self._sf() as session:
            session.add(row)
            await session.commit()

    async def get(self, review_fork_id: str) -> "ReviewForkAudit | None":
        stmt = select(ReviewForkAuditRow).where(ReviewForkAuditRow.review_fork_id == review_fork_id)
        async with self._sf() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return self._to_model(row)

    async def list_all(
        self,
        *,
        agent_id: str | None = None,
        limit: int = 100,
    ) -> "list[ReviewForkAudit]":
        stmt = select(ReviewForkAuditRow)
        if agent_id is not None:
            stmt = stmt.where(ReviewForkAuditRow.agent_id == agent_id)
        stmt = stmt.order_by(ReviewForkAuditRow.created_at.desc()).limit(limit)
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._to_model(r) for r in result.scalars().all()]

    @staticmethod
    def _to_model(row: ReviewForkAuditRow) -> "ReviewForkAudit":
        from agent_platform.evolution.review_fork import ReviewForkAudit
        return ReviewForkAudit(
            review_fork_id=row.review_fork_id,
            source_event_id=row.source_event_id,
            source_event_type=row.source_event_type,
            agent_id=row.agent_id,
            tenant_id=row.tenant_id or "default",
            input_evidence_ids=row.input_evidence_ids_json or [],
            output_type=row.output_type,
            candidate_id=row.candidate_id,
            proposal_id=row.proposal_id,
            risk_level=row.risk_level,
            model_provider=row.model_provider,
            status=row.status,
            error_message=row.error_message,
            created_at=row.created_at,
        )

