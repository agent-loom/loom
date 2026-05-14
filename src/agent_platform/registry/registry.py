from pathlib import Path

from agent_platform.domain.models import AgentDeployment, AgentDeploymentStatus, AgentSpec
from agent_platform.registry.loader import ManifestLoader


class AgentNotFoundError(LookupError):
    pass


class AgentRegistry:
    def __init__(self, root: Path, loader: ManifestLoader | None = None):
        self.root = root
        self.loader = loader or ManifestLoader()
        self._cache: dict[str, AgentSpec] = {}
        self._deployments: dict[str, AgentDeployment] = {}

    def discover(self) -> dict[str, AgentSpec]:
        self._cache.clear()
        if not self.root.exists():
            return self._cache

        for manifest_path in sorted(self.root.glob("*/manifest.yaml")):
            spec = self.loader.load_file(manifest_path)
            self.register(spec)
        return dict(self._cache)

    def register(self, spec: AgentSpec) -> AgentSpec:
        self._cache[spec.agent_id] = spec
        self.deploy(
            agent_id=spec.agent_id,
            version=spec.version,
            channel="dev",
            status=AgentDeploymentStatus.REGISTERED,
        )
        return spec

    def list_agents(self) -> list[AgentSpec]:
        if not self._cache:
            self.discover()
        return list(self._cache.values())

    def get(self, agent_id: str) -> AgentSpec:
        if not self._cache:
            self.discover()
        try:
            return self._cache[agent_id]
        except KeyError as exc:
            raise AgentNotFoundError(f"agent not found: {agent_id}") from exc

    def deploy(
        self,
        *,
        agent_id: str,
        version: str,
        channel: str,
        status: AgentDeploymentStatus,
        tenant_id: str | None = None,
        traffic_percent: int = 100,
    ) -> AgentDeployment:
        if agent_id not in self._cache:
            self.get(agent_id)

        deployment = AgentDeployment(
            deployment_id=self._deployment_id(agent_id, channel, tenant_id),
            agent_id=agent_id,
            version=version,
            channel=channel,
            status=status,
            tenant_id=tenant_id,
            traffic_percent=traffic_percent,
        )
        self._deployments[deployment.deployment_id] = deployment
        return deployment

    def list_deployments(self) -> list[AgentDeployment]:
        if not self._cache:
            self.discover()
        return list(self._deployments.values())

    def resolve_deployment(
        self,
        *,
        agent_id: str,
        channel: str = "dev",
        tenant_id: str | None = None,
    ) -> AgentDeployment | None:
        if not self._cache:
            self.discover()

        tenant_deployment = self._deployments.get(self._deployment_id(agent_id, channel, tenant_id))
        if tenant_deployment:
            return tenant_deployment
        return self._deployments.get(self._deployment_id(agent_id, channel, None))

    @staticmethod
    def _deployment_id(agent_id: str, channel: str, tenant_id: str | None) -> str:
        tenant_suffix = tenant_id or "default"
        return f"dep_{agent_id}_{channel}_{tenant_suffix}"
