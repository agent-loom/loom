from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agent_platform.domain.models import (
    AgentDefinition,
    AgentDeployment,
    AgentRun,
    AgentSession,
)
from agent_platform.registry.deployment import DeploymentEvent


@runtime_checkable
class AgentDefinitionRepository(Protocol):
    async def save(
        self, definition: AgentDefinition
    ) -> None: ...

    async def get(
        self, agent_id: str, version: str
    ) -> AgentDefinition | None: ...

    async def get_latest(
        self, agent_id: str
    ) -> AgentDefinition | None: ...

    async def list_all(
        self, *, status: str | None = None
    ) -> list[AgentDefinition]: ...

    async def update_status(
        self, agent_id: str, version: str, status: str
    ) -> None: ...


@runtime_checkable
class AgentDeploymentRepository(Protocol):
    async def save(
        self, deployment: AgentDeployment
    ) -> None: ...

    async def get(
        self, deployment_id: str
    ) -> AgentDeployment | None: ...

    async def resolve(
        self,
        *,
        agent_id: str,
        channel: str,
        tenant_id: str | None = None,
    ) -> AgentDeployment | None: ...

    async def list_all(
        self, *, agent_id: str | None = None
    ) -> list[AgentDeployment]: ...

    async def delete(self, deployment_id: str) -> None: ...


@runtime_checkable
class DeploymentAuditRepository(Protocol):
    async def record(
        self, event: DeploymentEvent
    ) -> None: ...

    async def list_events(
        self,
        *,
        agent_id: str | None = None,
        channel: str | None = None,
        limit: int = 50,
    ) -> list[DeploymentEvent]: ...

    async def get_rollback_version(
        self, agent_id: str, channel: str
    ) -> str | None: ...


@runtime_checkable
class AgentRunRepository(Protocol):
    async def record(self, run: AgentRun) -> None: ...

    async def get(
        self, run_id: str
    ) -> AgentRun | None: ...

    async def list_runs(
        self,
        *,
        agent_id: str | None = None,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[AgentRun]: ...


@runtime_checkable
class AgentSessionRepository(Protocol):
    async def save(
        self, session: AgentSession
    ) -> None: ...

    async def load(
        self, session_id: str
    ) -> AgentSession | None: ...

    async def delete(
        self, session_id: str
    ) -> None: ...

    async def list_sessions(
        self, *, agent_id: str | None = None
    ) -> list[AgentSession]: ...


@runtime_checkable
class WebhookDeliveryRepository(Protocol):
    async def exists(
        self, delivery_id: str
    ) -> bool: ...

    async def record(
        self,
        *,
        delivery_id: str,
        source: str,
        event_type: str | None = None,
        status: str = "accepted",
        payload: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None: ...


@runtime_checkable
class EvalRunRepository(Protocol):
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
    ) -> None: ...

    async def get_latest(
        self, agent_id: str
    ) -> dict[str, Any] | None: ...

    async def list_runs(
        self, *, agent_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]: ...
