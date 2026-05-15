from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from agent_platform.domain.models import AgentDeployment, AgentDeploymentStatus

logger = logging.getLogger(__name__)


class DeploymentEvent(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_type: str
    agent_id: str
    version: str
    channel: str
    traffic_percent: int = 100
    status: AgentDeploymentStatus
    previous_version: str | None = None
    actor: str = "system"
    metadata: dict[str, Any] = Field(default_factory=dict)


class DeploymentAuditLog:
    """Records all deployment events for audit trail and rollback support."""

    def __init__(self) -> None:
        self._events: list[DeploymentEvent] = []
        self._rollback_targets: dict[str, str] = {}

    def record_deploy(
        self,
        deployment: AgentDeployment,
        previous_version: str | None = None,
        actor: str = "system",
    ) -> DeploymentEvent:
        event = DeploymentEvent(
            event_type="deploy",
            agent_id=deployment.agent_id,
            version=deployment.version,
            channel=deployment.channel,
            traffic_percent=deployment.traffic_percent,
            status=deployment.status,
            previous_version=previous_version,
            actor=actor,
        )
        self._events.append(event)

        if previous_version:
            key = f"{deployment.agent_id}:{deployment.channel}"
            self._rollback_targets[key] = previous_version

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

    def record_rollback(
        self,
        agent_id: str,
        channel: str,
        from_version: str,
        to_version: str,
        actor: str = "system",
    ) -> DeploymentEvent:
        event = DeploymentEvent(
            event_type="rollback",
            agent_id=agent_id,
            version=to_version,
            channel=channel,
            status=AgentDeploymentStatus.ROLLED_BACK,
            previous_version=from_version,
            actor=actor,
        )
        self._events.append(event)
        logger.info(
            "rollback: %s %s -> %s (channel=%s)",
            agent_id,
            from_version,
            to_version,
            channel,
        )
        return event

    def get_rollback_version(self, agent_id: str, channel: str) -> str | None:
        key = f"{agent_id}:{channel}"
        return self._rollback_targets.get(key)

    def list_events(
        self,
        agent_id: str | None = None,
        channel: str | None = None,
        limit: int = 50,
    ) -> list[DeploymentEvent]:
        events = self._events
        if agent_id:
            events = [e for e in events if e.agent_id == agent_id]
        if channel:
            events = [e for e in events if e.channel == channel]
        return events[-limit:]

    def clear(self) -> None:
        self._events.clear()
        self._rollback_targets.clear()
