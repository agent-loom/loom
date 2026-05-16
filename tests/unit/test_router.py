from pathlib import Path

import pytest

from agent_platform.config import Settings
from agent_platform.domain.models import (
    AgentDeploymentStatus,
    AgentInput,
    AgentRequest,
    RequestContext,
    TenantContext,
)
from agent_platform.registry.registry import AgentRegistry
from agent_platform.router import AgentRouter
from agent_platform.router_semantic import SemanticRouter, SemanticRule


@pytest.mark.asyncio
async def test_router_uses_explicit_agent_id():
    registry = AgentRegistry(Path("agents"))
    router = AgentRouter(registry, Settings(default_agent_id="myj"))
    request = AgentRequest(agent_id="myj", input=AgentInput(query="hello"))

    route = await router.route(request)

    assert route.agent_spec.agent_id == "myj"
    assert route.reason == "agent_id"
    assert route.deployment_id == "dep_myj_dev_default"


@pytest.mark.asyncio
async def test_router_uses_retailer_id():
    registry = AgentRegistry(Path("agents"))
    router = AgentRouter(registry, Settings(default_agent_id="myj"))
    request = AgentRequest(
        context=RequestContext(tenant=TenantContext(retailer_id="myj")),
        input=AgentInput(query="hello"),
    )

    route = await router.route(request)

    assert route.agent_spec.agent_id == "myj"
    assert route.reason == "tenant.org_id"
    assert route.deployment_id == "dep_myj_dev_default"


@pytest.mark.asyncio
async def test_router_canary_miss_falls_back_to_stable_prod(monkeypatch):
    registry = AgentRegistry(Path("agents"))
    await registry.discover()
    await registry.deploy(
        agent_id="myj",
        version="0.1.0",
        channel="prod",
        status=AgentDeploymentStatus.PROD,
        traffic_percent=100,
    )
    await registry.deploy(
        agent_id="myj",
        version="0.1.0",
        channel="prod",
        status=AgentDeploymentStatus.PROD_CANARY,
        traffic_percent=5,
    )
    router = AgentRouter(registry, Settings(default_agent_id="myj"))
    monkeypatch.setattr(AgentRouter, "_compute_bucket", staticmethod(lambda _: 90))
    request = AgentRequest(
        agent_id="myj",
        context=RequestContext(tenant=TenantContext(retailer_id="myj")),
        input=AgentInput(query="hello"),
        options={"runtime_profile": "prod"},
    )

    route = await router.route(request)

    assert route.deployment_id == "dep_myj_prod_default"
    assert route.traffic_bucket == 90


@pytest.mark.asyncio
async def test_router_canary_hit_uses_canary_deployment(monkeypatch):
    registry = AgentRegistry(Path("agents"))
    await registry.discover()
    await registry.deploy(
        agent_id="myj",
        version="0.1.0",
        channel="prod",
        status=AgentDeploymentStatus.PROD,
    )
    await registry.deploy(
        agent_id="myj",
        version="0.1.0",
        channel="prod",
        status=AgentDeploymentStatus.PROD_CANARY,
        traffic_percent=5,
    )
    router = AgentRouter(registry, Settings(default_agent_id="myj"))
    monkeypatch.setattr(AgentRouter, "_compute_bucket", staticmethod(lambda _: 3))
    request = AgentRequest(
        agent_id="myj",
        context=RequestContext(tenant=TenantContext(retailer_id="myj")),
        input=AgentInput(query="hello"),
        options={"runtime_profile": "prod"},
    )

    route = await router.route(request)

    assert route.deployment_id == "dep_myj_prod_canary_default"
    assert route.traffic_bucket == 3


@pytest.mark.asyncio
async def test_router_uses_semantic_route_before_default_agent():
    registry = AgentRegistry(Path("agents"))
    semantic_router = SemanticRouter(confidence_threshold=0.5)
    semantic_router.add_rule(
        SemanticRule(
            agent_id="echo",
            keywords=["echo"],
            description="echo fallback",
        )
    )
    router = AgentRouter(
        registry,
        Settings(default_agent_id="myj"),
        semantic_router=semantic_router,
    )
    request = AgentRequest(input=AgentInput(query="please echo this"))

    route = await router.route(request)

    assert route.agent_spec.agent_id == "echo"
    assert route.reason == "semantic:echo fallback"
