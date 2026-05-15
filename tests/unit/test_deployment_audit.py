"""Tests for DeploymentAuditLog — src/agent_platform/registry/deployment.py"""

from __future__ import annotations

import pytest

from agent_platform.domain.models import AgentDeployment, AgentDeploymentStatus
from agent_platform.registry.deployment import DeploymentAuditLog, DeploymentEvent

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def audit_log() -> DeploymentAuditLog:
    return DeploymentAuditLog()


def _make_deployment(
    agent_id: str = "myj",
    version: str = "1.0.0",
    channel: str = "prod",
    status: AgentDeploymentStatus = AgentDeploymentStatus.PROD,
    traffic_percent: int = 100,
) -> AgentDeployment:
    return AgentDeployment(
        deployment_id=f"dep_{agent_id}_{channel}",
        agent_id=agent_id,
        version=version,
        channel=channel,
        status=status,
        traffic_percent=traffic_percent,
    )


# ---------------------------------------------------------------------------
# Tests — record_deploy
# ---------------------------------------------------------------------------

def test_record_deploy_creates_event(audit_log: DeploymentAuditLog):
    deployment = _make_deployment()
    event = audit_log.record_deploy(deployment, previous_version="0.9.0", actor="ci")

    assert isinstance(event, DeploymentEvent)
    assert event.event_type == "deploy"
    assert event.agent_id == "myj"
    assert event.version == "1.0.0"
    assert event.channel == "prod"
    assert event.previous_version == "0.9.0"
    assert event.actor == "ci"
    assert event.status == AgentDeploymentStatus.PROD
    assert event.traffic_percent == 100


def test_record_deploy_without_previous_version(audit_log: DeploymentAuditLog):
    deployment = _make_deployment()
    event = audit_log.record_deploy(deployment)

    assert event.previous_version is None
    assert event.actor == "system"


def test_record_deploy_adds_to_events(audit_log: DeploymentAuditLog):
    deployment = _make_deployment()
    audit_log.record_deploy(deployment)

    events = audit_log.list_events()
    assert len(events) == 1


def test_record_deploy_sets_rollback_target(audit_log: DeploymentAuditLog):
    deployment = _make_deployment(version="2.0.0")
    audit_log.record_deploy(deployment, previous_version="1.0.0")

    target = audit_log.get_rollback_version("myj", "prod")
    assert target[0] == "1.0.0"


def test_record_deploy_no_rollback_target_without_previous(audit_log: DeploymentAuditLog):
    deployment = _make_deployment()
    audit_log.record_deploy(deployment)

    target = audit_log.get_rollback_version("myj", "prod")
    assert target is None


# ---------------------------------------------------------------------------
# Tests — record_rollback
# ---------------------------------------------------------------------------

def test_record_rollback_creates_event(audit_log: DeploymentAuditLog):
    event = audit_log.record_rollback(
        agent_id="myj",
        channel="prod",
        from_version="2.0.0",
        to_version="1.0.0",
        actor="ops",
    )

    assert isinstance(event, DeploymentEvent)
    assert event.event_type == "rollback"
    assert event.agent_id == "myj"
    assert event.version == "1.0.0"  # rolls back TO this version
    assert event.previous_version == "2.0.0"  # rolled back FROM this version
    assert event.channel == "prod"
    assert event.status == AgentDeploymentStatus.ROLLED_BACK
    assert event.actor == "ops"


def test_record_rollback_default_actor(audit_log: DeploymentAuditLog):
    event = audit_log.record_rollback("myj", "prod", "2.0.0", "1.0.0")
    assert event.actor == "system"


# ---------------------------------------------------------------------------
# Tests — list_events with filters
# ---------------------------------------------------------------------------

def test_list_events_no_filter(audit_log: DeploymentAuditLog):
    for i in range(5):
        deployment = _make_deployment(version=f"1.{i}.0")
        audit_log.record_deploy(deployment)

    events = audit_log.list_events()
    assert len(events) == 5


