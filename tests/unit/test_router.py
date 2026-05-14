from pathlib import Path

from agent_platform.config import Settings
from agent_platform.domain.models import AgentInput, AgentRequest, RequestContext, TenantContext
from agent_platform.registry.registry import AgentRegistry
from agent_platform.router import AgentRouter


def test_router_uses_explicit_agent_id():
    registry = AgentRegistry(Path("agents"))
    router = AgentRouter(registry, Settings(default_agent_id="myj"))
    request = AgentRequest(agent_id="myj", input=AgentInput(query="hello"))

    route = router.route(request)

    assert route.agent_spec.agent_id == "myj"
    assert route.reason == "agent_id"
    assert route.deployment_id == "dep_myj_dev_default"


def test_router_uses_retailer_id():
    registry = AgentRegistry(Path("agents"))
    router = AgentRouter(registry, Settings(default_agent_id="myj"))
    request = AgentRequest(
        context=RequestContext(tenant=TenantContext(retailer_id="myj")),
        input=AgentInput(query="hello"),
    )

    route = router.route(request)

    assert route.agent_spec.agent_id == "myj"
    assert route.reason == "tenant.retailer_id"
    assert route.deployment_id == "dep_myj_dev_default"
