import hashlib
import logging
from dataclasses import dataclass

from agent_platform.config import Settings
from agent_platform.domain.models import AgentRequest, AgentSpec
from agent_platform.observability.tracing import get_tracer
from agent_platform.registry.registry import AgentRegistry
from agent_platform.router_semantic import SemanticRouter

logger = logging.getLogger(__name__)
tracer = get_tracer("agent_platform.router")


@dataclass(frozen=True)
class RouteResult:
    """路由结果数据类。
    
    包含目标 Agent 的规格信息、路由原因以及相关的部署和流量控制信息。
    """
    agent_spec: AgentSpec
    reason: str
    deployment_id: str | None = None
    traffic_bucket: int | None = None


class AgentRouter:
    """Agent 路由器。
    
    负责根据请求上下文（如 Agent ID、App ID、租户、渠道等）或语义匹配，
    将传入请求路由到对应的 Agent 实例，并处理流量分配和金丝雀部署。
    """
    def __init__(
        self,
        registry: AgentRegistry,
        settings: Settings,
        semantic_router: SemanticRouter | None = None,
    ):
        self.registry = registry
        self.settings = settings
        self.semantic_router = semantic_router

    async def route(self, request: AgentRequest) -> RouteResult:
        """执行请求路由。

        依次检查 Agent ID、App ID、租户 ID、渠道 ID，以及语义匹配，
        最后降级为默认 Agent。
        """
        with tracer.start_as_current_span("agent_route") as span:
            result = await self._route_inner(request)
            span.set_attribute("route.agent_id", result.agent_spec.agent_id)
            span.set_attribute("route.reason", result.reason)
            if result.deployment_id:
                span.set_attribute("route.deployment_id", result.deployment_id)
            if result.traffic_bucket is not None:
                span.set_attribute("route.traffic_bucket", result.traffic_bucket)
            logger.info(
                "route decision: agent=%s reason=%s deployment=%s bucket=%s",
                result.agent_spec.agent_id,
                result.reason,
                result.deployment_id,
                result.traffic_bucket,
            )
            return result

    async def _route_inner(self, request: AgentRequest) -> RouteResult:
        # 1. 显式指定了 Agent ID
        if request.agent_id:
            return await self._route_agent(request.agent_id, request, "agent_id")

        # 2. 根据元数据中的 App ID 进行路由
        app_id = request.metadata.get("app_id")
        if app_id:
            try:
                return await self._route_agent(app_id, request, "app_id")
            except LookupError:
                pass

        # 3. 根据租户 ID 进行路由
        retailer_id = request.context.tenant.org_id
        if retailer_id:
            try:
                return await self._route_agent(retailer_id, request, "tenant.org_id")
            except LookupError:
                pass

        # 4. 根据渠道 ID 进行路由
        channel_id = request.context.channel.channel_id
        if channel_id:
            try:
                return await self._route_agent(channel_id, request, "channel.channel_id")
            except LookupError:
                pass

        # 5. 如果启用了语义路由，尝试进行语义匹配
        if self.semantic_router:
            semantic_match = self.semantic_router.match(request.input.query)
            if semantic_match:
                try:
                    return await self._route_agent(
                        semantic_match.agent_id,
                        request,
                        semantic_match.reason,
                    )
                except LookupError:
                    pass

        # 6. 如果都没有匹配，使用配置的默认 Agent
        if not self.settings.default_agent_id:
            raise LookupError("no agent matched and no default_agent_id configured")
        return await self._route_agent(self.settings.default_agent_id, request, "default_agent")

    async def _route_agent(self, agent_id: str, request: AgentRequest, reason: str) -> RouteResult:
        """解析 Agent 的具体部署信息并进行灰度流量计算。"""
        spec = await self.registry.get(agent_id)
        canary = None
        # 如果是生产环境，尝试查找是否有金丝雀部署
        if request.options.runtime_profile == "prod":
            canary = await self.registry.resolve_canary_deployment(
                agent_id=agent_id,
                channel="prod",
                tenant_id=request.context.tenant.tenant_id,
            )
        # 获取基础的部署实例
        deployment = await self.registry.resolve_deployment(
            agent_id=agent_id,
            channel=request.options.runtime_profile,
            tenant_id=request.context.tenant.tenant_id,
        )

        traffic_bucket = None
        # 如果存在金丝雀部署，根据稳定 key 计算流量桶来决定是否路由到金丝雀版本
        if canary:
            stable_key = self._stable_user_key(request)
            traffic_bucket = self._compute_bucket(stable_key)
            if traffic_bucket < canary.traffic_percent:
                deployment = canary

        return RouteResult(
            agent_spec=spec,
            reason=reason,
            deployment_id=deployment.deployment_id if deployment else None,
            traffic_bucket=traffic_bucket,
        )

    @staticmethod
    def _stable_user_key(request: AgentRequest) -> str:
        """生成一个稳定的用户唯一标识，用于一致性哈希和流量分配。"""
        parts = [
            request.context.tenant.tenant_id or "",
            request.context.user.user_id or "",
            request.session_id or "",
        ]
        return "|".join(parts)

    @staticmethod
    def _compute_bucket(key: str) -> int:
        """将稳定的用户标识映射到 0-99 的流量桶中。"""
        digest = hashlib.md5(key.encode(), usedforsecurity=False).hexdigest()
        return int(digest[:8], 16) % 100
