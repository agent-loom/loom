"""部署审计日志：记录部署事件，支持回滚追踪。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from agent_platform.domain.models import AgentDeployment, AgentDeploymentStatus

if TYPE_CHECKING:
    from agent_platform.persistence.repositories import DeploymentAuditRepository

logger = logging.getLogger(__name__)


class DeploymentEvent(BaseModel):
    """单条部署事件记录。"""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_type: str
    agent_id: str
    version: str
    channel: str
    traffic_percent: int = 100
    status: AgentDeploymentStatus
    previous_version: str | None = None
    actor: str = "system"
    artifact_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DeploymentAuditLog:
    """Records all deployment events for audit trail and rollback support."""

    def __init__(self, *, repo: DeploymentAuditRepository | None = None) -> None:
        if repo is None:
            from agent_platform.persistence.memory import InMemoryDeploymentAuditRepository
            self._repo = InMemoryDeploymentAuditRepository()
        else:
            self._repo = repo

    async def record_deploy(
        self,
        deployment: AgentDeployment,
        previous_version: str | None = None,
        actor: str = "system",
        artifact_id: str | None = None,
    ) -> DeploymentEvent:
        """记录一次部署事件。"""
        event = DeploymentEvent(
            event_type="deploy",
            agent_id=deployment.agent_id,
            version=deployment.version,
            channel=deployment.channel,
            traffic_percent=deployment.traffic_percent,
            status=deployment.status,
            previous_version=previous_version,
            actor=actor,
            artifact_id=artifact_id,
        )
        await self._repo.record(event)

        logger.info(
            "deployment event: %s %s@%s -> %s (channel=%s, traffic=%d%%)",
            event.event_type,
            event.agent_id,
            event.version,
            event.status,
            event.channel,
            event.traffic_percent,
        )
        return event

    async def record_rollback(
        self,
        agent_id: str,
        channel: str,
        from_version: str,
        to_version: str,
        actor: str = "system",
    ) -> DeploymentEvent:
        """记录一次回滚事件。"""
        event = DeploymentEvent(
            event_type="rollback",
            agent_id=agent_id,
            version=to_version,
            channel=channel,
            status=AgentDeploymentStatus.ROLLED_BACK,
            previous_version=from_version,
            actor=actor,
        )
        await self._repo.record(event)
        logger.info(
            "rollback: %s %s -> %s (channel=%s)",
            agent_id,
            from_version,
            to_version,
            channel,
        )
        return event

    async def get_rollback_version(
        self, agent_id: str, channel: str
    ) -> tuple[str, str | None] | None:
        """Return (version, artifact_id) for rollback, or None if no target exists."""
        version = await self._repo.get_rollback_version(agent_id, channel)
        if version:
            return (version, None)
        return None

    async def list_events(
        self,
        agent_id: str | None = None,
        channel: str | None = None,
        limit: int = 50,
    ) -> list[DeploymentEvent]:
        """按条件筛选并返回最近的部署事件列表。"""
        return await self._repo.list_events(agent_id=agent_id, channel=channel, limit=limit)
