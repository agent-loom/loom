"""各 Repository 协议的内存实现，用于测试和开发环境。"""

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
    """Agent 定义的内存存储实现。"""

    def __init__(self) -> None:
        """初始化内存字典存储。"""
        self._store: dict[str, AgentDefinition] = {}

    @staticmethod
    def _key(agent_id: str, version: str) -> str:
        return f"{agent_id}:{version}"

    async def save(
        self, definition: AgentDefinition
    ) -> None:
        """保存 Agent 定义到内存。"""
        key = self._key(definition.agent_id, definition.version)
        self._store[key] = definition

    async def get(
        self, agent_id: str, version: str
    ) -> AgentDefinition | None:
        """按 agent_id 和版本获取定义。"""
        return self._store.get(self._key(agent_id, version))

    async def get_latest(
        self, agent_id: str
    ) -> AgentDefinition | None:
        """获取指定 agent 的最新版本定义。"""
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
        """列出所有定义，可按状态过滤。"""
        items = list(self._store.values())
        if status is not None:
            items = [d for d in items if d.status == status]
        return items

    async def update_status(
        self, agent_id: str, version: str, status: str
    ) -> None:
        """更新指定定义的状态。"""
        key = self._key(agent_id, version)
        defn = self._store.get(key)
        if defn is not None:
            defn.status = status  # type: ignore[assignment]
            defn.updated_at = datetime.now(UTC)