def test_list_events_filter_by_agent_id(audit_log: DeploymentAuditLog):
    audit_log.record_deploy(_make_deployment(agent_id="myj"))
    audit_log.record_deploy(_make_deployment(agent_id="echo"))
    audit_log.record_deploy(_make_deployment(agent_id="myj", version="2.0.0"))

    events = audit_log.list_events(agent_id="myj")
    assert len(events) == 2
    assert all(e.agent_id == "myj" for e in events)


def test_list_events_filter_by_channel(audit_log: DeploymentAuditLog):
    audit_log.record_deploy(_make_deployment(channel="prod"))
    audit_log.record_deploy(_make_deployment(channel="staging"))
    audit_log.record_deploy(_make_deployment(channel="prod", version="2.0.0"))

    events = audit_log.list_events(channel="prod")
    assert len(events) == 2
    assert all(e.channel == "prod" for e in events)


def test_list_events_filter_by_agent_and_channel(audit_log: DeploymentAuditLog):
    audit_log.record_deploy(_make_deployment(agent_id="myj", channel="prod"))
    audit_log.record_deploy(_make_deployment(agent_id="echo", channel="prod"))
    audit_log.record_deploy(_make_deployment(agent_id="myj", channel="staging"))

    events = audit_log.list_events(agent_id="myj", channel="prod")
    assert len(events) == 1
    assert events[0].agent_id == "myj"
    assert events[0].channel == "prod"


def test_list_events_respects_limit(audit_log: DeploymentAuditLog):
    for i in range(10):
        audit_log.record_deploy(_make_deployment(version=f"1.{i}.0"))

    events = audit_log.list_events(limit=3)
    assert len(events) == 3
    # Should return the last 3 events
    assert events[0].version == "1.7.0"
    assert events[2].version == "1.9.0"


# ---------------------------------------------------------------------------
# Tests — get_rollback_version
# ---------------------------------------------------------------------------

def test_get_rollback_version_returns_previous(audit_log: DeploymentAuditLog):
    deployment = _make_deployment(version="2.0.0", channel="prod")
    audit_log.record_deploy(deployment, previous_version="1.0.0")

    assert audit_log.get_rollback_version("myj", "prod")[0] == "1.0.0"


def test_get_rollback_version_returns_none_when_no_history(audit_log: DeploymentAuditLog):
    assert audit_log.get_rollback_version("myj", "prod") is None


def test_get_rollback_version_returns_none_for_wrong_agent(audit_log: DeploymentAuditLog):
    deployment = _make_deployment(agent_id="myj", version="2.0.0")
    audit_log.record_deploy(deployment, previous_version="1.0.0")

    assert audit_log.get_rollback_version("echo", "prod") is None


def test_get_rollback_version_returns_none_for_wrong_channel(audit_log: DeploymentAuditLog):
    deployment = _make_deployment(channel="prod", version="2.0.0")
    audit_log.record_deploy(deployment, previous_version="1.0.0")

    assert audit_log.get_rollback_version("myj", "staging") is None


def test_rollback_version_updates_on_successive_deploys(audit_log: DeploymentAuditLog):
    dep1 = _make_deployment(version="2.0.0")
    audit_log.record_deploy(dep1, previous_version="1.0.0")
    assert audit_log.get_rollback_version("myj", "prod")[0] == "1.0.0"

    dep2 = _make_deployment(version="3.0.0")
    audit_log.record_deploy(dep2, previous_version="2.0.0")
    assert audit_log.get_rollback_version("myj", "prod")[0] == "2.0.0"


# ---------------------------------------------------------------------------
# Tests — clear
# ---------------------------------------------------------------------------

def test_clear_removes_all_events(audit_log: DeploymentAuditLog):
    deployment = _make_deployment()
    audit_log.record_deploy(deployment, previous_version="0.9.0")
    audit_log.record_rollback("myj", "prod", "1.0.0", "0.9.0")

    audit_log.clear()

    assert audit_log.list_events() == []
    assert audit_log.get_rollback_version("myj", "prod") is None
