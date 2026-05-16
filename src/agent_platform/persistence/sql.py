from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
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
)
from agent_platform.persistence.context import get_audit_context
from agent_platform.persistence.tables import (
    AgentDefinitionRow,
    AgentDeploymentRow,
    AgentRunRow,
    AgentSessionRow,
    DeploymentAuditEventRow,
    EvalRunRow,
    WebhookDeliveryRow,
)
from agent_platform.registry.deployment import DeploymentEvent


def _fill_audit(row: Any) -> None:
    """Fill audit fields from the current AuditContext."""
    ctx = get_audit_context()
    row.created_by = ctx.actor
    row.request_id = ctx.request_id
    if ctx.tenant_id is not None:
        row.tenant_id = ctx.tenant_id


# ------------------------------------------------------------------
# AgentDefinition
# ------------------------------------------------------------------


class SqlAgentDefinitionRepository:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self._sf = session_factory

    async def save(
        self, definition: AgentDefinition
    ) -> None:
        row = AgentDefinitionRow(
            agent_id=definition.agent_id,
            version=definition.version,
            status=definition.status.value,
            manifest_json=definition.manifest.model_dump(
                mode="json"
            ),
            created_at=definition.created_at,
            updated_at=definition.updated_at,
        )
        _fill_audit(row)
        async with self._sf() as session:
            session.add(row)
            await session.commit()

    async def get(
        self, agent_id: str, version: str
    ) -> AgentDefinition | None:
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
    ) -> None:
        stmt = select(AgentDefinitionRow).where(
            AgentDefinitionRow.agent_id == agent_id,
            AgentDefinitionRow.version == version,
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is not None:
                row.status = status
                row.updated_at = datetime.now(UTC)
                await session.commit()

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
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self._sf = session_factory

    async def save(
        self, deployment: AgentDeployment
    ) -> None:
        row = AgentDeploymentRow(
            deployment_id=deployment.deployment_id,
            agent_id=deployment.agent_id,
            version=deployment.version,
            channel=deployment.channel,
            status=deployment.status.value,
            traffic_percent=deployment.traffic_percent,
        )
        if deployment.tenant_id is not None:
            row.tenant_id = deployment.tenant_id
        _fill_audit(row)
        async with self._sf() as session:
            session.add(row)
            await session.commit()

    async def get(
        self, deployment_id: str
    ) -> AgentDeployment | None:
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
        stmt = select(AgentDeploymentRow).where(
            AgentDeploymentRow.agent_id == agent_id,
            AgentDeploymentRow.channel == channel,
        )
        if tenant_id is not None:
            stmt = stmt.where(
                AgentDeploymentRow.tenant_id == tenant_id
            )
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
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self._sf = session_factory

    async def record(
        self, event: DeploymentEvent
    ) -> None:
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
        )


# ------------------------------------------------------------------
# AgentRun
# ------------------------------------------------------------------


class SqlAgentRunRepository:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self._sf = session_factory

    async def record(self, run: AgentRun) -> None:
        row = AgentRunRow(
            run_id=run.run_id,
            request_id=run.request_id,
            session_id=run.session_id,
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
        )
        _fill_audit(row)
        async with self._sf() as session:
            session.add(row)
            await session.commit()

    async def get(
        self, run_id: str
    ) -> AgentRun | None:
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
        return AgentRun(
            run_id=row.run_id,
            request_id=row.request_id,
            session_id=row.session_id,
            agent_id=row.agent_id,
            agent_version=row.agent_version,
            route_reason=row.route_reason,
            runtime_backend=row.runtime_backend,
            status=AgentRunStatus(row.status),
            latency_ms=row.latency_ms,
            tool_calls=tool_calls,
            error=error,
            metadata=row.metadata_json or {},
        )


# ------------------------------------------------------------------
# AgentSession
# ------------------------------------------------------------------


class SqlAgentSessionRepository:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self._sf = session_factory

    async def save(
        self, session: AgentSession
    ) -> None:
        row = AgentSessionRow(
            session_id=session.session_id,
            agent_id=session.agent_id,
            location_id=session.location_id,
            user_id=session.user_id,
            channel_id=session.channel_id,
            history_json=[
                m.model_dump(mode="json")
                for m in session.history
            ],
            state_snapshot_json=session.state_snapshot,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )
        if session.tenant_id is not None:
            row.tenant_id = session.tenant_id
        _fill_audit(row)
        async with self._sf() as db:
            db.add(row)
            await db.commit()

    async def load(
        self, session_id: str
    ) -> AgentSession | None:
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
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self._sf = session_factory

    async def exists(self, delivery_id: str) -> bool:
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
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
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
