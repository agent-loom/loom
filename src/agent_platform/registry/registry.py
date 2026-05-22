"""Agent 注册中心：发现、注册、部署管理。

设计定位：
  控制面 (Control Plane) 的核心组件，负责 Agent Package 的物理包发现、定义解析、状态生命周期转换和灰度分流。
  架构组件图详见：docs/02-architecture/agent-platform-design.md §2 控制面。

生命周期规范 (docs/02-architecture/agent-platform-core-design.md §3.5)：
  DRAFT (草案) -> ACTIVE (启用) -> DEPRECATED (弃用) -> ARCHIVED (归档)

当前实现差距说明 (P0 TODO)：
  1. register() 当前直接写入 ACTIVE 状态并布署到 dev 渠道，缺少从 DRAFT 状态经人工或 CI 自动化评测达标后触发的 activate() 转化流程。
  2. unregister() 当前是硬删除，缺少 DEPRECATED 和 ARCHIVED 的两级软卸载过渡逻辑。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from agent_platform.domain.models import AgentDeployment, AgentDeploymentStatus, AgentSpec
from agent_platform.registry.loader import ManifestLoader
from agent_platform.router_semantic import SemanticRouter, SemanticRule

if TYPE_CHECKING:
    from agent_platform.persistence.repositories import (
        AgentDefinitionRepository,
        AgentDeploymentRepository,
    )

logger = logging.getLogger(__name__)


class AgentNotFoundError(LookupError):
    """在注册中心中未找到指定 Agent 时抛出。"""


class AgentRegistry:
    """Agent 注册中心，提供发现、注册和部署管理功能。"""

    def __init__(
        self,
        root: Path,
        loader: ManifestLoader | None = None,
        *,
        definition_repo: AgentDefinitionRepository | None = None,
        deployment_repo: AgentDeploymentRepository | None = None,
        semantic_router: SemanticRouter | None = None,
    ):
        self.root = root
        self.loader = loader or ManifestLoader()
        self.semantic_router = semantic_router

        # We fallback to in-memory repos if none provided
        if definition_repo is None or deployment_repo is None:
            from agent_platform.persistence.memory import (
                InMemoryAgentDefinitionRepository,
                InMemoryAgentDeploymentRepository,
            )
            self._definition_repo = definition_repo or InMemoryAgentDefinitionRepository()
            self._deployment_repo = deployment_repo or InMemoryAgentDeploymentRepository()
        else:
            self._definition_repo = definition_repo
            self._deployment_repo = deployment_repo

        # We still keep a small cache to map agent_id to its local package_path
        # But all deployment/routing state goes to DB.
        self._local_specs: dict[str, AgentSpec] = {}
        self._deleted_ids: set[str] = set()

    async def discover(self) -> dict[str, AgentSpec]:
        """扫描 root 目录下的 manifest.yaml，发现并注册所有 Agent（写 DB）。"""
        self._local_specs.clear()
        if not self.root.exists():
            return self._local_specs

        for manifest_path in sorted(self.root.glob("*/manifest.yaml")):
            spec = self.loader.load_file(manifest_path)
            if spec.agent_id in self._deleted_ids:
                continue
            await self.register(spec)
        return dict(self._local_specs)

    async def persist_definition(self, spec: AgentSpec) -> None:
        """将 Agent 定义写入持久化存储（如果配置了 repo）。"""
        from agent_platform.domain.models import AgentDefinition, AgentDefinitionStatus
        definition = AgentDefinition(
            agent_id=spec.agent_id,
            version=spec.version,
            status=AgentDefinitionStatus.ACTIVE,
            manifest=spec.manifest,
        )
        await self._definition_repo.save(definition)

    async def register(self, spec: AgentSpec) -> AgentSpec:
        """注册一个 AgentSpec 并创建对应的 dev 部署记录。

        TODO: 核心设计规范差距 (core-design.md §3.5)
        当前实现简化为直接转为 ACTIVE 状态。后续版本需重构为：
        1. 注册时默认状态设为 DRAFT。
        2. 通过 `docs/04-devflow/` CI 评测流运行 evals.runner 检验。
        3. 评测指标通过后，由 activate() API 将定义状态提升为 ACTIVE 并同步至路由表。
        """
        self._deleted_ids.discard(spec.agent_id)
        self._local_specs[spec.agent_id] = spec
        await self.persist_definition(spec)
        await self.deploy(
            agent_id=spec.agent_id,
            version=spec.version,
            channel="dev",
            status=AgentDeploymentStatus.REGISTERED,
        )
        self._load_routing_rules(spec)
        return spec

    async def unregister(self, agent_id: str) -> None:
        """从注册中心彻底注销一个 Agent，阻止从 DB 或磁盘自动发现与加载。

        TODO: 核心设计规范差距 (core-design.md §3.5)
        当前实现直接将 Agent 从物理缓存中移除并打上删除标记。
        未来应支持两级软下线流程：
        1. deprecate(): 将状态设为 DEPRECATED，拒绝新 Session 路由，但保持已有 Session 存活。
        2. archive(): 彻底转为 ARCHIVED，全量下线并释放资源。
        """
        self._local_specs.pop(agent_id, None)
        self._deleted_ids.add(agent_id)

    def _load_routing_rules(self, spec: AgentSpec) -> None:
        """Extract manifest routing rules and register them with the SemanticRouter."""
        if not self.semantic_router:
            return
        for manifest_rule in spec.manifest.routing.routing_rules:
            rule = SemanticRule(
                agent_id=spec.agent_id,
                keywords=manifest_rule.keywords,
                patterns=manifest_rule.patterns,
                description=manifest_rule.description,
            )
            self.semantic_router.add_rule(rule)
            logger.info(
                "loaded routing rule for %s: %s",
                spec.agent_id,
                manifest_rule.description or "(unnamed)",
            )

    async def list_agents(self) -> list[AgentSpec]:
        """列出所有已注册的 Agent，必要时自动触发发现。"""
        if not self._local_specs:
            await self.discover()
        # Querying DB to sync is an option, but for now just return discovery results.
        return list(self._local_specs.values())

    async def get(self, agent_id: str) -> AgentSpec:
        """根据 agent_id 获取 AgentSpec，未找到时抛出 AgentNotFoundError。"""
        if agent_id in self._deleted_ids:
            raise AgentNotFoundError(f"agent not found: {agent_id}")
        if not self._local_specs:
            await self.discover()

        spec = self._local_specs.get(agent_id)
        if spec is None:
            # Maybe it is in DB?
            db_def = await self._definition_repo.get_latest(agent_id)
            if db_def:
                # Reconstruct spec using default path when the local cache misses.
                spec = AgentSpec(manifest=db_def.manifest, package_path=self.root / agent_id)
                self._local_specs[agent_id] = spec
                return spec
            raise AgentNotFoundError(f"agent not found: {agent_id}")
        return spec

    async def deploy(
        self,
        *,
        agent_id: str,
        version: str,
        channel: str,
        status: AgentDeploymentStatus,
        tenant_id: str | None = None,
        traffic_percent: int = 100,
    ) -> AgentDeployment:
        """创建或更新一条部署记录（直接入库）。"""
        # Ensure agent exists in memory
        await self.get(agent_id)

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
        await self.persist_deployment(deployment)
        return deployment

    async def persist_deployment(self, deployment: AgentDeployment) -> None:
        """将部署记录写入持久化存储。"""
        await self._deployment_repo.save(deployment)

    async def list_deployments(self) -> list[AgentDeployment]:
        """列出所有部署记录（来自 DB）。"""
        if not self._local_specs:
            await self.discover()
        return await self._deployment_repo.list_all()

    async def get_deployment(self, deployment_id: str) -> AgentDeployment | None:
        """按 deployment_id 获取部署记录。"""
        return await self._deployment_repo.get(deployment_id)

    async def resolve_deployment(
        self,
        *,
        agent_id: str,
        channel: str = "dev",
        tenant_id: str | None = None,
    ) -> AgentDeployment | None:
        """解析指定 Agent 在给定 channel 的部署，优先匹配租户级别。"""
        if not self._local_specs:
            await self.discover()

        # Try tenant specific deployment
        if tenant_id:
            dep = await self._deployment_repo.resolve(
                agent_id=agent_id,
                channel=channel,
                tenant_id=tenant_id,
            )
            if dep:
                return dep
        # Fallback to general deployment
        return await self._deployment_repo.resolve(
            agent_id=agent_id,
            channel=channel,
            tenant_id=None,
        )

    async def resolve_canary_deployment(
        self,
        *,
        agent_id: str,
        channel: str = "prod",
        tenant_id: str | None = None,
    ) -> AgentDeployment | None:
        """解析指定 Agent 的金丝雀部署。"""
        if not self._local_specs:
            await self.discover()

        deployment_id = self._deployment_id(agent_id, channel, tenant_id, slot="canary")
        dep = await self._deployment_repo.get(deployment_id)
        if dep:
            return dep

        if tenant_id:
            fallback_id = self._deployment_id(agent_id, channel, None, slot="canary")
            return await self._deployment_repo.get(fallback_id)
        return None

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
