"""Admin API dependency container."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_platform.api.tenant_quota import TenantQuotaManager
    from agent_platform.observability.metrics import MetricsCollector
    from agent_platform.persistence.repositories import EvalRunRepository, ToolAuditRepository
    from agent_platform.persistence.sql import SqlApiKeyStore
    from agent_platform.registry.deployment import DeploymentAuditLog
    from agent_platform.registry.registry import AgentRegistry
    from agent_platform.runtime.manager import RuntimeManager
    from agent_platform.tools.registry import ToolRegistry


@dataclass
class AdminDeps:
    """Admin API 端点所需的核心组件容器。"""

    registry: AgentRegistry
    runtime_manager: RuntimeManager
    audit_log: DeploymentAuditLog
    tool_registry: ToolRegistry
    metrics: MetricsCollector
    key_store: SqlApiKeyStore | None = None
    eval_repo: EvalRunRepository | None = None
    tool_audit_repo: ToolAuditRepository | None = None
    quota_manager: TenantQuotaManager | None = None
