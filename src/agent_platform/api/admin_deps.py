"""Admin API dependency container."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_platform.observability.metrics import MetricsCollector
    from agent_platform.registry.deployment import DeploymentAuditLog
    from agent_platform.registry.registry import AgentRegistry
    from agent_platform.runtime.manager import RuntimeManager
    from agent_platform.tools.registry import ToolRegistry


@dataclass
class AdminDeps:
    """Holds references to the core components needed by admin endpoints."""

    registry: AgentRegistry
    runtime_manager: RuntimeManager
    audit_log: DeploymentAuditLog
    tool_registry: ToolRegistry
    metrics: MetricsCollector
