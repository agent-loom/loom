from dataclasses import dataclass

from agent_platform.config import Settings
from agent_platform.domain.models import AgentRequest, AgentSpec
from agent_platform.registry.registry import AgentRegistry


@dataclass(frozen=True)
class RouteResult:
    agent_spec: AgentSpec
    reason: str
    deployment_id: str | None = None


class AgentRouter:
    def __init__(self, registry: AgentRegistry, settings: Settings):
        self.registry = registry
        self.settings = settings

    def route(self, request: AgentRequest) -> RouteResult:
        if request.agent_id:
            return self._route_agent(request.agent_id, request, "agent_id")

        retailer_id = request.context.tenant.retailer_id
        if retailer_id:
            try:
                return self._route_agent(retailer_id, request, "tenant.retailer_id")
            except LookupError:
                pass

        return self._route_agent(self.settings.default_agent_id, request, "default_agent")

    def _route_agent(self, agent_id: str, request: AgentRequest, reason: str) -> RouteResult:
        spec = self.registry.get(agent_id)
        deployment = self.registry.resolve_deployment(
            agent_id=agent_id,
            channel=request.options.runtime_profile,
            tenant_id=request.context.tenant.tenant_id,
        )
        return RouteResult(
            agent_spec=spec,
            reason=reason,
            deployment_id=deployment.deployment_id if deployment else None,
        )