class InMemoryAgentDeploymentRepository:
    """Agent 部署记录的内存存储实现。"""

    def __init__(self) -> None:
        """初始化内存字典存储。"""
        self._store: dict[str, AgentDeployment] = {}

    async def save(
        self, deployment: AgentDeployment
    ) -> None:
        """保存部署记录。"""
        self._store[deployment.deployment_id] = deployment

    async def get(
        self, deployment_id: str
    ) -> AgentDeployment | None:
        """按 deployment_id 获取部署记录。"""
        return self._store.get(deployment_id)

    async def resolve(
        self,
        *,
        agent_id: str,
        channel: str,
        tenant_id: str | None = None,
    ) -> AgentDeployment | None:
        """按 agent_id、渠道和租户解析部署记录。"""
        for dep in self._store.values():
            if dep.agent_id != agent_id:
                continue
            if dep.channel != channel:
                continue
            if dep.tenant_id != tenant_id:
                continue
            return dep
        return None

    async def list_all(
        self,
        *,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[AgentDeployment]:
        """列出所有部署，可按 agent_id 或租户过滤。"""
        items = list(self._store.values())
        if agent_id is not None:
            items = [
                d for d in items if d.agent_id == agent_id
            ]
        if tenant_id is not None:
            items = [
                d for d in items if d.tenant_id == tenant_id
            ]
        return items

    async def delete(self, deployment_id: str) -> None:
        """删除指定部署记录。"""
        self._store.pop(deployment_id, None)


class InMemoryDeploymentAuditRepository:
    """部署审计事件的内存存储实现。"""

    def __init__(self) -> None:
        """初始化事件列表和回滚版本映射。"""
        self._events: list[DeploymentEvent] = []
        self._rollback: dict[str, str] = {}

    async def record(
        self, event: DeploymentEvent
    ) -> None:
        """记录一条部署审计事件。"""
        self._events.append(event)
        if event.previous_version:
            key = f"{event.agent_id}:{event.channel}"
            self._rollback[key] = event.previous_version

    async def list_events(
        self,
        *,
        agent_id: str | None = None,
        channel: str | None = None,
        tenant_id: str | None = None,
        limit: int = 50,
    ) -> list[DeploymentEvent]:
        """列出审计事件，可按条件过滤。"""
        result = self._events
        if agent_id is not None:
            result = [
                e for e in result if e.agent_id == agent_id
            ]
        if channel is not None:
            result = [
                e for e in result if e.channel == channel
            ]
        if tenant_id is not None:
            result = [
                e for e in result
                if getattr(e, "metadata", {}).get("tenant_id") == tenant_id
            ]
        return result[-limit:]

    async def get_rollback_version(
        self, agent_id: str, channel: str
    ) -> str | None:
        """获取可回滚的上一版本号。"""
        key = f"{agent_id}:{channel}"
        return self._rollback.get(key)


class InMemoryAgentRunRepository:
    """Agent 运行记录的内存存储实现。"""

    def __init__(self) -> None:
        """初始化内存字典存储。"""
        self._store: dict[str, AgentRun] = {}

    async def record(self, run: AgentRun) -> None:
        """保存一条运行记录。"""
        self._store[run.run_id] = run

    async def get(
        self, run_id: str
    ) -> AgentRun | None:
        """按 run_id 获取运行记录。"""
        return self._store.get(run_id)

    async def list_runs(
        self,
        *,
        agent_id: str | None = None,
        session_id: str | None = None,
        tenant_id: str | None = None,
        limit: int = 100,
    ) -> list[AgentRun]:
        """列出运行记录，可按条件过滤。"""
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
        if tenant_id is not None:
            items = [
                r for r in items if r.tenant_id == tenant_id
            ]
        return items[-limit:]


class InMemoryAgentSessionRepository:
    """Agent 会话的内存存储实现。"""

    def __init__(self) -> None:
        """初始化内存字典存储。"""
        self._store: dict[str, AgentSession] = {}

    async def save(
        self, session: AgentSession
    ) -> None:
        """保存会话。"""
        self._store[session.session_id] = session

    async def load(
        self, session_id: str
    ) -> AgentSession | None:
        """按 session_id 加载会话。"""
        return self._store.get(session_id)

    async def delete(self, session_id: str) -> None:
        """删除指定会话。"""
        self._store.pop(session_id, None)

    async def list_sessions(
        self,
        *,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[AgentSession]:
        """列出会话，可按 agent_id 或租户过滤。"""
        items = list(self._store.values())
        if agent_id is not None:
            items = [
                s for s in items if s.agent_id == agent_id
            ]
        if tenant_id is not None:
            items = [
                s for s in items if s.tenant_id == tenant_id
            ]
        return items

    async def count_sessions(
        self,
        *,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> int:
        """统计会话数量。"""
        items = await self.list_sessions(agent_id=agent_id, tenant_id=tenant_id)
        return len(items)


class InMemoryWebhookDeliveryRepository:
    """Webhook 投递记录的内存存储实现。"""

    def __init__(self) -> None:
        """初始化内存字典存储。"""
        self._store: dict[str, dict[str, Any]] = {}

    async def exists(self, delivery_id: str) -> bool:
        """判断投递记录是否已存在。"""
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
        """记录一条 Webhook 投递。"""
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
    """评估运行记录的内存存储实现。"""

    def __init__(self) -> None:
        """初始化内存列表存储。"""
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
        """记录一次评估运行。"""
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
        """获取指定 agent 最近一次评估结果。"""
        matches = [
            r for r in self._runs
            if r["agent_id"] == agent_id
        ]
        return matches[-1] if matches else None

    async def list_runs(
        self,
        *,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """列出评估记录，可按条件过滤。"""
        items = self._runs
        if agent_id is not None:
            items = [
                r for r in items
                if r["agent_id"] == agent_id
            ]
        return items[-limit:]


class InMemoryCodingJobRepository:
    """In-memory coding job persistence for dev/test environments."""

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}

    async def save(self, job_data: dict[str, Any]) -> None:
        job_id = job_data.get("job_id", "")
        self._jobs[job_id] = job_data

    async def get(self, job_id: str) -> dict[str, Any] | None:
        return self._jobs.get(job_id)

    async def list_jobs(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        items = list(self._jobs.values())
        if status is not None:
            items = [j for j in items if j.get("state") == status]
        return items[-limit:]


class InMemoryToolAuditRepository:
    """工具调用审计的内存存储实现。"""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []

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
        self._events.append({
            "id": uuid.uuid4().hex,
            "tool_name": tool_name,
            "status": status,
            "latency_ms": latency_ms,
            "error": error,
            "payload": payload,
            "output": output,
            "run_id": run_id,
            "agent_id": agent_id,
            "created_at": datetime.now(UTC).isoformat(),
        })

    async def list_events(
        self,
        *,
        tool_name: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        items = self._events
        if tool_name is not None:
            items = [e for e in items if e["tool_name"] == tool_name]
        if agent_id is not None:
            items = [e for e in items if e.get("agent_id") == agent_id]
        if run_id is not None:
            items = [e for e in items if e.get("run_id") == run_id]
        if status is not None:
            items = [e for e in items if e["status"] == status]
        return items[-limit:]


class InMemoryRoutingDecisionRepository:
    """路由决策的内存存储实现。"""

    def __init__(self) -> None:
        self._decisions: dict[str, dict[str, Any]] = {}
        self._ordered: list[dict[str, Any]] = []

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
        entry = {
            "run_id": run_id,
            "agent_id": agent_id,
            "reason": reason,
            "deployment_id": deployment_id,
            "traffic_bucket": traffic_bucket,
            "latency_ms": latency_ms,
            "context": context or {},
            "created_at": datetime.now(UTC).isoformat(),
        }
        self._decisions[run_id] = entry
        self._ordered.append(entry)

    async def get(self, run_id: str) -> dict[str, Any] | None:
        return self._decisions.get(run_id)

    async def list_decisions(
        self,
        *,
        agent_id: str | None = None,
        reason: str | None = None,
        tenant_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        items = self._ordered
        if agent_id is not None:
            items = [d for d in items if d["agent_id"] == agent_id]
        if reason is not None:
            items = [d for d in items if d["reason"] == reason]
        return items[-limit:]
