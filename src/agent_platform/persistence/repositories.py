"""持久化层 Repository 协议定义，声明各领域实体的 CRUD 接口。"""

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
    """Agent 定义的存储协议。"""

    async def save(
        self, definition: AgentDefinition
    ) -> None:
        """保存 Agent 定义。"""
        ...

    async def get(
        self, agent_id: str, version: str
    ) -> AgentDefinition | None:
        """按 agent_id 和版本获取定义。"""
        ...

    async def get_latest(
        self, agent_id: str
    ) -> AgentDefinition | None:
        """获取指定 agent 的最新版本定义。"""
        ...

    async def list_all(
        self, *, status: str | None = None
    ) -> list[AgentDefinition]:
        """列出所有定义，可按状态过滤。"""
        ...

    async def update_status(
        self, agent_id: str, version: str, status: str
    ) -> None:
        """更新指定定义的状态。"""
        ...


@runtime_checkable
class AgentDeploymentRepository(Protocol):
    """Agent 部署记录的存储协议。"""

    async def save(
        self, deployment: AgentDeployment
    ) -> None:
        """保存部署记录。"""
        ...

    async def get(
        self, deployment_id: str
    ) -> AgentDeployment | None:
        """按 deployment_id 获取部署记录。"""
        ...

    async def resolve(
        self,
        *,
        agent_id: str,
        channel: str,
        tenant_id: str | None = None,
    ) -> AgentDeployment | None:
        """按 agent_id、渠道和租户解析部署记录。"""
        ...

    async def list_all(
        self,
        *,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[AgentDeployment]:
        """列出所有部署，可按 agent_id 或租户过滤。"""
        ...

    async def delete(self, deployment_id: str) -> None:
        """删除指定部署记录。"""
        ...


@runtime_checkable
class DeploymentAuditRepository(Protocol):
    """部署审计事件的存储协议。"""

    async def record(
        self, event: DeploymentEvent
    ) -> None:
        """记录一条部署审计事件。"""
        ...

    async def list_events(
        self,
        *,
        agent_id: str | None = None,
        channel: str | None = None,
        tenant_id: str | None = None,
        limit: int = 50,
    ) -> list[DeploymentEvent]:
        """列出审计事件，可按条件过滤。"""
        ...

    async def get_rollback_version(
        self, agent_id: str, channel: str
    ) -> str | None:
        """获取可回滚的上一版本号。"""
        ...


@runtime_checkable
class AgentRunRepository(Protocol):
    """Agent 运行记录的存储协议。"""

    async def record(self, run: AgentRun) -> None:
        """保存一条运行记录。"""
        ...

    async def get(
        self, run_id: str
    ) -> AgentRun | None:
        """按 run_id 获取运行记录。"""
        ...

    async def list_runs(
        self,
        *,
        agent_id: str | None = None,
        session_id: str | None = None,
        tenant_id: str | None = None,
        limit: int = 100,
    ) -> list[AgentRun]:
        """列出运行记录，可按条件过滤。"""
        ...


@runtime_checkable
class AgentSessionRepository(Protocol):
    """Agent 会话的存储协议。"""

    async def save(
        self, session: AgentSession
    ) -> None:
        """保存会话。"""
        ...

    async def load(
        self, session_id: str
    ) -> AgentSession | None:
        """按 session_id 加载会话。"""
        ...

    async def delete(
        self, session_id: str
    ) -> None:
        """删除指定会话。"""
        ...

    async def list_sessions(
        self,
        *,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[AgentSession]:
        """列出会话，可按 agent_id 或租户过滤。"""
        ...


@runtime_checkable
class WebhookDeliveryRepository(Protocol):
    """Webhook 投递记录的存储协议。"""

    async def exists(
        self, delivery_id: str
    ) -> bool:
        """判断投递记录是否已存在（幂等检查）。"""
        ...

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
        """记录一条 Webhook 投递。"""
        ...


@runtime_checkable
class EvalRunRepository(Protocol):
    """评估运行记录的存储协议。"""

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
        """记录一次评估运行。"""
        ...

    async def get_latest(
        self, agent_id: str
    ) -> dict[str, Any] | None:
        """获取指定 agent 最近一次评估结果。"""
        ...

    async def list_runs(
        self,
        *,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """列出评估记录，可按条件过滤。"""
        ...


@runtime_checkable
class ToolAuditRepository(Protocol):
    """工具调用审计记录的存储协议。"""

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
        """记录一次工具调用审计事件。"""
        ...

    async def list_events(
        self,
        *,
        tool_name: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """列出工具审计记录，可按条件过滤。"""
        ...


@runtime_checkable
class CodingJobRepository(Protocol):
    """DevFlow coding job persistence."""

    async def save(self, job_data: dict[str, Any]) -> None:
        """Persist a coding job snapshot."""
        ...

    async def get(self, job_id: str) -> dict[str, Any] | None:
        """Retrieve a coding job by ID."""
        ...

    async def list_jobs(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List coding jobs with optional status filter."""
        ...


@runtime_checkable
class RoutingDecisionRepository(Protocol):
    """路由决策记录的存储协议。"""

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
        """记录一条路由决策。"""
        ...

    async def get(self, run_id: str) -> dict[str, Any] | None:
        """按 run_id 获取路由决策。"""
        ...

    async def list_decisions(
        self,
        *,
        agent_id: str | None = None,
        reason: str | None = None,
        tenant_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """列出路由决策记录，可按条件过滤。"""
        ...
