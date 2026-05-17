"""Admin API dependency container."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_platform.api.tenant_quota import TenantQuotaManager
    from agent_platform.devflow.state_sync import DevFlowStateSync
    from agent_platform.evals.runner import EvalRunner
    from agent_platform.governance.slo import SLOGate
    from agent_platform.observability.metrics import MetricsCollector
    from agent_platform.persistence.repositories import (
        EvalRunRepository,
        RoutingDecisionRepository,
        ToolAuditRepository,
    )
    from agent_platform.persistence.sql import SqlApiKeyStore
    from agent_platform.registry.deployment import DeploymentAuditLog
    from agent_platform.registry.registry import AgentRegistry
    from agent_platform.runtime.manager import RuntimeManager
    from agent_platform.tools.registry import ToolRegistry
    from agent_platform.webhooks.dead_letter import WebhookRetryService


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
    eval_runner: EvalRunner | None = None
    slo_gate: SLOGate | None = None
    webhook_retry_service: WebhookRetryService | None = None
    state_sync: DevFlowStateSync | None = None
    routing_decision_repo: RoutingDecisionRepository | None = None
