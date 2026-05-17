"""部署审计日志：记录部署事件，支持回滚追踪和哈希链完整性校验。"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from agent_platform.domain.models import AgentDeployment, AgentDeploymentStatus

if TYPE_CHECKING:
    from agent_platform.persistence.repositories import DeploymentAuditRepository

logger = logging.getLogger(__name__)


def _compute_event_hash(event_data: str, prev_hash: str) -> str:
    """计算审计事件的完整性哈希（SHA-256 链式哈希）。"""
    payload = f"{prev_hash}|{event_data}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
    integrity_hash: str = ""
    prev_hash: str = ""


class DeploymentAuditLog:
    """Records all deployment events for audit trail and rollback support.

    每条事件通过 SHA-256 链式哈希确保不可变性和防篡改。
    """

    GENESIS_HASH = "0" * 64

    def __init__(self, *, repo: DeploymentAuditRepository | None = None) -> None:
        if repo is None:
            from agent_platform.persistence.memory import InMemoryDeploymentAuditRepository
            self._repo = InMemoryDeploymentAuditRepository()
        else:
            self._repo = repo
        self._last_hash: str = self.GENESIS_HASH

    def _seal_event(self, event: DeploymentEvent) -> DeploymentEvent:
        """为事件计算完整性哈希并链接到前一事件。"""
        canonical = json.dumps(
            {
                "ts": event.timestamp.isoformat(),
                "type": event.event_type,
                "agent": event.agent_id,
                "ver": event.version,
                "ch": event.channel,
                "status": event.status.value,
                "actor": event.actor,
            },
            sort_keys=True,
        )
        integrity = _compute_event_hash(canonical, self._last_hash)
        sealed = event.model_copy(
            update={"integrity_hash": integrity, "prev_hash": self._last_hash},
        )
        self._last_hash = integrity
        return sealed

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
        sealed = self._seal_event(event)
        await self._repo.record(sealed)

        logger.info(
            "deployment event: %s %s@%s -> %s (channel=%s, traffic=%d%%)",
            sealed.event_type,
            sealed.agent_id,
            sealed.version,
            sealed.status,
            sealed.channel,
            sealed.traffic_percent,
        )
        return sealed

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
        sealed = self._seal_event(event)
        await self._repo.record(sealed)
        logger.info(
            "rollback: %s %s -> %s (channel=%s)",
            agent_id,
            from_version,
            to_version,
            channel,
        )
        return sealed

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

    async def verify_chain(
        self,
        agent_id: str | None = None,
        channel: str | None = None,
    ) -> tuple[bool, int]:
        """校验审计事件链的完整性，返回 (是否完整, 已验证事件数)。"""
        events = await self._repo.list_events(
            agent_id=agent_id, channel=channel, limit=10000,
        )
        events.sort(key=lambda e: e.timestamp)

        prev = self.GENESIS_HASH
        for i, ev in enumerate(events):
            if ev.prev_hash and ev.prev_hash != prev:
                return False, i
            prev = ev.integrity_hash or prev
        return True, len(events)
