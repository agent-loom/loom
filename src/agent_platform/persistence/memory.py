from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from agent_platform.domain.models import (
    AgentDefinition,
    AgentDeployment,
    AgentRun,
    AgentSession,
)
from agent_platform.registry.deployment import DeploymentEvent


class InMemoryAgentDefinitionRepository:
    def __init__(self) -> None:
        self._store: dict[str, AgentDefinition] = {}

    @staticmethod
    def _key(agent_id: str, version: str) -> str:
        return f"{agent_id}:{version}"

    async def save(
        self, definition: AgentDefinition
    ) -> None:
        key = self._key(definition.agent_id, definition.version)
        self._store[key] = definition

    async def get(
        self, agent_id: str, version: str
    ) -> AgentDefinition | None:
        return self._store.get(self._key(agent_id, version))

    async def get_latest(
        self, agent_id: str
    ) -> AgentDefinition | None:
        matches = [
            d
            for d in self._store.values()
            if d.agent_id == agent_id
        ]
        if not matches:
            return None
        return max(matches, key=lambda d: d.created_at)

    async def list_all(
        self, *, status: str | None = None
    ) -> list[AgentDefinition]:
        items = list(self._store.values())
        if status is not None:
            items = [d for d in items if d.status == status]
        return items

    async def update_status(
        self, agent_id: str, version: str, status: str
    ) -> None:
        key = self._key(agent_id, version)
        defn = self._store.get(key)
        if defn is not None:
            defn.status = status  # type: ignore[assignment]
            defn.updated_at = datetime.now(UTC)


class InMemoryAgentDeploymentRepository:
    def __init__(self) -> None:
        self._store: dict[str, AgentDeployment] = {}

    async def save(
        self, deployment: AgentDeployment
    ) -> None:
        self._store[deployment.deployment_id] = deployment

    async def get(
        self, deployment_id: str
    ) -> AgentDeployment | None:
        return self._store.get(deployment_id)

    async def resolve(
        self,
        *,
        agent_id: str,
        channel: str,
        tenant_id: str | None = None,
    ) -> AgentDeployment | None:
        for dep in self._store.values():
            if dep.agent_id != agent_id:
                continue
            if dep.channel != channel:
                continue
            if tenant_id and dep.tenant_id != tenant_id:
                continue
            return dep
        return None

    async def list_all(
        self, *, agent_id: str | None = None
    ) -> list[AgentDeployment]:
        items = list(self._store.values())
        if agent_id is not None:
            items = [
                d for d in items if d.agent_id == agent_id
            ]
        return items

    async def delete(self, deployment_id: str) -> None:
        self._store.pop(deployment_id, None)


class InMemoryDeploymentAuditRepository:
    def __init__(self) -> None:
        self._events: list[DeploymentEvent] = []
        self._rollback: dict[str, str] = {}

    async def record(
        self, event: DeploymentEvent
    ) -> None:
        self._events.append(event)
        if event.previous_version:
            key = f"{event.agent_id}:{event.channel}"
            self._rollback[key] = event.previous_version

    async def list_events(
        self,
        *,
        agent_id: str | None = None,
        channel: str | None = None,
        limit: int = 50,
    ) -> list[DeploymentEvent]:
        result = self._events
        if agent_id is not None:
            result = [
                e for e in result if e.agent_id == agent_id
            ]
        if channel is not None:
            result = [
                e for e in result if e.channel == channel
            ]
        return result[-limit:]

    async def get_rollback_version(
        self, agent_id: str, channel: str
    ) -> str | None:
        key = f"{agent_id}:{channel}"
        return self._rollback.get(key)


class InMemoryAgentRunRepository:
    def __init__(self) -> None:
        self._store: dict[str, AgentRun] = {}

    async def record(self, run: AgentRun) -> None:
        self._store[run.run_id] = run

    async def get(
        self, run_id: str
    ) -> AgentRun | None:
        return self._store.get(run_id)

    async def list_runs(
        self,
        *,
        agent_id: str | None = None,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[AgentRun]:
        items = list(self._store.values())
        if agent_id is not None:
            items = [
                r for r in items if r.agent_id == agent_id
            ]
        if session_id is not None:
            items = [
                r for r in items
                if r.session_id == session_id
            ]
        return items[-limit:]


class InMemoryAgentSessionRepository:
    def __init__(self) -> None:
        self._store: dict[str, AgentSession] = {}

    async def save(
        self, session: AgentSession
    ) -> None:
        self._store[session.session_id] = session

    async def load(
        self, session_id: str
    ) -> AgentSession | None:
        return self._store.get(session_id)

    async def delete(self, session_id: str) -> None:
        self._store.pop(session_id, None)

    async def list_sessions(
        self, *, agent_id: str | None = None
    ) -> list[AgentSession]:
        items = list(self._store.values())
        if agent_id is not None:
            items = [
                s for s in items if s.agent_id == agent_id
            ]
        return items


class InMemoryWebhookDeliveryRepository:
    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    async def exists(self, delivery_id: str) -> bool:
        return delivery_id in self._store

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
        self._store[delivery_id] = {
            "delivery_id": delivery_id,
            "source": source,
            "event_type": event_type,
            "status": status,
            "payload": payload,
            "error_message": error_message,
            "created_at": datetime.now(UTC).isoformat(),
        }


class InMemoryEvalRunRepository:
    def __init__(self) -> None:
        self._runs: list[dict[str, Any]] = []

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
        self._runs.append({
            "id": uuid.uuid4().hex,
            "agent_id": agent_id,
            "agent_version": agent_version,
            "total": total,
            "passed": passed,
            "pass_rate": pass_rate,
            "required_pass_rate": required_pass_rate,
            "gate_passed": gate_passed,
            "results": results,
            "trigger": trigger,
            "created_at": datetime.now(UTC).isoformat(),
        })

    async def get_latest(
        self, agent_id: str
    ) -> dict[str, Any] | None:
        matches = [
            r for r in self._runs
            if r["agent_id"] == agent_id
        ]
        return matches[-1] if matches else None

    async def list_runs(
        self,
        *,
        agent_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        items = self._runs
        if agent_id is not None:
            items = [
                r for r in items
                if r["agent_id"] == agent_id
            ]
        return items[-limit:]
