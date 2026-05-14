import hashlib
from dataclasses import dataclass

from agent_platform.config import Settings
from agent_platform.domain.models import AgentRequest, AgentSpec
from agent_platform.registry.registry import AgentRegistry


@dataclass(frozen=True)
class RouteResult:
    agent_spec: AgentSpec
    reason: str
    deployment_id: str | None = None
    traffic_bucket: int | None = None


class AgentRouter:
    def __init__(self, registry: AgentRegistry, settings: Settings):
        self.registry = registry
        self.settings = settings

    def route(self, request: AgentRequest) -> RouteResult:
        if request.agent_id:
            return self._route_agent(request.agent_id, request, "agent_id")

        app_id = request.metadata.get("app_id")
        if app_id:
            try:
                return self._route_agent(app_id, request, "app_id")
            except LookupError:
                pass

        retailer_id = request.context.tenant.retailer_id
        if retailer_id:
            try:
                return self._route_agent(retailer_id, request, "tenant.retailer_id")
            except LookupError:
                pass

        channel_id = request.context.channel.channel_id
        if channel_id:
            try:
                return self._route_agent(channel_id, request, "channel.channel_id")
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

        traffic_bucket = None
        if deployment and deployment.traffic_percent < 100:
            stable_key = self._stable_user_key(request)
            traffic_bucket = self._compute_bucket(stable_key)
            if traffic_bucket >= deployment.traffic_percent:
                raise LookupError(
                    f"request outside canary bucket: bucket={traffic_bucket} "
                    f"traffic_percent={deployment.traffic_percent}"
                )

        return RouteResult(
            agent_spec=spec,
            reason=reason,
            deployment_id=deployment.deployment_id if deployment else None,
            traffic_bucket=traffic_bucket,
        )

    @staticmethod
    def _stable_user_key(request: AgentRequest) -> str:
        parts = [
            request.context.tenant.tenant_id or "",
            request.context.user.user_id or "",
            request.session_id or "",
        ]
        return "|".join(parts)

    @staticmethod
    def _compute_bucket(key: str) -> int:
        digest = hashlib.md5(key.encode(), usedforsecurity=False).hexdigest()
        return int(digest[:8], 16) % 100
