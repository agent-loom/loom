"""Agent 注册中心：发现、注册、部署管理。"""

from pathlib import Path

from agent_platform.domain.models import AgentDeployment, AgentDeploymentStatus, AgentSpec
from agent_platform.registry.loader import ManifestLoader


class AgentNotFoundError(LookupError):
    """在注册中心中未找到指定 Agent 时抛出。"""


class AgentRegistry:
    """Agent 注册中心，提供发现、注册和部署管理功能。"""

    def __init__(self, root: Path, loader: ManifestLoader | None = None):
        """初始化注册中心。"""
        self.root = root
        self.loader = loader or ManifestLoader()
        self._cache: dict[str, AgentSpec] = {}
        self._deployments: dict[str, AgentDeployment] = {}

    def discover(self) -> dict[str, AgentSpec]:
        """扫描 root 目录下的 manifest.yaml，发现并注册所有 Agent。"""
        self._cache.clear()
        if not self.root.exists():
            return self._cache

        for manifest_path in sorted(self.root.glob("*/manifest.yaml")):
            spec = self.loader.load_file(manifest_path)
            self.register(spec)
        return dict(self._cache)

    def register(self, spec: AgentSpec) -> AgentSpec:
        """注册一个 AgentSpec 并创建对应的 dev 部署记录。"""
        self._cache[spec.agent_id] = spec
        self.deploy(
            agent_id=spec.agent_id,
            version=spec.version,
            channel="dev",
            status=AgentDeploymentStatus.REGISTERED,
        )
        return spec

    def list_agents(self) -> list[AgentSpec]:
        """列出所有已注册的 Agent，必要时自动触发发现。"""
        if not self._cache:
            self.discover()
        return list(self._cache.values())

    def get(self, agent_id: str) -> AgentSpec:
        """根据 agent_id 获取 AgentSpec，未找到时抛出 AgentNotFoundError。"""
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
        """创建或更新一条部署记录。"""
        if agent_id not in self._cache:
            self.get(agent_id)

        deployment_id = self._deployment_id(agent_id, channel, tenant_id)
        if status == AgentDeploymentStatus.PROD_CANARY:
            deployment_id = self._deployment_id(agent_id, channel, tenant_id, slot="canary")

        deployment = AgentDeployment(
            deployment_id=deployment_id,
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
        """列出所有部署记录。"""
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
        """解析指定 Agent 在给定 channel 的部署，优先匹配租户级别。"""
        if not self._cache:
            self.discover()

        tenant_deployment = self._deployments.get(self._deployment_id(agent_id, channel, tenant_id))
        if tenant_deployment:
            return tenant_deployment
        return self._deployments.get(self._deployment_id(agent_id, channel, None))

    def resolve_canary_deployment(
        self,
        *,
        agent_id: str,
        channel: str = "prod",
        tenant_id: str | None = None,
    ) -> AgentDeployment | None:
        """解析指定 Agent 的金丝雀部署。"""
        if not self._cache:
            self.discover()

        tenant_deployment = self._deployments.get(
            self._deployment_id(agent_id, channel, tenant_id, slot="canary")
        )
        if tenant_deployment:
            return tenant_deployment
        return self._deployments.get(self._deployment_id(agent_id, channel, None, slot="canary"))

    @staticmethod
    def _deployment_id(
        agent_id: str,
        channel: str,
        tenant_id: str | None,
        *,
        slot: str | None = None,
    ) -> str:
        tenant_suffix = tenant_id or "default"
        if slot:
            return f"dep_{agent_id}_{channel}_{slot}_{tenant_suffix}"
        return f"dep_{agent_id}_{channel}_{tenant_suffix}"
