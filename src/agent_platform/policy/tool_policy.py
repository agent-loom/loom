"""Tool permission matrix – policy models and permission computation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Policy models
# ---------------------------------------------------------------------------


class TenantToolPolicy(BaseModel):
    """Per-tenant tool access policy."""

    tenant_id: str
    allow_tools: list[str] = Field(default_factory=list)  # empty = no restriction
    deny_tools: list[str] = Field(default_factory=list)  # overrides allow
    max_calls_per_minute: int = 60


class EnvironmentToolPolicy(BaseModel):
    """Per-environment tool access policy."""

    environment: str  # dev | staging | prod
    default_action: Literal["allow", "deny"] = "allow"
    allow_tools: list[str] = Field(default_factory=list)
    deny_tools: list[str] = Field(default_factory=list)
    high_risk_tools: list[str] = Field(default_factory=list)  # glob patterns


# ---------------------------------------------------------------------------
# Permission decision types
# ---------------------------------------------------------------------------


class ToolPermissionDecision(BaseModel):
    """Base class for a tool permission decision."""

    allowed: bool
    reason: str = ""
    requires_approval: bool = False


class Allowed(ToolPermissionDecision):
    """允许使用工具。"""

    allowed: bool = True


class Denied(ToolPermissionDecision):
    """拒绝使用工具。"""

    allowed: bool = False


class RequiresApproval(ToolPermissionDecision):
    """需要人工审批后方可使用工具。"""

    allowed: bool = False
    requires_approval: bool = True


# ---------------------------------------------------------------------------
# Permission computation
# ---------------------------------------------------------------------------


def compute_tool_permission(
    tool_name: str,
    *,
    manifest_allow: list[str],
    manifest_deny: list[str],
    risk_level: str = "low",
    tenant_policy: TenantToolPolicy | None = None,
    environment: str = "dev",
    env_policy: EnvironmentToolPolicy | None = None,
) -> ToolPermissionDecision:
    """Compute tool permission from the intersection of three layers.

    Final permission = manifest allow-list ∩ tenant policy ∩ environment policy
    """
    # Step 1: Agent manifest check
    if tool_name in manifest_deny:
        return Denied(reason="agent manifest deny-list")
    if manifest_allow and tool_name not in manifest_allow:
        return Denied(reason="agent manifest allow-list")

    # Step 2: Tenant policy check
    if tenant_policy:
        if tool_name in tenant_policy.deny_tools:
            return Denied(reason="tenant deny-list")
        if tenant_policy.allow_tools and tool_name not in tenant_policy.allow_tools:
            return Denied(reason="tenant allow-list")

    # Step 3: Environment policy check
    if env_policy:
        if tool_name in env_policy.deny_tools:
            return Denied(reason="environment deny-list")
        if env_policy.default_action == "deny" and tool_name not in env_policy.allow_tools:
            return Denied(reason="environment default deny")

    # Step 4: Risk-level gating
    if risk_level in ("high", "critical") and environment == "prod":
        return RequiresApproval(reason=f"{risk_level}-risk tool in prod")
    if risk_level == "critical" and environment == "staging":
        return RequiresApproval(reason="critical tool in staging")

    return Allowed(reason="all checks passed")


# ---------------------------------------------------------------------------
# Default environment policies
# ---------------------------------------------------------------------------

DEFAULT_ENV_POLICIES: dict[str, EnvironmentToolPolicy] = {
    "dev": EnvironmentToolPolicy(environment="dev", default_action="allow"),
    "staging": EnvironmentToolPolicy(environment="staging", default_action="allow"),
    "prod": EnvironmentToolPolicy(environment="prod", default_action="deny"),
}
