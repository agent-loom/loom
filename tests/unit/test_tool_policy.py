from __future__ import annotations

from agent_platform.policy.tool_policy import (
    Allowed,
    Denied,
    EnvironmentToolPolicy,
    RequiresApproval,
    TenantToolPolicy,
    compute_tool_permission,
)


def test_manifest_allow_list_blocks():
    result = compute_tool_permission(
        "unknown_tool",
        manifest_allow=["tool_a", "tool_b"],
        manifest_deny=[],
    )
    assert isinstance(result, Denied)
    assert "allow-list" in result.reason


def test_manifest_deny_list_blocks():
    result = compute_tool_permission(
        "tool_a",
        manifest_allow=["tool_a"],
        manifest_deny=["tool_a"],
    )
    assert isinstance(result, Denied)
    assert "deny-list" in result.reason


def test_tenant_deny_overrides():
    policy = TenantToolPolicy(tenant_id="t1", deny_tools=["tool_a"])
    result = compute_tool_permission(
        "tool_a",
        manifest_allow=["tool_a"],
        manifest_deny=[],
        tenant_policy=policy,
    )
    assert isinstance(result, Denied)
    assert "tenant" in result.reason


def test_tenant_allow_restricts():
    policy = TenantToolPolicy(tenant_id="t1", allow_tools=["tool_b"])
    result = compute_tool_permission(
        "tool_a",
        manifest_allow=["tool_a", "tool_b"],
        manifest_deny=[],
        tenant_policy=policy,
    )
    assert isinstance(result, Denied)
    assert "tenant" in result.reason


def test_env_default_deny_blocks():
    env_policy = EnvironmentToolPolicy(environment="prod", default_action="deny")
    result = compute_tool_permission(
        "tool_a",
        manifest_allow=["tool_a"],
        manifest_deny=[],
        environment="prod",
        env_policy=env_policy,
    )
    assert isinstance(result, Denied)
    assert "environment" in result.reason


def test_env_deny_list_blocks():
    env_policy = EnvironmentToolPolicy(
        environment="staging",
        deny_tools=["tool_a"],
    )
    result = compute_tool_permission(
        "tool_a",
        manifest_allow=["tool_a"],
        manifest_deny=[],
        environment="staging",
        env_policy=env_policy,
    )
    assert isinstance(result, Denied)
    assert "environment" in result.reason


def test_high_risk_requires_approval_in_prod():
    result = compute_tool_permission(
        "tool_a",
        manifest_allow=["tool_a"],
        manifest_deny=[],
        risk_level="high",
        environment="prod",
    )
    assert isinstance(result, RequiresApproval)
    assert "high-risk" in result.reason


def test_critical_requires_approval_in_staging():
    result = compute_tool_permission(
        "tool_a",
        manifest_allow=["tool_a"],
        manifest_deny=[],
        risk_level="critical",
        environment="staging",
    )
    assert isinstance(result, RequiresApproval)
    assert "critical" in result.reason


def test_high_risk_allowed_in_dev():
    result = compute_tool_permission(
        "tool_a",
        manifest_allow=["tool_a"],
        manifest_deny=[],
        risk_level="high",
        environment="dev",
    )
    assert isinstance(result, Allowed)


def test_all_layers_pass():
    policy = TenantToolPolicy(tenant_id="t1", allow_tools=["tool_a"])
    env_policy = EnvironmentToolPolicy(
        environment="staging",
        default_action="allow",
    )
    result = compute_tool_permission(
        "tool_a",
        manifest_allow=["tool_a"],
        manifest_deny=[],
        tenant_policy=policy,
        environment="staging",
        env_policy=env_policy,
    )
    assert isinstance(result, Allowed)


def test_empty_manifest_allow_means_no_restriction():
    result = compute_tool_permission(
        "any_tool",
        manifest_allow=[],
        manifest_deny=[],
    )
    assert isinstance(result, Allowed)
