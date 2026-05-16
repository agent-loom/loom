"""Tests for SemanticRouter auto-loading from manifest routing rules."""

from pathlib import Path

import pytest

from agent_platform.domain.models import (
    AgentManifest,
    AgentSpec,
    ManifestMetadata,
    ManifestOutput,
    ManifestRouting,
    ManifestRoutingRule,
    ManifestVersion,
)
from agent_platform.registry.registry import AgentRegistry
from agent_platform.router_semantic import SemanticRouter


def _make_spec(
    agent_id: str,
    routing_rules: list[ManifestRoutingRule] | None = None,
) -> AgentSpec:
    manifest = AgentManifest(
        api_version="agent.platform/v1",
        kind="AgentPackage",
        metadata=ManifestMetadata(id=agent_id, name=agent_id),
        version=ManifestVersion(package_version="1.0.0"),
        routing=ManifestRouting(routing_rules=routing_rules or []),
        output=ManifestOutput(),
    )
    return AgentSpec(manifest=manifest, package_path=Path(f"/tmp/{agent_id}"))


@pytest.mark.asyncio
async def test_register_loads_routing_rules_into_semantic_router():
    sr = SemanticRouter(confidence_threshold=0.5)
    registry = AgentRegistry(Path("/tmp/agents"), semantic_router=sr)

    spec = _make_spec("faq-agent", [
        ManifestRoutingRule(
            keywords=["FAQ", "help", "question"],
            patterns=[r"(?i)\bfaq\b"],
            description="FAQ queries",
        ),
    ])
    await registry.register(spec)

    match = sr.match("I have a FAQ question")
    assert match is not None
    assert match.agent_id == "faq-agent"


@pytest.mark.asyncio
async def test_register_without_routing_rules_no_side_effects():
    sr = SemanticRouter(confidence_threshold=0.5)
    registry = AgentRegistry(Path("/tmp/agents"), semantic_router=sr)

    spec = _make_spec("plain-agent")
    await registry.register(spec)

    assert sr.match("anything") is None


@pytest.mark.asyncio
async def test_multiple_agents_routing_rules():
    sr = SemanticRouter(confidence_threshold=0.5)
    registry = AgentRegistry(Path("/tmp/agents"), semantic_router=sr)

    await registry.register(_make_spec("order-agent", [
        ManifestRoutingRule(keywords=["order", "purchase", "buy"], description="order handling"),
    ]))
    await registry.register(_make_spec("support-agent", [
        ManifestRoutingRule(keywords=["support", "help", "issue"], description="support handling"),
    ]))

    order_match = sr.match("I want to buy and order something")
    assert order_match is not None
    assert order_match.agent_id == "order-agent"

    support_match = sr.match("I need support help with an issue")
    assert support_match is not None
    assert support_match.agent_id == "support-agent"


@pytest.mark.asyncio
async def test_pattern_based_routing_rule():
    sr = SemanticRouter(confidence_threshold=0.5)
    registry = AgentRegistry(Path("/tmp/agents"), semantic_router=sr)

    await registry.register(_make_spec("billing-agent", [
        ManifestRoutingRule(
            patterns=[r"(?i)\b(invoice|bill|charge)\b"],
            description="billing queries",
        ),
    ]))

    match = sr.match("Where is my invoice?")
    assert match is not None
    assert match.agent_id == "billing-agent"


@pytest.mark.asyncio
async def test_register_without_semantic_router_does_not_raise():
    registry = AgentRegistry(Path("/tmp/agents"))
    spec = _make_spec("lonely-agent", [
        ManifestRoutingRule(keywords=["test"], description="test"),
    ])
    await registry.register(spec)


@pytest.mark.asyncio
async def test_discover_loads_rules_from_manifests(tmp_path):
    import yaml

    agent_dir = tmp_path / "faq-agent"
    agent_dir.mkdir()
    manifest_data = {
        "api_version": "agent.platform/v1",
        "kind": "AgentPackage",
        "metadata": {"id": "faq-agent", "name": "FAQ Agent"},
        "version": {"package_version": "1.0.0"},
        "routing": {
            "routing_rules": [
                {"keywords": ["faq", "help"], "description": "FAQ routing"}
            ],
        },
        "output": {"protocol": "agent-chat/v1"},
    }
    (agent_dir / "manifest.yaml").write_text(yaml.dump(manifest_data))

    sr = SemanticRouter(confidence_threshold=0.5)
    registry = AgentRegistry(tmp_path, semantic_router=sr)
    await registry.discover()

    match = sr.match("faq help me please")
    assert match is not None
    assert match.agent_id == "faq-agent"


def test_manifest_routing_rule_model():
    rule = ManifestRoutingRule(
        keywords=["a", "b"],
        patterns=[r"\d+"],
        description="test rule",
    )
    assert rule.keywords == ["a", "b"]
    assert rule.patterns == [r"\d+"]
    assert rule.description == "test rule"


def test_manifest_routing_with_routing_rules():
    routing = ManifestRouting(
        routing_rules=[
            ManifestRoutingRule(keywords=["x"]),
            ManifestRoutingRule(patterns=[r"y"]),
        ]
    )
    assert len(routing.routing_rules) == 2
    assert routing.rules is None
