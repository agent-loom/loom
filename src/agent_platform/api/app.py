import hmac
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import (
    BackgroundTasks,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import StreamingResponse

from agent_platform.api.access_log import AccessLogMiddleware
from agent_platform.api.admin import router as admin_router
from agent_platform.api.admin_deps import AdminDeps
from agent_platform.api.auth import AuthIdentity, require_role, require_scope
from agent_platform.api.input_sanitizer import InputSanitizationMiddleware
from agent_platform.api.rate_limiter import RateLimiterMiddleware
from agent_platform.api.service_auth import ServiceAuthError, ServiceAuthProvider
from agent_platform.api.streaming import stream_agent_response
from agent_platform.api.websocket import AgentWebSocketManager
from agent_platform.artifacts.signer import ArtifactSigner
from agent_platform.config import get_settings
from agent_platform.devflow.agents import ArchitectureDesignAgent, TestGenerationAgent
from agent_platform.devflow.issue_generator import IssueGenerator
from agent_platform.devflow.orchestrator import DevFlowOrchestrator
from agent_platform.devflow.requirement_parser import RequirementParser
from agent_platform.devflow.runner.factory import create_adapter
from agent_platform.devflow.runner.runner import CodingAgentRunner
from agent_platform.devflow.runner.workspace import WorkspaceManager
from agent_platform.devflow.scaffolder import AgentScaffolder
from agent_platform.devflow.task_pack import TaskPackGenerator
from agent_platform.domain.models import (
    AgentDeploymentStatus,
    AgentError,
    AgentIdentity,
    AgentOutput,
    AgentRequest,
    AgentResponse,
    OutputStatus,
    ResponseText,
    ResponseTrace,
    RuntimeRequest,
)
from agent_platform.evals.runner import EvalReport, EvalRunner
from agent_platform.hooks import HookContext, HookRegistry
from agent_platform.integrations.gitlab.adapter import GitLabAdapter
from agent_platform.integrations.gitlab.webhook import (
    GitLabEventHandler,
    GitLabWebhookError,
    GitLabWebhookVerifier,
)
from agent_platform.integrations.plane.adapter import PlaneAdapter
from agent_platform.integrations.plane.webhook import (
    PlaneWebhookError,
    PlaneWebhookVerifier,
)
from agent_platform.knowledge import KnowledgeService
from agent_platform.observability.logging_config import setup_logging
from agent_platform.observability.metrics import MetricsCollector
from agent_platform.persistence.context import AuditContext, set_audit_context
from agent_platform.persistence.memory import (
    InMemoryAgentRunRepository,
    InMemoryAgentSessionRepository,
    InMemoryCodingJobRepository,
    InMemoryDeploymentAuditRepository,
    InMemoryEvalRunRepository,
    InMemoryRoutingDecisionRepository,
    InMemoryToolAuditRepository,
    InMemoryWebhookDeliveryRepository,
)
from agent_platform.evolution.memory_repository import (
    InMemoryRuntimeMemoryRepository,
    InMemorySkillRepository,
    InMemoryEvolutionMemoryRepository,
)
from agent_platform.policy import PolicyEngine
from agent_platform.registry.artifact import ArtifactStore
from agent_platform.registry.deployment import DeploymentAuditLog
from agent_platform.registry.registry import AgentNotFoundError, AgentRegistry
from agent_platform.router import AgentRouter
from agent_platform.router_semantic import SemanticRouter
from agent_platform.runtime.manager import RuntimeManager
from agent_platform.runtime.model_gateway import ModelGateway
from agent_platform.tools.approval import (
    ApprovalStatus,
    AutoApproveGate,
    InMemoryApprovalGate,
)
from agent_platform.tools.executor import ToolExecutor
from agent_platform.tools.registry import create_default_tool_registry

logger = logging.getLogger(__name__)

_SCOPE_CHAT = require_scope("chat")
_SCOPE_DEPLOY = require_scope("deploy")
_SCOPE_ADMIN = require_scope("admin")
_SCOPE_EVAL = require_scope("eval")
_SCOPE_REGISTER = require_scope("register")
_SCOPE_ROLLBACK = require_scope("rollback")
_SCOPE_READ = require_scope("read")
_ROLE_ADMIN = require_role("platform_admin")


class RegisterAgentRequest(BaseModel):
    manifest_path: str


class RunEvalRequest(BaseModel):
    agent_id: str


class DeployAgentRequest(BaseModel):
    channel: str = "staging"
    tenant_id: str | None = None
    traffic_percent: int = Field(default=100, ge=0, le=100)
    eval_passed: bool | None = None


class CreateTaskPackRequest(BaseModel):
    task_id: str
    title: str
    task_type: str
    project_id: str
    background: str
    agent_id: str | None = None


class ParseRequirementRequest(BaseModel):
    text: str
    context: dict | None = None


class GenerateIssuesRequest(BaseModel):
    text: str
    project_context: dict | None = None


class ScaffoldAgentRequest(BaseModel):
    agent_id: str
    name: str
    description: str = ""
    owner: str = "platform"
    domain: str = "general"
    mode: str = "single_worker"


class DesignAnalysisRequest(BaseModel):
    requirement_text: str
    context: dict | None = None


class TestPlanRequest(BaseModel):
    agent_id: str
    change_type: str
    changed_files: list[str] | None = None


class RollbackRequest(BaseModel):
    agent_id: str
    channel: str = "prod"
    actor: str = "system"


class ResolveApprovalRequest(BaseModel):
    status: str  # "approved" or "rejected"
    actor: str


class AnalyzeEvolutionEventRequest(BaseModel):
    event_type: str
    agent_id: str
    tenant_id: str = "default"
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)


class DismissProposalRequest(BaseModel):
    reason: str = ""


class CreateMemoryRequest(BaseModel):
    agent_id: str
    tenant_id: str = "default"
    type: str  # pattern | constraint | preference | fix_recipe | knowledge
    content: str
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)
    source_proposal_id: str | None = None


class CreateRuntimeMemoryRequest(BaseModel):
    agent_id: str
    tenant_id: str = "default"
    scope: str  # session | user | tenant | agent
    subject_id: str | None = None
    session_id: str | None = None
    type: str  # preference | session_summary | context_hint | user_profile
    content: str
    source_type: str = "user_input"
    source_id: str | None = None
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    privacy_level: str = "internal"
    ttl_seconds: int | None = None


class MemoryFeedbackRequest(BaseModel):
    helpful: bool


class CreateSkillRequest(BaseModel):
    agent_id: str
    name: str
    description: str = ""
    path: str
    provenance: str = "user_created"
    tags: list[str] = Field(default_factory=list)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """请求上下文中间件。
    
    负责在每个请求中提取或生成 request_id 和 tenant_id，并将其附加到请求上下文中，
    最后在响应头中带上 X-Request-ID。
    """
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or f"req_{uuid4().hex}"
        tenant_id = request.headers.get("x-tenant-id")
        request.state.request_id = request_id
        request.state.tenant_id = tenant_id
        response: Response = await call_next(request)
        final_req_id = getattr(request.state, "request_id", None)
        if final_req_id:
            response.headers["X-Request-ID"] = final_req_id
        return response


class AuthMiddleware(BaseHTTPMiddleware):
    """身份验证中间件。

    校验传入请求的 API 密钥并将 AuthIdentity 填入 request.state.auth，
    使得下游 require_role()/require_scope() 依赖可正常工作。
    支持三种认证模式：API Key、Service Token（JWT/Shared Secret）、匿名。
    """

    _ALL_SCOPES = ["chat", "deploy", "admin", "eval", "register", "rollback", "read"]
    _SERVICE_SCOPES = ["chat", "deploy", "eval", "read"]
    _SKIP_PATHS = {
        "/health",
        "/health/ready",
        "/metrics",
        "/docs",
        "/openapi.json",
        "/redoc",
        # 外部 webhook 平台通常不能附加平台 API Key。
        # 这些入口由各自 endpoint 内部的 webhook secret/signature 认证。
        "/api/v1/integrations/plane/webhook",
        "/api/v1/integrations/gitlab/webhook",
    }

    def __init__(
        self,
        app,
        api_key: str | None = None,
        key_store=None,
        service_auth: ServiceAuthProvider | None = None,
        env: str = "dev",
    ):
        super().__init__(app)
        self.api_key = api_key
        self.key_store = key_store
        self.service_auth = service_auth
        self._env = env

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self._SKIP_PATHS:
            return await call_next(request)

        # 服务间鉴权：JWT service token
        service_token = request.headers.get("x-service-token")
        if service_token and self.service_auth:
            try:
                identity = self.service_auth.verify_token(service_token)
                self._set_auth_context(request, AuthIdentity(
                    subject=identity.service_id,
                    tenant_id=request.headers.get("x-tenant-id", "default"),
                    role="service",
                    scopes=identity.permissions or self._SERVICE_SCOPES,
                ))
                return await call_next(request)
            except ServiceAuthError:
                return JSONResponse(
                    status_code=401,
                    content={"error": {"code": "UNAUTHORIZED", "message": "invalid service token"}},
                )

        # 服务间鉴权：Shared Secret
        service_id = request.headers.get("x-service-id")
        service_secret = request.headers.get("x-service-secret")
        if service_id and service_secret and self.service_auth:
            try:
                identity = self.service_auth.verify_shared_secret(service_id, service_secret)
                self._set_auth_context(request, AuthIdentity(
                    subject=identity.service_id,
                    tenant_id=request.headers.get("x-tenant-id", "default"),
                    role="service",
                    scopes=identity.permissions or self._SERVICE_SCOPES,
                ))
                return await call_next(request)
            except ServiceAuthError:
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": {
                            "code": "UNAUTHORIZED",
                            "message": "invalid service credentials",
                        }
                    },
                )

        if not self.api_key and self.key_store is None:
            anon_role = "readonly" if self._env == "production" else "platform_admin"
            anon_scopes = ["read"] if self._env == "production" else self._ALL_SCOPES
            self._set_auth_context(request, AuthIdentity(
                subject="anonymous",
                tenant_id=request.headers.get("x-tenant-id", "default"),
                role=anon_role,
                scopes=anon_scopes,
            ))
            return await call_next(request)

        token = None
        auth_header = request.headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]
        if not token:
            token = request.headers.get("x-api-key")

        if not token:
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "code": "UNAUTHORIZED",
                        "message": "invalid or missing API key",
                    }
                },
            )

        record = await self._verify_token(token)
        if record is not None:
            self._set_auth_context(request, AuthIdentity(
                subject=record.created_by,
                tenant_id=record.tenant_id,
                role=record.role,
                scopes=record.scopes,
                key_id=record.key_id,
            ))
            return await call_next(request)

        if self.api_key and hmac.compare_digest(token, self.api_key):
            self._set_auth_context(request, AuthIdentity(
                subject="api-key-user",
                tenant_id=request.headers.get("x-tenant-id", "default"),
                role="platform_admin",
                scopes=self._ALL_SCOPES,
            ))
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "invalid or missing API key",
                }
            },
        )

    async def _verify_token(self, token: str):
        if self.key_store is None:
            return None
        if hasattr(self.key_store, "verify_async"):
            return await self.key_store.verify_async(token)
        return self.key_store.verify(token)

    @staticmethod
    def _set_auth_context(request: Request, identity: AuthIdentity) -> None:
        request.state.auth = identity
        request.state.tenant_id = identity.tenant_id
        set_audit_context(
            AuditContext(
                request_id=getattr(request.state, "request_id", None),
                actor=identity.subject,
                tenant_id=identity.tenant_id,
            )
        )


def _validate_startup_config(settings) -> None:
    """Log warnings for common configuration issues at startup."""
    if settings.devflow_runner_adapter == "mock":
        if settings.env == "production" and settings.devflow_repo_url:
            raise ValueError(
                "生产环境禁止使用 mock adapter（DEVFLOW_RUNNER_ADAPTER=mock），"
                "请设置为 'claude_code' 或 'codex'"
            )
        logger.warning(
            "DEVFLOW_RUNNER_ADAPTER=mock — DevFlow will not execute real AI coding tasks. "
            "Set to 'claude_code' or 'codex' for production."
        )
    if settings.env == "production" and not settings.api_key:
        logger.warning(
            "No AGENT_PLATFORM_API_KEY configured in production — "
            "all endpoints are unauthenticated"
        )
    if settings.cors_allowed_origins == "*":
        if settings.env == "production":
            logger.warning(
                "CORS is open to all origins in production. "
                "Set CORS_ALLOWED_ORIGINS to restrict access."
            )
    if not os.getenv("OPENAI_API_KEY") and not os.getenv("ANTHROPIC_API_KEY"):
        logger.warning(
            "未设置 OPENAI_API_KEY 或 ANTHROPIC_API_KEY — "
            "ModelGateway 将使用 Stub 提供商，chat 请求不会调用真实 LLM"
        )
    if settings.env == "production" and not settings.service_jwt_secret:
        logger.warning(
            "生产环境未配置 SERVICE_JWT_SECRET — 服务间鉴权不可用"
        )
    if settings.plane_base_url:
        missing = [
            f for f in (
                "plane_ai_developing_state_id",
                "plane_testing_state_id",
                "plane_human_review_state_id",
                "plane_staging_state_id",
                "plane_done_state_id",
            )
            if not getattr(settings, f)
        ]
        if missing:
            logger.warning(
                "DevFlow state sync may be incomplete — missing: %s",
                ", ".join(missing),
            )
    if settings.env == "production" and not settings.weaviate_url:
        logger.warning(
            "生产环境未配置 WEAVIATE_URL — 知识检索将使用 Stub 后端返回空结果"
        )


@asynccontextmanager
async def _app_lifespan(app: FastAPI):
    """Application lifespan: startup validation and graceful shutdown."""
    settings = getattr(app.state, "_settings", None)
    if settings:
        _validate_startup_config(settings)

    # SQLite 环境自动建表（生产环境应使用 Alembic 迁移）
    db_engine = None
    for name, resource in getattr(app.state, "_closeables", []):
        if name == "SQLAlchemy engine":
            db_engine = resource
            break
    if db_engine and settings and "sqlite" in settings.database_url:
        import agent_platform.persistence.tables  # noqa: F401 — 确保所有表定义已加载
        from agent_platform.storage.base import Base
        async with db_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("SQLite 自动建表完成")

    app.state.started_at = time.time()
    logger.info("Agent Platform started (env=%s)", settings.env if settings else "unknown")

    # Bootstrap API key 初始化（需要在建表之后）
    _key_store = getattr(app.state, "_key_store", None)
    if _key_store is not None and settings and settings.api_key:
        try:
            await _key_store.add_key(
                settings.api_key,
                key_id="bootstrap-key",
                role="platform_admin",
                created_by="bootstrap",
            )
        except Exception:
            logger.debug("Bootstrap key 可能已存在，跳过", exc_info=True)

    # DLQ 后台重试任务
    import asyncio as _asyncio

    _dlq_task: _asyncio.Task | None = None
    retry_svc = getattr(app.state, "webhook_retry_service", None)
    if not retry_svc:
        for _admin_deps_attr in ("admin_deps",):
            _ad = getattr(app.state, _admin_deps_attr, None)
            if _ad and hasattr(_ad, "webhook_retry_service"):
                retry_svc = _ad.webhook_retry_service
                break

    if retry_svc:
        async def _dlq_retry_loop():
            while True:
                try:
                    async def _dlq_dispatch_handler(source, event_type, payload):
                        """DLQ 重试分发：根据来源路由到对应的事件处理器。"""
                        if source == "plane":
                            orchestrator = getattr(app.state, "devflow_orchestrator", None)
                            if orchestrator:
                                await orchestrator.handle_webhook_event(event_type, payload)
                                return
                        elif source == "gitlab":
                            handler = getattr(app.state, "gitlab_event_handler", None)
                            if handler:
                                await handler.handle_event(event_type, payload)
                                return
                        logger.warning("DLQ 重试：未知来源 %s，跳过", source)
                    processed = await retry_svc.process_retries(_dlq_dispatch_handler)
                    if processed:
                        logger.info("DLQ 处理了 %d 条重试", processed)
                except _asyncio.CancelledError:
                    break
                except Exception:
                    logger.debug("DLQ 重试循环异常", exc_info=True)
                await _asyncio.sleep(60)

        _dlq_task = _asyncio.create_task(_dlq_retry_loop())
        logger.info("DLQ 后台重试任务已启动（60s 间隔）")

    # ── 知识同步调度器 ──
    _knowledge_scheduler = getattr(app.state, "_knowledge_scheduler", None)
    if _knowledge_scheduler is not None:
        await _knowledge_scheduler.start()
        logger.info("KnowledgeSyncScheduler 已启动")

    # ── SemanticRouter: 从 Registry 批量加载 routing.rules ──
    _registry = getattr(app.state, "registry", None)
    _sem_router = getattr(app.state, "semantic_router", None)
    if _registry is not None and _sem_router is not None:
        try:
            _loaded_rules = await _sem_router.load_from_registry(_registry)
            if _loaded_rules:
                logger.info(
                    "SemanticRouter 已从 registry 加载 %d 条 routing.rules",
                    _loaded_rules,
                )
        except Exception:
            logger.warning("SemanticRouter 从 registry 加载规则失败", exc_info=True)

    # DevFlow 调度器
    _devflow_scheduler = getattr(app.state, "_devflow_scheduler", None)
    if _devflow_scheduler is not None:
        await _devflow_scheduler.start()

    yield

    if _devflow_scheduler is not None:
        await _devflow_scheduler.stop()

    if _knowledge_scheduler is not None:
        await _knowledge_scheduler.stop()

    if _dlq_task:
        _dlq_task.cancel()
        try:
            await _dlq_task
        except _asyncio.CancelledError:
            pass

    for name, resource in getattr(app.state, "_closeables", []):
        try:
            if hasattr(resource, "close"):
                await _asyncio.wait_for(resource.close(), timeout=10)
            elif hasattr(resource, "dispose"):
                await _asyncio.wait_for(resource.dispose(), timeout=10)
            logger.info("Closed %s", name)
        except TimeoutError:
            logger.warning("关闭 %s 超时（10s），跳过", name)
        except Exception:
            logger.warning("Failed to close %s", name, exc_info=True)
    logger.info("Agent Platform shutdown complete")


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例。
    
    该工厂函数负责：
    1. 初始化日志记录器和配置。
    2. 设置核心组件，如注册中心、路由器、策略引擎、指标收集器等。
    3. 根据配置决定是否使用 SQL 持久化或内存存储。
    4. 配置应用中间件（鉴权、限流、CORS、请求上下文）。
    5. 注册所有的 API 路由和 WebSocket 端点。
    """
    setup_logging()

    settings = get_settings()

    app_semantic_router = SemanticRouter()

    app_policy_engine = PolicyEngine()
    app_knowledge_service = KnowledgeService()

    if settings.weaviate_url:
        from agent_platform.knowledge.service import WeaviateKnowledgeBackend
        weaviate_backend = WeaviateKnowledgeBackend(
            url=settings.weaviate_url,
            api_key=settings.weaviate_api_key,
        )
        app_knowledge_service.register(weaviate_backend)
        logger.info("WeaviateKnowledgeBackend registered (url=%s)", settings.weaviate_url)

        from agent_platform.knowledge.scheduler import KnowledgeSyncScheduler
        from agent_platform.knowledge.sync import DataSynchronization
        _data_sync = DataSynchronization(weaviate_backend)
        _knowledge_scheduler = KnowledgeSyncScheduler(_data_sync, interval_seconds=3600.0)
    else:
        _knowledge_scheduler = None

    app_hook_registry = HookRegistry()
    app_metrics = MetricsCollector()

    from agent_platform.observability.langfuse_tracer import LangfuseTracer
    langfuse_tracer = LangfuseTracer(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )
    if langfuse_tracer.enabled:
        logger.info("Langfuse LLM tracing enabled")

    model_gateway = ModelGateway.create_default()
    tool_registry = create_default_tool_registry()

    # Approval gate: use InMemoryApprovalGate when HITL_ENABLED=true,
    # otherwise default to AutoApproveGate.
    hitl_enabled = os.getenv("HITL_ENABLED", "").lower() == "true"
    approval_gate: InMemoryApprovalGate | AutoApproveGate
    if hitl_enabled:
        approval_gate = InMemoryApprovalGate()
    else:
        approval_gate = AutoApproveGate()

    tool_executor = ToolExecutor(
        registry=tool_registry,
        policy_engine=app_policy_engine,
        hook_registry=app_hook_registry,
        metrics_collector=app_metrics,
        approval_gate=approval_gate,
    )

    db_session_factory = None
    db_engine = None
    _has_explicit_db = bool(os.getenv("DATABASE_URL"))
    if _has_explicit_db and settings.database_url:
        try:
            from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

            db_engine = create_async_engine(
                settings.database_url,
                echo=False,
                pool_pre_ping=True,
                pool_recycle=1800,
                **({} if "sqlite" in settings.database_url else {
                    "pool_size": 10,
                    "max_overflow": 20,
                }),
            )
            db_session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
            logger.info("SQL persistence enabled for %s", settings.database_url)
        except Exception:
            logger.exception("Failed to create SQL engine; falling back to InMemory repos")

    if db_session_factory is not None:
        from agent_platform.persistence.sql import (
            SqlAgentDefinitionRepository,
            SqlAgentDeploymentRepository,
            SqlAgentRunRepository,
            SqlAgentSessionRepository,
            SqlCodingJobRepository,
            SqlDeadLetterQueue,
            SqlDeploymentAuditRepository,
            SqlEvalRunRepository,
            SqlRoutingDecisionRepository,
            SqlToolAuditRepository,
            SqlWebhookDeliveryRepository,
        )

        run_repo = SqlAgentRunRepository(db_session_factory)
        session_repo = SqlAgentSessionRepository(db_session_factory)
        webhook_repo = SqlWebhookDeliveryRepository(db_session_factory)
        audit_repo = SqlDeploymentAuditRepository(db_session_factory)
        eval_repo = SqlEvalRunRepository(db_session_factory)
        definition_repo = SqlAgentDefinitionRepository(db_session_factory)
        deployment_repo = SqlAgentDeploymentRepository(db_session_factory)
        tool_audit_repo = SqlToolAuditRepository(db_session_factory)
        routing_decision_repo = SqlRoutingDecisionRepository(db_session_factory)
        coding_job_repo = SqlCodingJobRepository(db_session_factory)
    else:
        run_repo = InMemoryAgentRunRepository()
        session_repo = InMemoryAgentSessionRepository()
        webhook_repo = InMemoryWebhookDeliveryRepository()
        audit_repo = InMemoryDeploymentAuditRepository()
        eval_repo = InMemoryEvalRunRepository()
        definition_repo = None
        deployment_repo = None
        tool_audit_repo = InMemoryToolAuditRepository()
        routing_decision_repo = InMemoryRoutingDecisionRepository()
        coding_job_repo = InMemoryCodingJobRepository()

    tool_executor.audit_repo = tool_audit_repo

    if db_session_factory is not None:
        from agent_platform.persistence.sql import SqlRuntimeMemoryRepository, SqlSkillRepository
        _runtime_memory_repo = SqlRuntimeMemoryRepository(db_session_factory)
        _skill_repo = SqlSkillRepository(db_session_factory)
    else:
        _runtime_memory_repo = InMemoryRuntimeMemoryRepository()
        _skill_repo = InMemorySkillRepository()

    registry = AgentRegistry(
        Path(settings.registry_root),
        definition_repo=definition_repo,
        deployment_repo=deployment_repo,
        semantic_router=app_semantic_router,
    )
    router = AgentRouter(registry, settings, semantic_router=app_semantic_router)

    runtime_manager = RuntimeManager(
        run_store=run_repo,
        session_store=session_repo,
        policy_engine=app_policy_engine,
        hook_registry=app_hook_registry,
        metrics_collector=app_metrics,
        model_gateway=model_gateway,
        tool_executor=tool_executor,
        knowledge_service=app_knowledge_service,
        langfuse_tracer=langfuse_tracer,
        approval_gate=approval_gate,
        runtime_memory_repo=_runtime_memory_repo,
        skill_repo=_skill_repo,
    )
    eval_runner = EvalRunner(runtime_manager, eval_repo=eval_repo)
    task_pack_generator = TaskPackGenerator()

    requirement_parser = RequirementParser()
    issue_generator = IssueGenerator()
    scaffolder = AgentScaffolder(settings.registry_root)
    architect_agent = ArchitectureDesignAgent()
    test_agent = TestGenerationAgent()
    audit_log = DeploymentAuditLog(repo=audit_repo)
    artifact_store = ArtifactStore()
    artifact_signer = ArtifactSigner()

    # ── SLO 门禁 ──
    from agent_platform.governance.slo import SLOGate
    slo_gate = SLOGate(metrics=app_metrics)

    # ── Dead Letter Queue ──
    from agent_platform.webhooks.dead_letter import (
        InMemoryDeadLetterQueue,
        WebhookRetryService,
    )
    db_url = get_settings().database_url
    if db_url and db_session_factory:
        dlq = SqlDeadLetterQueue(session_factory=db_session_factory)
    else:
        dlq = InMemoryDeadLetterQueue()

    webhook_retry_service = WebhookRetryService(dlq=dlq)

    key_store = None
    if db_session_factory is not None:
        from agent_platform.persistence.sql import SqlApiKeyStore
        key_store = SqlApiKeyStore(db_session_factory)

    ws_manager = AgentWebSocketManager(
        router, runtime_manager,
        api_key=settings.api_key,
        key_store=key_store,
    )

    # ── 服务间鉴权 ──
    service_auth: ServiceAuthProvider | None = None
    if settings.service_jwt_secret:
        service_auth = ServiceAuthProvider(
            jwt_secret=settings.service_jwt_secret,
            shared_secrets=_parse_service_secrets(settings.service_shared_secrets),
        )
        logger.info("Service-to-service auth enabled (JWT + Shared Secret)")

    app = FastAPI(title="Agent Platform", version="0.2.0", lifespan=_app_lifespan)
    app.state._settings = settings
    app.state._closeables = []
    app.state.runtime_memory_repo = _runtime_memory_repo
    app.state.skill_repo = _skill_repo
    if db_engine:
        app.state._closeables.append(("SQLAlchemy engine", db_engine))
    if langfuse_tracer.enabled:
        app.state._closeables.append(("LangfuseTracer", langfuse_tracer))
    app.state.policy_engine = app_policy_engine
    app.state.knowledge_service = app_knowledge_service
    app.state._knowledge_scheduler = _knowledge_scheduler
    app.state.langfuse = langfuse_tracer
    app.state.hook_registry = app_hook_registry
    app.state.semantic_router = app_semantic_router
    app.state.registry = registry
    app.state.metrics = app_metrics
    app.state.webhook_repo = webhook_repo
    app.state.audit_repo = audit_repo
    app.state.eval_repo = eval_repo
    app.state.db_session_factory = db_session_factory
    app.state.approval_gate = approval_gate

    # ── 多租户配额管理 ──
    from agent_platform.api.tenant_quota import TenantQuotaManager
    _redis_client = None
    if settings.redis_url:
        try:
            import redis.asyncio as aioredis
            _redis_client = aioredis.from_url(
                settings.redis_url, decode_responses=False,
            )
            logger.info("Redis 已连接: 限流 + 配额使用分布式后端")
        except Exception:
            logger.warning("Redis 连接失败，回退到内存后端", exc_info=True)

    _quota_backend = None
    if _redis_client is not None:
        from agent_platform.api.tenant_quota import RedisQuotaBackend
        _quota_backend = RedisQuotaBackend(_redis_client)
    quota_manager = TenantQuotaManager(backend=_quota_backend)
    app.state.quota_manager = quota_manager
    if _redis_client is not None:
        app.state._closeables.append(("Redis (限流/配额)", _redis_client))

    app.state.admin_deps = AdminDeps(
        registry=registry,
        runtime_manager=runtime_manager,
        audit_log=audit_log,
        tool_registry=tool_registry,
        metrics=app_metrics,
        key_store=key_store,
        eval_repo=eval_repo,
        tool_audit_repo=tool_audit_repo,
        quota_manager=quota_manager,
        eval_runner=eval_runner,
        slo_gate=slo_gate,
        webhook_retry_service=webhook_retry_service,
        routing_decision_repo=routing_decision_repo,
        definition_repo=definition_repo,
    )
    app.include_router(
        admin_router,
        dependencies=[_ROLE_ADMIN],
    )

    # ── MCP SSE 传输层 ──
    from agent_platform.mcp.server import AgentPlatformMCPServer
    from agent_platform.mcp.sse_transport import MCPSSETransport
    mcp_server = AgentPlatformMCPServer(
        registry=registry,
        tool_registry=tool_registry,
        eval_runner=eval_runner,
        audit_log=audit_log,
    )
    mcp_sse = MCPSSETransport(mcp_server)
    app.include_router(mcp_sse.router)
    app.state.mcp_sse = mcp_sse

    from agent_platform.admin.routes import router as admin_ui_router
    app.include_router(admin_ui_router, dependencies=[_ROLE_ADMIN])

    app.state.key_store = key_store
    app.state._key_store = key_store
    app.state.service_auth = service_auth
    app.state.routing_decision_repo = routing_decision_repo
    app.state.artifact_store = artifact_store
    app.state.artifact_signer = artifact_signer
    app.state.dlq = dlq
    app.state.webhook_retry_service = webhook_retry_service

    _rl_backend = None
    if _redis_client is not None:
        from agent_platform.api.rate_limiter import RedisRateLimiterBackend
        _rl_backend = RedisRateLimiterBackend(_redis_client)
    app.add_middleware(
        RateLimiterMiddleware, requests_per_minute=120, burst=20,
        backend=_rl_backend,
    )
    app.add_middleware(
        AuthMiddleware, api_key=settings.api_key, key_store=key_store,
        service_auth=service_auth, env=settings.env,
    )
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(
        InputSanitizationMiddleware,
        max_body_bytes=settings.max_request_body_bytes,
    )

    cors_raw = settings.cors_allowed_origins
    if settings.env == "production" and (not cors_raw or cors_raw == "*"):
        logger.warning(
            "生产环境 CORS 默认拒绝所有跨域请求 — 通过 CORS_ALLOWED_ORIGINS 显式配置"
        )
        cors_origins: list[str] = []
    elif cors_raw and cors_raw != "*":
        cors_origins = [o.strip() for o in cors_raw.split(",") if o.strip()]
    else:
        cors_origins = ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", "unknown")
        logger.exception("Unhandled exception [request_id=%s]", request_id)
        detail = str(exc) if settings.env != "production" else "internal server error"
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": detail,
                    "request_id": request_id,
                }
            },
        )

    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(request: Request, exc: RequestValidationError):
        request_id = getattr(request.state, "request_id", "unknown")
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "请求参数校验失败",
                    "details": exc.errors(),
                    "request_id": request_id,
                }
            },
        )

    devflow: DevFlowOrchestrator | None = None
    devflow_state_sync: Any = None
    execution_log_repo: Any = None
    if (
        settings.plane_base_url
        and settings.plane_api_key
        and settings.gitlab_base_url
        and settings.gitlab_token
        and settings.gitlab_project_id
    ):
        plane_adapter = PlaneAdapter(
            base_url=settings.plane_base_url,
            api_key=settings.plane_api_key,
            workspace_slug=settings.plane_workspace_slug,
        )
        gitlab_adapter = GitLabAdapter(
            base_url=settings.gitlab_base_url,
            token=settings.gitlab_token,
        )
        app.state._closeables.append(("PlaneAdapter", plane_adapter))
        app.state._closeables.append(("GitLabAdapter", gitlab_adapter))
        workspace_base = (
            Path(settings.devflow_workspace_base_dir)
            if settings.devflow_workspace_base_dir
            else None
        )
        # DevFlow 状态同步服务
        from agent_platform.devflow.state_sync import DevFlowStateSync
        devflow_state_sync = DevFlowStateSync(plane_adapter=plane_adapter)

        from agent_platform.devflow.ownership import AgentOwnershipResolver
        ownership_resolver = AgentOwnershipResolver.from_file(
            settings.devflow_agent_ownership_config
        )

        # 执行日志仓库
        from agent_platform.devflow.runner.execution_log import (
            FileExecutionLogRepository,
            InMemoryExecutionLogRepository,
        )
        if db_session_factory is not None:
            from agent_platform.persistence.sql import SqlExecutionLogRepository
            execution_log_repo = SqlExecutionLogRepository(db_session_factory)
        else:
            log_base = (
                Path(settings.devflow_workspace_base_dir) / "_logs"
                if settings.devflow_workspace_base_dir
                else None
            )
            execution_log_repo = (
                FileExecutionLogRepository(log_base) if log_base
                else InMemoryExecutionLogRepository()
            )

        workspace_manager = WorkspaceManager(base_dir=workspace_base)
        adapter = create_adapter(
            settings.devflow_runner_adapter,
            codex_profile=settings.devflow_codex_profile,
            sandbox_mode=settings.devflow_sandbox_mode,
            docker_image=settings.devflow_docker_image,
        )
        async def _review_fork_trigger_cb(job, task) -> None:
            rf = getattr(app.state, "review_fork", None)
            if rf:
                from agent_platform.evolution.review_fork import ReviewForkEvent, ReviewForkEventType
                event = ReviewForkEvent(
                    event_type=ReviewForkEventType.DEVFLOW_JOB_COMPLETED,
                    agent_id=task.agent_id or "unknown",
                    tenant_id="default",
                    evidence_summary=f"DevFlow 任务完成: 状态={job.state}",
                    payload={
                        "job_id": job.job_id,
                        "state": job.state,
                        "result": job.result.model_dump(mode="json") if hasattr(job.result, "model_dump") else str(job.result),
                    }
                )
                await rf.trigger(event)

        coding_runner = CodingAgentRunner(
            adapter=adapter,
            workspace_manager=workspace_manager,
            gitlab=gitlab_adapter,
            plane=plane_adapter,
            gitlab_project_id=settings.gitlab_project_id,
            repo_url=settings.devflow_repo_url,
            testing_state_id=settings.plane_testing_state_id,
            ai_developing_state_id=settings.plane_ai_developing_state_id,
            gitlab_base_url=settings.gitlab_base_url,
            job_repo=coding_job_repo,
            log_repo=execution_log_repo,
            review_fork_trigger=_review_fork_trigger_cb,
        )
        from agent_platform.devflow.runner.job_queue import AsyncJobQueue

        if settings.devflow_job_queue_backend == "redis" and settings.redis_url:
            from agent_platform.devflow.runner.redis_queue import RedisJobQueue
            job_queue = RedisJobQueue(
                redis_url=settings.redis_url, max_concurrent=3,
            )
            app.state._closeables.append(("RedisJobQueue", job_queue))
        else:
            job_queue = AsyncJobQueue(max_concurrent=3)
            app.state._closeables.append(("AsyncJobQueue", job_queue))
        app.state.runner_adapter = adapter

        devflow = DevFlowOrchestrator(
            plane=plane_adapter,
            gitlab=gitlab_adapter,
            gitlab_project_id=settings.gitlab_project_id,
            webhook_repo=webhook_repo,
            coding_runner=coding_runner,
                job_queue=job_queue,
                ai_developing_state_id=settings.plane_ai_developing_state_id,
                default_branch=settings.devflow_default_branch,
                state_sync=devflow_state_sync,
                ownership_resolver=ownership_resolver,
            )

        app.state.execution_log_repo = execution_log_repo
        app.state.coding_runner = coding_runner

        logger.info(
            "DevFlow enabled: adapter=%s, project=%s",
            settings.devflow_runner_adapter,
            settings.gitlab_project_id,
        )

    # GitLab reverse sync handler
    gitlab_event_handler: GitLabEventHandler | None = None
    if devflow is not None:

        async def _on_pipeline_failed(
            *, project_id: str, work_item_id: str, ref: str
        ) -> None:
            """Pipeline 失败后重新触发 coding runner。"""
            logger.info(
                "Pipeline 失败回调: 重新触发 runner, project=%s item=%s ref=%s",
                project_id, work_item_id, ref,
            )
            try:
                work_item = await plane_adapter.get_work_item(
                    project_id, work_item_id
                )
                payload = {
                    "event": "work_item.updated",
                    "data": work_item,
                }
                await devflow.handle_webhook_event("work_item.updated", payload)
            except Exception:
                logger.warning(
                    "Pipeline 失败重跑触发异常: item=%s", work_item_id, exc_info=True
                )

        gitlab_event_handler = GitLabEventHandler(
            plane=plane_adapter,
            webhook_repo=webhook_repo,
            testing_state_id=settings.plane_testing_state_id,
            human_review_state_id=settings.plane_human_review_state_id,
            staging_state_id=settings.plane_staging_state_id,
            done_state_id=settings.plane_done_state_id,
            ai_developing_state_id=settings.plane_ai_developing_state_id,
            on_pipeline_failed=_on_pipeline_failed,
        )

    app.state.devflow_orchestrator = devflow
    app.state.gitlab_event_handler = gitlab_event_handler
    app.state.devflow_enabled = devflow is not None
    if devflow_state_sync is not None:
        app.state.admin_deps.state_sync = devflow_state_sync
    if devflow is not None:
        app.state.admin_deps.execution_log_repo = execution_log_repo
        app.state.admin_deps.coding_job_repo = coding_job_repo
        app.state.admin_deps.coding_runner = coding_runner

        # DevFlow Reconciler
        from agent_platform.devflow.reconcile import DevFlowReconciler
        reconciler = DevFlowReconciler(
            state_sync=devflow_state_sync,
            plane=plane_adapter,
            gitlab=gitlab_adapter,
            gitlab_project_id=settings.gitlab_project_id,
        )
        app.state.admin_deps.reconciler = reconciler

    # Feedback Intelligence Service
    if db_session_factory is not None and devflow is not None:
        from agent_platform.feedback.collector import FeedbackCollector
        from agent_platform.feedback.gate import ProposalGate
        from agent_platform.feedback.miner import FeedbackMiner
        from agent_platform.feedback.publisher import PlanePublisher
        from agent_platform.feedback.service import FeedbackIntelligenceService

        feedback_collector = FeedbackCollector(session_factory=db_session_factory)
        feedback_miner = FeedbackMiner()
        feedback_gate = ProposalGate()
        feedback_publisher = PlanePublisher(
            plane=plane_adapter,
            project_id=settings.plane_project_id or "",
        )
        feedback_service = FeedbackIntelligenceService(
            collector=feedback_collector,
            miner=feedback_miner,
            gate=feedback_gate,
            publisher=feedback_publisher,
        )
        app.state.admin_deps.feedback_service = feedback_service

    # ── Evolution Engine ──
    from agent_platform.evolution.engine import EvolutionEngine
    from agent_platform.evolution.repository import InMemoryProposalRepository

    if db_session_factory is not None:
        from agent_platform.persistence.sql import SqlProposalRepository
        _evo_repo = SqlProposalRepository(db_session_factory)
    else:
        _evo_repo = InMemoryProposalRepository()
    if devflow is not None:
        evolution_engine = EvolutionEngine(
            repo=_evo_repo,
            plane_adapter=plane_adapter,
            plane_project_id=settings.plane_project_id,
            ai_developing_state_id=settings.plane_ai_developing_state_id,
        )
    else:
        evolution_engine = EvolutionEngine(repo=_evo_repo)
    app.state.evolution_engine = evolution_engine

    # ── Memory / Skills / Candidate ──
    from agent_platform.evolution.memory_repository import (
        InMemoryEvolutionMemoryRepository,
    )
    from agent_platform.evolution.repository import InMemoryCandidateRepository

    # ── Background Review Fork ──
    from agent_platform.evolution.review_fork import (
        InMemoryReviewForkAuditRepository,
        BackgroundReviewFork,
        ReviewForkEvent,
        ReviewForkEventType,
    )

    if db_session_factory is not None:
        from agent_platform.persistence.sql import (
            SqlEvolutionMemoryRepository,
            SqlCandidateRepository,
            SqlReviewForkAuditRepository,
        )
        _memory_repo = SqlEvolutionMemoryRepository(db_session_factory)
        _candidate_repo = SqlCandidateRepository(db_session_factory)
        _review_audit_repo = SqlReviewForkAuditRepository(db_session_factory)
    else:
        _memory_repo = InMemoryEvolutionMemoryRepository()
        _candidate_repo = InMemoryCandidateRepository()
        _review_audit_repo = InMemoryReviewForkAuditRepository()

    app.state.memory_repo = _memory_repo
    app.state.candidate_repo = _candidate_repo

    review_fork = BackgroundReviewFork(
        model_gateway=model_gateway,
        candidate_repo=_candidate_repo,
        audit_repo=_review_audit_repo,
        proposal_repo=_evo_repo,
    )
    app.state.review_fork_audit_repo = _review_audit_repo
    app.state.review_fork = review_fork

    # 订阅 post_run 钩子
    async def on_post_run(ctx: HookContext) -> None:
        data = ctx.data or {}
        resp = data.get("response")
        run_id = data.get("run_id", "unknown_run")
        if not resp:
            return

        agent_id = getattr(resp, "agent_id", None)
        tenant_id = "default"
        if hasattr(resp, "response") and resp.response and hasattr(resp.response, "metadata") and resp.response.metadata:
            tenant_id = resp.response.metadata.get("tenant_id") or "default"
            agent_id = agent_id or resp.response.metadata.get("agent_id")

        if not agent_id and hasattr(resp, "response") and resp.response and hasattr(resp.response, "agent_id"):
            agent_id = resp.response.agent_id

        if not agent_id:
            agent_id = "unknown"

        summary = ""
        if hasattr(resp, "response") and resp.response and hasattr(resp.response, "output") and resp.response.output:
            summary = getattr(resp.response.output.text, "display", "") or ""

        event = ReviewForkEvent(
            event_type=ReviewForkEventType.AGENT_RUN_COMPLETED,
            agent_id=agent_id,
            tenant_id=tenant_id,
            evidence_summary=summary[:200],
            payload={
                "run_id": run_id,
                "response": resp.model_dump(mode="json") if hasattr(resp, "model_dump") else str(resp),
            }
        )
        await review_fork.trigger(event)

    app_hook_registry.register("post_run", on_post_run)

    # DevFlow 后台调度器
    from agent_platform.devflow.scheduler import DevFlowScheduler
    scheduler = DevFlowScheduler(
        reconciler=getattr(app.state.admin_deps, "reconciler", None),
        feedback_service=getattr(app.state.admin_deps, "feedback_service", None),
        project_id=settings.plane_project_id,
        reconcile_interval=300,
        feedback_interval=3600,
    )
    app.state._devflow_scheduler = scheduler

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def health_ready() -> JSONResponse:
        checks: dict[str, str] = {}
        overall = True

        if db_session_factory:
            try:
                from sqlalchemy import text as sa_text

                async with db_session_factory() as session:
                    await session.execute(sa_text("SELECT 1"))
                checks["database"] = "ok"
            except Exception:
                checks["database"] = "error"
                overall = False
        else:
            checks["database"] = "in_memory"

        checks["devflow"] = "enabled" if devflow is not None else "disabled"
        checks["runner_adapter"] = "configured"
        checks["auth"] = "enabled" if settings.api_key else "open"

        runner_adapter = getattr(app.state, "runner_adapter", None)
        if runner_adapter is not None:
            try:
                healthy = await runner_adapter.health_check()
                checks["runner_health"] = "ok" if healthy else "unhealthy"
                if not healthy:
                    overall = False
            except Exception:
                checks["runner_health"] = "error"

        job_queue_state = getattr(app.state, "_closeables", [])
        for name, resource in job_queue_state:
            if name in ("AsyncJobQueue", "RedisJobQueue") and hasattr(resource, "get_stats"):
                checks["job_queue"] = resource.get_stats()

        lf = getattr(app.state, "langfuse", None)
        if lf is not None:
            checks["langfuse"] = "enabled" if lf.enabled else "disabled"

        if settings.weaviate_url:
            ks = getattr(app.state, "knowledge_service", None)
            if ks:
                wb = ks._backends.get("weaviate")
                if wb and hasattr(wb, "health_check"):
                    try:
                        wv_ok = await wb.health_check()
                        checks["weaviate"] = "ok" if wv_ok else "unhealthy"
                    except Exception:
                        checks["weaviate"] = "error"
                else:
                    checks["weaviate"] = "not_registered"
            else:
                checks["weaviate"] = "configured"

        if settings.redis_url:
            _redis_client = getattr(app.state, "_redis_client", None)
            if _redis_client is not None:
                try:
                    await _redis_client.ping()
                    checks["redis"] = "ok"
                except Exception:
                    checks["redis"] = "unhealthy"
            else:
                checks["redis"] = "configured"

        return JSONResponse(
            status_code=200 if overall else 503,
            content={
                "status": "ready" if overall else "degraded",
                "checks": checks,
            },
        )

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(
            content=app.state.metrics.format_prometheus(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.get("/api/v1/agents")
    async def list_agents(
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> list[dict[str, str]]:
        return [
            {
                "agent_id": spec.agent_id,
                "version": spec.version,
                "name": spec.manifest.metadata.name,
                "runtime_backend": spec.manifest.runtime.backend,
            }
            for spec in await registry.list_agents()
        ]

    @app.post("/api/v1/agent-packages/register")
    async def register_agent(
        payload: RegisterAgentRequest,
        _auth: AuthIdentity = _SCOPE_REGISTER,
    ) -> dict[str, str]:
        manifest = Path(payload.manifest_path).resolve()
        allowed_root = settings.registry_root.resolve()
        if not manifest.is_relative_to(allowed_root):
            raise HTTPException(
                status_code=400,
                detail="manifest_path 必须位于 registry_root 目录内",
            )
        spec = registry.loader.load_file(manifest)
        await registry.register(spec)
        return {"agent_id": spec.agent_id, "version": spec.version, "status": "registered"}

    @app.get("/api/v1/agents/{agent_id}/health")
    async def agent_health(
        agent_id: str,
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> dict:
        """Per-agent health check: backend, recent runs, sessions."""
        try:
            spec = await registry.get(agent_id)
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        backend_name = spec.manifest.runtime.backend
        backend_ok = backend_name in runtime_manager._backends
        recent_runs = await runtime_manager.list_runs(
            agent_id=agent_id, limit=20,
        )
        sessions = await runtime_manager.list_sessions(agent_id=agent_id)

        total = len(recent_runs)
        succeeded = sum(
            1 for r in recent_runs if r.status.value == "succeeded"
        )
        success_rate = succeeded / total if total else 1.0

        health = "healthy"
        if not backend_ok:
            health = "unhealthy"
        elif total > 0 and success_rate < 0.5:
            health = "degraded"

        return {
            "agent_id": agent_id,
            "health": health,
            "backend": backend_name,
            "backend_available": backend_ok,
            "recent_runs": total,
            "success_rate": round(success_rate, 3),
            "active_sessions": len(sessions),
        }

    @app.get("/api/v1/agent-runs")
    async def list_agent_runs(
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> list[dict]:
        tenant_id = None if _auth.role == "platform_admin" else _auth.tenant_id
        runs = await runtime_manager.list_runs(tenant_id=tenant_id)
        return [run.model_dump(mode="json") for run in runs]

    @app.get("/api/v1/agent-deployments")
    async def list_agent_deployments(
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> list[dict]:
        deployments = await registry.list_deployments()
        if _auth.role != "platform_admin" and _auth.tenant_id:
            deployments = [
                d for d in deployments
                if d.tenant_id is None or d.tenant_id == _auth.tenant_id
            ]
        return [deployment.model_dump(mode="json") for deployment in deployments]

    @app.get("/api/v1/agent-deployments/{deployment_id}/metrics")
    async def deployment_metrics(
        deployment_id: str,
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> dict:
        """Canary deployment metrics: error rate, latency, run count."""
        deployment = await registry.get_deployment(deployment_id)
        if deployment is None:
            raise HTTPException(
                status_code=404, detail=f"deployment not found: {deployment_id}",
            )

        runs = await runtime_manager.list_runs(
            agent_id=deployment.agent_id, limit=100,
        )
        dep_runs = [
            r for r in runs
            if r.agent_version == deployment.version
        ]
        total = len(dep_runs)
        failed = sum(
            1 for r in dep_runs if r.status.value == "failed"
        )
        error_rate = failed / total if total else 0.0
        latencies = [r.latency_ms for r in dep_runs if r.latency_ms]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
        p99_latency = (
            sorted(latencies)[int(len(latencies) * 0.99)]
            if latencies else 0.0
        )

        return {
            "deployment_id": deployment_id,
            "agent_id": deployment.agent_id,
            "version": deployment.version,
            "status": deployment.status.value,
            "traffic_percent": deployment.traffic_percent,
            "total_runs": total,
            "failed_runs": failed,
            "error_rate": round(error_rate, 4),
            "avg_latency_ms": round(avg_latency, 1),
            "p99_latency_ms": round(p99_latency, 1),
            "needs_rollback": error_rate > 0.1 and total >= 10,
        }

    @app.get("/api/v1/sessions")
    async def list_sessions(
        agent_id: str | None = None,
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> list[dict]:
        tenant_id = None if _auth.role == "platform_admin" else _auth.tenant_id
        sessions = await runtime_manager.list_sessions(
            agent_id=agent_id, tenant_id=tenant_id,
        )
        return [s.model_dump(mode="json") for s in sessions]

    @app.get("/api/v1/sessions/{session_id}")
    async def get_session(
        session_id: str,
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> dict:
        session = await runtime_manager.load_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
        if _auth.role != "platform_admin" and session.tenant_id != _auth.tenant_id:
            raise HTTPException(status_code=403, detail="access denied")
        return session.model_dump(mode="json")

    @app.get("/api/v1/agent-packages/{agent_id}/versions/{version}/validate")
    async def validate_agent_deploy(
        agent_id: str,
        version: str,
        _auth: AuthIdentity = _SCOPE_DEPLOY,
    ) -> dict:
        """Pre-deploy validation: checks manifest, backend, and tools."""
        checks: list[dict] = []
        try:
            spec = await registry.get(agent_id)
        except AgentNotFoundError:
            return {
                "valid": False,
                "checks": [{"name": "agent_exists", "passed": False,
                             "message": f"agent not found: {agent_id}"}],
            }
        checks.append({"name": "agent_exists", "passed": True})

        if spec.version != version:
            checks.append({
                "name": "version_match", "passed": False,
                "message": f"loaded {spec.version}, requested {version}",
            })
        else:
            checks.append({"name": "version_match", "passed": True})

        backend_name = spec.manifest.runtime.backend
        backend_ok = backend_name in runtime_manager._backends
        checks.append({
            "name": "backend_available", "passed": backend_ok,
            "message": f"backend={backend_name}" + (
                "" if backend_ok else " not registered"
            ),
        })

        allowed_tools = spec.manifest.tools.allow or []
        missing_tools = []
        for tool_name in allowed_tools:
            try:
                tool_registry.get(tool_name)
            except LookupError:
                missing_tools.append(tool_name)
        checks.append({
            "name": "tools_registered", "passed": len(missing_tools) == 0,
            "message": (
                f"missing: {missing_tools}" if missing_tools
                else f"{len(allowed_tools)} tools verified"
            ),
        })

        eval_suites = spec.manifest.evals.suites
        suites_ok = len(eval_suites) > 0
        checks.append({
            "name": "eval_suites", "passed": suites_ok,
            "message": (
                f"{len(eval_suites)} suites configured"
                if suites_ok else "no eval suites"
            ),
        })

        all_passed = all(c["passed"] for c in checks)
        return {"valid": all_passed, "checks": checks}

    @app.post("/api/v1/agent-packages/{agent_id}/versions/{version}/deploy")
    async def deploy_agent(
        agent_id: str,
        version: str,
        payload: DeployAgentRequest,
        _auth: AuthIdentity = _SCOPE_DEPLOY,
    ) -> dict:
        try:
            spec = await registry.get(agent_id)
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if spec.version != version:
            raise HTTPException(
                status_code=404,
                detail=f"agent version not found: {agent_id}@{version}",
            )

        if payload.channel in {"staging", "prod"} and payload.eval_passed is False:
            raise HTTPException(status_code=409, detail="eval gate must pass before deployment")

        eval_report: EvalReport | None = None
        if payload.channel in {"staging", "prod"}:
            report = await eval_runner.run_agent(spec)
            if not report.gate_passed:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"eval gate failed: pass_rate={report.pass_rate:.1%} "
                        f"required={report.required_pass_rate:.1%}"
                    ),
                )
            eval_report = report

        # SLO 门禁：staging/prod 部署前检查 SLO 是否满足
        slo_results = None
        if payload.channel in {"staging", "prod"}:
            all_passed, slo_checks = slo_gate.check_all(agent_id)
            slo_results = [r.model_dump(mode="json") for r in slo_checks]
            if not all_passed:
                violations = [r.message for r in slo_checks if not r.passed]
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "SLO gate failed",
                        "violations": violations,
                        "results": slo_results,
                    },
                )

        status = _deployment_status(payload.channel, payload.traffic_percent)
        previous_deployment = await registry.resolve_deployment(
            agent_id=agent_id,
            channel=payload.channel,
            tenant_id=payload.tenant_id,
        )
        deployment = await registry.deploy(
            agent_id=agent_id,
            version=version,
            channel=payload.channel,
            status=status,
            tenant_id=payload.tenant_id,
            traffic_percent=payload.traffic_percent,
        )

        artifact_meta = artifact_store.create_artifact(
            agent_id=agent_id,
            version=version,
            package_path=spec.package_path,
        )

        # 产物签名：计算 manifest SHA-256 并记录
        manifest_sha256 = artifact_signer.sign_manifest(spec.manifest)

        # 产物签名验证：部署前验证 manifest 完整性
        if not artifact_signer.verify_manifest(spec.manifest, manifest_sha256):
            raise HTTPException(
                status_code=409,
                detail="产物签名验证失败：manifest SHA-256 不一致",
            )

        eval_report_id = (
            f"{eval_report.agent_id}@{eval_report.agent_version}"
            if eval_report else None
        )

        await audit_log.record_deploy(
            deployment,
            previous_version=previous_deployment.version if previous_deployment else None,
            artifact_id=artifact_meta.artifact_id,
            eval_report_id=eval_report_id,
            manifest_sha256=manifest_sha256,
        )
        result = deployment.model_dump(mode="json")
        result["artifact_id"] = artifact_meta.artifact_id
        result["manifest_sha256"] = manifest_sha256
        if slo_results:
            result["slo_checks"] = slo_results
        if eval_report:
            result["eval"] = eval_report.model_dump(mode="json")
        return result

    @app.post("/api/v1/evals/run", response_model=EvalReport)
    async def run_eval(
        payload: RunEvalRequest,
        _auth: AuthIdentity = _SCOPE_EVAL,
    ) -> EvalReport:
        try:
            spec = await registry.get(payload.agent_id)
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return await eval_runner.run_agent(spec)

    @app.post("/api/v1/evals/ci-callback")
    async def eval_ci_callback(
        agent_id: str,
        project_id: str | None = None,
        mr_iid: int | None = None,
        work_item_id: str | None = None,
        _auth: AuthIdentity = _SCOPE_EVAL,
    ) -> dict:
        try:
            spec = await registry.get(agent_id)
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        report = await eval_runner.run_agent(spec)
        result: dict = {"agent_id": agent_id, "gate_passed": report.gate_passed}

        if devflow and project_id and mr_iid:
            from agent_platform.evals.feedback import EvalFeedback
            gitlab_adapter = devflow.gitlab
            feedback = EvalFeedback(gitlab=gitlab_adapter, eval_repo=eval_repo)
            await feedback.post_to_gitlab(report, project_id, mr_iid)
            result["gitlab_comment_posted"] = True

        return result

    @app.post("/api/v1/devflow/task-packs")
    async def create_task_pack(
        payload: CreateTaskPackRequest,
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ):
        return task_pack_generator.from_requirement(**payload.model_dump())

    @app.post("/api/v1/devflow/parse-requirement")
    async def parse_requirement(
        payload: ParseRequirementRequest,
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ):
        return requirement_parser.parse(
            payload.text, payload.context or {},
        ).model_dump()

    @app.post("/api/v1/devflow/generate-issues")
    async def generate_issues(
        payload: GenerateIssuesRequest,
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ):
        parsed = requirement_parser.parse(
            payload.text, payload.project_context or {},
        )
        issues = issue_generator.generate(
            parsed, payload.project_context or {},
        )
        return [i.model_dump() for i in issues]

    @app.post("/api/v1/devflow/scaffold-agent")
    async def scaffold_agent(
        payload: ScaffoldAgentRequest,
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ):
        path = scaffolder.create(**payload.model_dump())
        return {"agent_id": payload.agent_id, "path": str(path)}

    @app.post("/api/v1/devflow/design-analysis")
    async def design_analysis(
        payload: DesignAnalysisRequest,
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ):
        brief = architect_agent.analyze(
            payload.requirement_text, payload.context,
        )
        return brief.model_dump()

    @app.post("/api/v1/devflow/test-plan")
    async def test_plan(
        payload: TestPlanRequest,
        _auth: AuthIdentity = _SCOPE_EVAL,
    ):
        plan = test_agent.generate_plan(
            payload.agent_id,
            payload.change_type,
            payload.changed_files,
        )
        return plan.model_dump()

    # --- DevFlow job observability endpoints ---

    @app.get("/api/v1/devflow/jobs")
    async def list_devflow_jobs(
        status: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> list[dict]:
        return await coding_job_repo.list_jobs(status=status, limit=limit)

    @app.get("/api/v1/devflow/jobs/{job_id}")
    async def get_devflow_job(
        job_id: str,
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> dict:
        job = await coding_job_repo.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
        return job

    @app.get("/api/v1/devflow/jobs/{job_id}/logs")
    async def get_devflow_job_logs(
        job_id: str,
        stream: str | None = Query(default=None, description="stdout 或 stderr"),
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> list[dict]:
        if execution_log_repo is None:
            raise HTTPException(status_code=503, detail="execution log repository not configured")
        from agent_platform.devflow.runner.execution_log import LogStream
        stream_filter = None
        if stream:
            try:
                stream_filter = LogStream(stream)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"invalid stream: {stream!r}")
        entries = await execution_log_repo.get_logs(job_id, stream=stream_filter)
        return [e.model_dump(mode="json") for e in entries]

    @app.get("/api/v1/devflow/status")
    async def devflow_status(
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> dict[str, Any]:
        jobs = await coding_job_repo.list_jobs(limit=1000)
        by_state: dict[str, int] = {}
        for j in jobs:
            st = j.get("state", "unknown")
            by_state[st] = by_state.get(st, 0) + 1
        result: dict[str, Any] = {
            "enabled": devflow is not None,
            "runner_adapter": settings.devflow_runner_adapter,
            "gitlab_project_id": settings.gitlab_project_id,
            "total_jobs": len(jobs),
            "jobs_by_state": by_state,
        }

        for name, resource in getattr(app.state, "_closeables", []):
            if name in ("AsyncJobQueue", "RedisJobQueue") and hasattr(resource, "get_stats"):
                result["job_queue"] = resource.get_stats()

        return result

    @app.post("/api/v1/deployments/rollback")
    async def rollback_deployment(
        payload: RollbackRequest,
        _auth: AuthIdentity = _SCOPE_ROLLBACK,
    ):
        rollback_info = await audit_log.get_rollback_version(
            payload.agent_id, payload.channel,
        )
        if not rollback_info:
            raise HTTPException(
                status_code=404,
                detail=f"no rollback target for {payload.agent_id}:{payload.channel}",
            )
        target_version, _rollback_artifact_id = rollback_info
        current_deployment = await registry.resolve_deployment(
            agent_id=payload.agent_id,
            channel=payload.channel,
        )
        current_version = current_deployment.version if current_deployment else None

        deployment = await registry.deploy(
            agent_id=payload.agent_id,
            version=target_version,
            channel=payload.channel,
            status=AgentDeploymentStatus.ROLLED_BACK,
        )
        await audit_log.record_rollback(
            payload.agent_id,
            payload.channel,
            current_version or "unknown",
            target_version,
            payload.actor,
        )
        return deployment.model_dump(mode="json")

    @app.get("/api/v1/deployments/audit")
    async def deployment_audit(
        agent_id: str | None = None,
        channel: str | None = None,
        limit: int = 50,
        _auth: AuthIdentity = _SCOPE_READ,
    ):
        events = await audit_log.list_events(agent_id, channel, limit)
        return [e.model_dump(mode="json") for e in events]

    @app.get("/api/v1/artifacts")
    async def list_artifacts(
        agent_id: str | None = None,
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> list[dict]:
        return [a.model_dump(mode="json") for a in artifact_store.list_artifacts(agent_id)]

    @app.get("/api/v1/artifacts/{artifact_id}")
    async def get_artifact(
        artifact_id: str,
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> dict:
        meta = artifact_store.get_metadata(artifact_id)
        if not meta:
            raise HTTPException(status_code=404, detail=f"artifact not found: {artifact_id}")
        return meta.model_dump(mode="json")

    @app.get("/api/v1/artifacts/{artifact_id}/download")
    async def download_artifact(
        artifact_id: str,
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> Response:
        data = artifact_store.get_data(artifact_id)
        if not data:
            raise HTTPException(status_code=404, detail=f"artifact not found: {artifact_id}")
        filename = f"{artifact_id.replace('@', '_')}.tar.gz"
        return Response(
            content=data,
            media_type="application/gzip",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    @app.websocket("/ws/agent/chat")
    async def websocket_chat(websocket: WebSocket, session_id: str | None = None):
        await ws_manager.handle(websocket, session_id)

    @app.post("/api/v1/agent/chat", response_model=AgentResponse)
    async def chat(
        request: AgentRequest,
        raw_request: Request,
        _auth: AuthIdentity = _SCOPE_CHAT,
    ) -> AgentResponse:
        if not request.request_id:
            req_id = getattr(raw_request.state, "request_id", None)
            request.request_id = req_id or f"req_{uuid4().hex}"
            raw_request.state.request_id = request.request_id
        else:
            raw_request.state.request_id = request.request_id
        header_tenant = getattr(raw_request.state, "tenant_id", None)
        if header_tenant and not request.context.tenant.tenant_id:
            request.context.tenant.tenant_id = header_tenant

        # 租户配额检查
        _tenant_id = request.context.tenant.tenant_id or header_tenant
        if _tenant_id and quota_manager:
            try:
                quota_manager.check_request_quota(_tenant_id)
            except Exception as quota_exc:
                return JSONResponse(
                    status_code=429,
                    content=_error_response(
                        request,
                        code="QUOTA_EXCEEDED",
                        message=str(quota_exc),
                        status_code=429,
                    ).model_dump(mode="json"),
                )

        try:
            route = await router.route(request)
        except AgentNotFoundError as exc:
            return JSONResponse(
                status_code=404,
                content=_error_response(
                    request,
                    code="AGENT_NOT_FOUND",
                    message=str(exc),
                    status_code=404,
                ).model_dump(mode="json"),
            )

        # 持久化路由决策
        try:
            await routing_decision_repo.record(
                run_id=request.request_id or "",
                agent_id=route.agent_spec.agent_id,
                reason=route.reason,
                deployment_id=route.deployment_id,
                traffic_bucket=route.traffic_bucket,
            )
        except Exception:
            logger.debug("路由决策记录失败", exc_info=True)

        missing_context = _missing_required_context(
            request,
            route.agent_spec.manifest.context.required,
        )
        if missing_context:
            return JSONResponse(
                status_code=400,
                content=_error_response(
                    request,
                    code="INVALID_REQUEST",
                    message=f"missing required context: {', '.join(missing_context)}",
                    status_code=400,
                    agent_id=route.agent_spec.agent_id,
                    agent_version=route.agent_spec.version,
                ).model_dump(mode="json"),
            )

        runtime_request = RuntimeRequest(
            request=request,
            agent_spec=route.agent_spec,
            route_reason=route.reason,
            deployment_id=route.deployment_id,
            traffic_bucket=route.traffic_bucket,
        )

        if request.options.stream:
            return StreamingResponse(
                stream_agent_response(runtime_manager, runtime_request),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        runtime_response = await runtime_manager.run(runtime_request)

        # 成功后记录配额用量
        if _tenant_id and quota_manager:
            try:
                quota_manager.record_request(_tenant_id)
            except Exception:
                logger.debug("配额用量记录失败", exc_info=True)

        return runtime_response.response

    async def _run_devflow(devflow_inst, event, payload):
        try:
            await devflow_inst.handle_webhook_event(event, payload)
        except Exception:
            logger.exception("DevFlow background task failed for event %s", event)

    @app.post("/api/v1/integrations/plane/webhook")
    async def plane_webhook(
        request: Request,
        background_tasks: BackgroundTasks,
        x_plane_delivery: str | None = Header(default=None),
        x_plane_event: str | None = Header(default=None),
        x_plane_signature: str | None = Header(default=None),
    ) -> dict[str, str | None]:
        raw_body = await request.body()
        if settings.plane_webhook_secret:
            try:
                PlaneWebhookVerifier(settings.plane_webhook_secret).verify(
                    raw_body,
                    x_plane_signature,
                )
            except PlaneWebhookError as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc
        else:
            logger.warning("PLANE_WEBHOOK_SECRET 未配置，跳过签名验证")

        if x_plane_delivery and await webhook_repo.exists(x_plane_delivery):
            return {
                "status": "duplicate",
                "delivery_id": x_plane_delivery,
                "event": x_plane_event,
            }
        if x_plane_delivery:
            await webhook_repo.record(
                delivery_id=x_plane_delivery,
                source="plane",
                event_type=x_plane_event,
                status="accepted",
            )

        result: dict[str, str | None] = {
            "status": "accepted",
            "delivery_id": x_plane_delivery,
            "event": x_plane_event,
        }

        if devflow and x_plane_event:
            payload = json.loads(raw_body) if raw_body else {}
            if x_plane_delivery:
                meta = payload.setdefault("_devflow", {})
                if isinstance(meta, dict):
                    meta["delivery_id"] = x_plane_delivery
            background_tasks.add_task(_run_devflow, devflow, x_plane_event, payload)
            result["devflow_status"] = "queued"

        return result

    # --- GitLab webhook for reverse state sync ---

    @app.post("/api/v1/integrations/gitlab/webhook")
    async def gitlab_webhook(
        request: Request,
        background_tasks: BackgroundTasks,
        x_gitlab_token: str | None = Header(default=None),
        x_gitlab_event: str | None = Header(default=None),
    ) -> dict[str, str | None]:
        if settings.gitlab_webhook_secret:
            try:
                GitLabWebhookVerifier(settings.gitlab_webhook_secret).verify(x_gitlab_token)
            except GitLabWebhookError as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc
        else:
            logger.warning("GITLAB_WEBHOOK_SECRET 未配置，跳过签名验证")

        raw_body = await request.body()
        payload = json.loads(raw_body) if raw_body else {}

        event_type = (
            payload.get("object_kind")
            or (x_gitlab_event or "").replace(" Hook", "").lower().replace(" ", "_")
        )

        if not gitlab_event_handler:
            return {"status": "accepted", "event": event_type, "sync": "disabled"}

        async def _run_gitlab_sync() -> None:
            try:
                await gitlab_event_handler.handle_event(event_type, payload)
            except Exception:
                logger.exception("GitLab reverse sync failed for event %s", event_type)

        background_tasks.add_task(_run_gitlab_sync)
        return {"status": "accepted", "event": event_type, "sync": "queued"}

    # --- Approval gate API endpoints ---

    @app.get("/api/v1/approvals/pending")
    async def list_pending_approvals(
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ) -> list[dict]:
        pending = await approval_gate.list_pending()
        return [req.model_dump(mode="json") for req in pending]

    @app.post("/api/v1/approvals/{request_id}/resolve")
    async def resolve_approval(
        request_id: str,
        body: ResolveApprovalRequest,
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ) -> dict:
        status_str = body.status.lower()
        if status_str not in ("approved", "rejected"):
            raise HTTPException(
                status_code=400,
                detail=f"invalid status: {body.status}; must be 'approved' or 'rejected'",
            )
        approval_status = ApprovalStatus(status_str)
        try:
            await approval_gate.resolve(request_id, approval_status, body.actor)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "request_id": request_id,
            "status": status_str,
            "actor": body.actor,
        }

    # ── Evolution Engine API ──

    @app.post("/api/v1/evolution/analyze")
    async def evolution_analyze(
        payload: AnalyzeEvolutionEventRequest,
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ) -> dict:
        from agent_platform.evolution.models import EvolutionEvent

        if _auth.role != "platform_admin":
            payload.tenant_id = _auth.tenant_id

        event = EvolutionEvent(
            event_type=payload.event_type,
            agent_id=payload.agent_id,
            tenant_id=payload.tenant_id,
            summary=payload.summary,
            details=payload.details,
        )
        proposal = await evolution_engine.process_event(event)
        if proposal is None:
            return {"status": "duplicate", "message": "事件已去重跳过"}
        result = proposal.model_dump(mode="json")
        auto = await evolution_engine.auto_dispatch_if_low_risk(proposal)
        if auto:
            result["auto_dispatch"] = auto
        return result

    @app.get("/api/v1/evolution/proposals")
    async def list_evolution_proposals(
        agent_id: str | None = None,
        status: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> list[dict]:
        from agent_platform.evolution.models import ProposalStatus

        tenant_filter = None if _auth.role == "platform_admin" else _auth.tenant_id
        status_filter = ProposalStatus(status) if status else None
        if agent_id:
            proposals = await _evo_repo.list_by_agent(
                agent_id, status=status_filter, limit=limit,
            )
        else:
            proposals = await _evo_repo.list_all(
                status=status_filter, limit=limit,
            )
        if tenant_filter:
            proposals = [p for p in proposals if p.tenant_id == tenant_filter]
        return [p.model_dump(mode="json") for p in proposals]

    @app.get("/api/v1/evolution/proposals/{proposal_id}")
    async def get_evolution_proposal(
        proposal_id: str,
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> dict:
        proposal = await _evo_repo.get(proposal_id)
        if proposal is None:
            raise HTTPException(
                status_code=404,
                detail=f"proposal not found: {proposal_id}",
            )
        if _auth.role != "platform_admin" and proposal.tenant_id != _auth.tenant_id:
            raise HTTPException(status_code=403, detail="Permission denied")
        return proposal.model_dump(mode="json")

    @app.post("/api/v1/evolution/proposals/{proposal_id}/dispatch")
    async def dispatch_evolution_proposal(
        proposal_id: str,
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ) -> dict:
        proposal = await _evo_repo.get(proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail="Proposal not found")
        if _auth.role != "platform_admin" and proposal.tenant_id != _auth.tenant_id:
            raise HTTPException(status_code=403, detail="Permission denied")

        result = await evolution_engine.dispatch_to_plane(proposal_id)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result

    @app.post("/api/v1/evolution/proposals/{proposal_id}/dismiss")
    async def dismiss_evolution_proposal(
        proposal_id: str,
        body: DismissProposalRequest,
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ) -> dict:
        proposal = await _evo_repo.get(proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail="Proposal not found")
        if _auth.role != "platform_admin" and proposal.tenant_id != _auth.tenant_id:
            raise HTTPException(status_code=403, detail="Permission denied")

        await evolution_engine.dismiss(proposal_id, body.reason)
        return {"status": "dismissed", "proposal_id": proposal_id}

    # ── Evolution Memory API ──

    @app.post("/api/v1/evolution/memories")
    async def create_memory(
        body: CreateMemoryRequest,
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ) -> dict:
        from agent_platform.evolution.memory_models import EvolutionMemory, MemoryType

        if _auth.role != "platform_admin":
            body.tenant_id = _auth.tenant_id

        memory = EvolutionMemory(
            agent_id=body.agent_id,
            tenant_id=body.tenant_id,
            type=MemoryType(body.type),
            content=body.content,
            confidence=body.confidence,
            tags=body.tags,
            source_proposal_id=body.source_proposal_id,
        )
        await _memory_repo.create(memory)
        return memory.model_dump(mode="json")

    @app.get("/api/v1/evolution/memories")
    async def list_memories(
        agent_id: str | None = None,
        tenant_id: str | None = None,
        memory_type: str | None = None,
        status: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> list[dict]:
        from agent_platform.evolution.memory_models import MemoryStatus as MS
        from agent_platform.evolution.memory_models import MemoryType

        tenant_filter = None if _auth.role == "platform_admin" else _auth.tenant_id
        type_filter = MemoryType(memory_type) if memory_type else None
        status_filter = MS(status) if status else None
        if agent_id:
            memories = await _memory_repo.list_by_agent(
                agent_id, memory_type=type_filter, status=status_filter, limit=limit,
            )
        elif tenant_id:
            memories = await _memory_repo.list_by_tenant(
                tenant_id, memory_type=type_filter, status=status_filter, limit=limit,
            )
        else:
            memories = await _memory_repo.list_all(
                memory_type=type_filter, status=status_filter, limit=limit,
            )

        if tenant_filter:
            memories = [m for m in memories if m.tenant_id == tenant_filter]
        return [m.model_dump(mode="json") for m in memories]

    @app.get("/api/v1/evolution/memories/{memory_id}")
    async def get_memory(
        memory_id: str,
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> dict:
        memory = await _memory_repo.get(memory_id)
        if memory is None:
            raise HTTPException(status_code=404, detail=f"memory not found: {memory_id}")
        if _auth.role != "platform_admin" and memory.tenant_id != _auth.tenant_id:
            raise HTTPException(status_code=403, detail="Permission denied")
        return memory.model_dump(mode="json")

    @app.post("/api/v1/evolution/memories/{memory_id}/feedback")
    async def memory_feedback(
        memory_id: str,
        body: MemoryFeedbackRequest,
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ) -> dict:
        memory = await _memory_repo.get(memory_id)
        if memory is None:
            raise HTTPException(status_code=404, detail=f"memory not found: {memory_id}")
        if _auth.role != "platform_admin" and memory.tenant_id != _auth.tenant_id:
            raise HTTPException(status_code=403, detail="Permission denied")

        memory.record_feedback(body.helpful)
        await _memory_repo.update(memory)
        return {"memory_id": memory_id, "trust_score": memory.trust_score}

    @app.delete("/api/v1/evolution/memories/{memory_id}")
    async def delete_memory(
        memory_id: str,
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ) -> dict:
        memory = await _memory_repo.get(memory_id)
        if memory is None:
            raise HTTPException(status_code=404, detail=f"memory not found: {memory_id}")
        if _auth.role != "platform_admin" and memory.tenant_id != _auth.tenant_id:
            raise HTTPException(status_code=403, detail="Permission denied")

        deleted = await _memory_repo.delete(memory_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"memory not found: {memory_id}")
        return {"status": "deleted", "memory_id": memory_id}

    # ── Runtime Memory API ──

    @app.post("/api/v1/runtime-memory")
    async def create_runtime_memory(
        body: CreateRuntimeMemoryRequest,
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ) -> dict:
        from agent_platform.evolution.memory_models import RuntimeMemory, RuntimeMemoryScope, RuntimeMemoryType, MemoryStatus
        from datetime import UTC, datetime, timedelta

        if _auth.role != "platform_admin":
            body.tenant_id = _auth.tenant_id

        expires_at = None
        if body.ttl_seconds is not None:
            expires_at = datetime.now(UTC) + timedelta(seconds=body.ttl_seconds)

        memory = RuntimeMemory(
            agent_id=body.agent_id,
            tenant_id=body.tenant_id,
            scope=RuntimeMemoryScope(body.scope),
            subject_id=body.subject_id,
            session_id=body.session_id,
            type=RuntimeMemoryType(body.type),
            content=body.content,
            source_type=body.source_type,
            source_id=body.source_id,
            confidence=body.confidence,
            privacy_level=body.privacy_level,
            ttl_seconds=body.ttl_seconds,
            expires_at=expires_at,
            status=MemoryStatus.ACTIVE,
        )
        await _runtime_memory_repo.create(memory)
        return memory.model_dump(mode="json")

    @app.get("/api/v1/runtime-memory")
    async def list_runtime_memories(
        agent_id: str | None = None,
        tenant_id: str | None = None,
        scope: str | None = None,
        subject_id: str | None = None,
        session_id: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> list[dict]:
        from agent_platform.evolution.memory_models import RuntimeMemoryScope, MemoryStatus

        tenant_filter = None if _auth.role == "platform_admin" else _auth.tenant_id
        scope_enum = RuntimeMemoryScope(scope) if scope else None

        if agent_id:
            memories = await _runtime_memory_repo.list_by_agent(
                agent_id, scope=scope_enum, status=MemoryStatus.ACTIVE, limit=limit
            )
            # Filter by tenant_id if specified or by caller's tenant
            t_id = tenant_filter or tenant_id
            if t_id:
                memories = [m for m in memories if m.tenant_id == t_id]
        elif tenant_filter or tenant_id:
            t_id = tenant_filter or tenant_id
            memories = await _runtime_memory_repo.list_by_tenant(
                t_id, scope=scope_enum, status=MemoryStatus.ACTIVE, limit=limit
            )
        elif session_id:
            memories = await _runtime_memory_repo.list_by_session(
                session_id, status=MemoryStatus.ACTIVE, limit=limit
            )
            if tenant_filter:
                memories = [m for m in memories if m.tenant_id == tenant_filter]
        elif subject_id:
            memories = await _runtime_memory_repo.list_by_user(
                subject_id, status=MemoryStatus.ACTIVE, limit=limit
            )
            if tenant_filter:
                memories = [m for m in memories if m.tenant_id == tenant_filter]
        else:
            memories = await _runtime_memory_repo.list_all(
                scope=scope_enum, status=MemoryStatus.ACTIVE, limit=limit
            )
            if tenant_filter:
                memories = [m for m in memories if m.tenant_id == tenant_filter]

        return [m.model_dump(mode="json") for m in memories]

    @app.get("/api/v1/runtime-memory/{memory_id}")
    async def get_runtime_memory(
        memory_id: str,
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> dict:
        memory = await _runtime_memory_repo.get(memory_id)
        if memory is None or memory.is_expired():
            raise HTTPException(status_code=404, detail=f"runtime memory not found or expired: {memory_id}")
        if _auth.role != "platform_admin" and memory.tenant_id != _auth.tenant_id:
            raise HTTPException(status_code=403, detail="Permission denied")
        return memory.model_dump(mode="json")

    @app.delete("/api/v1/runtime-memory/{memory_id}")
    async def delete_runtime_memory(
        memory_id: str,
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ) -> dict:
        memory = await _runtime_memory_repo.get(memory_id)
        if memory is None:
            raise HTTPException(status_code=404, detail=f"runtime memory not found: {memory_id}")
        if _auth.role != "platform_admin" and memory.tenant_id != _auth.tenant_id:
            raise HTTPException(status_code=403, detail="Permission denied")

        deleted = await _runtime_memory_repo.delete(memory_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"runtime memory not found: {memory_id}")
        return {"status": "deleted", "memory_id": memory_id}

    # ── Skill Registry API ──

    @app.post("/api/v1/evolution/skills")
    async def create_skill(
        body: CreateSkillRequest,
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ) -> dict:
        from agent_platform.evolution.memory_models import SkillEntry, SkillProvenance

        skill = SkillEntry(
            agent_id=body.agent_id,
            name=body.name,
            description=body.description,
            path=body.path,
            provenance=SkillProvenance(body.provenance),
            tags=body.tags,
        )
        await _skill_repo.create(skill)
        return skill.model_dump(mode="json")

    @app.get("/api/v1/evolution/skills")
    async def list_skills(
        agent_id: str | None = None,
        status: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> list[dict]:
        from agent_platform.evolution.memory_models import MemoryStatus as MS

        status_filter = MS(status) if status else None
        if agent_id:
            skills = await _skill_repo.list_by_agent(agent_id, status=status_filter, limit=limit)
        else:
            skills = await _skill_repo.list_all(status=status_filter, limit=limit)
        return [s.model_dump(mode="json") for s in skills]

    @app.get("/api/v1/evolution/skills/{skill_id}")
    async def get_skill(
        skill_id: str,
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> dict:
        skill = await _skill_repo.get(skill_id)
        if skill is None:
            raise HTTPException(status_code=404, detail=f"skill not found: {skill_id}")
        return skill.model_dump(mode="json")

    @app.post("/api/v1/evolution/skills/scan")
    async def scan_skills(
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ) -> dict:
        from agent_platform.evolution.skill_scanner import sync_skills_to_repo

        agents_dir = Path(__file__).resolve().parents[2] / "agents"
        result = await sync_skills_to_repo(agents_dir, _skill_repo)
        return result

    @app.delete("/api/v1/evolution/skills/{skill_id}")
    async def delete_skill(
        skill_id: str,
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ) -> dict:
        deleted = await _skill_repo.delete(skill_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"skill not found: {skill_id}")
        return {"status": "deleted", "skill_id": skill_id}

    @app.get("/api/v1/evolution/metrics")
    async def evolution_metrics(
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ) -> dict:
        from agent_platform.evolution.metrics import EvolutionMetricsCollector

        collector = EvolutionMetricsCollector(_evo_repo)
        metrics = await collector.collect()
        return {
            "total_proposals": metrics.total_proposals,
            "by_status": metrics.by_status,
            "by_risk": metrics.by_risk,
            "by_agent": metrics.by_agent,
            "by_root_cause": metrics.by_root_cause,
            "dispatched_count": metrics.dispatched_count,
            "dismissed_count": metrics.dismissed_count,
            "closed_count": metrics.closed_count,
            "auto_dispatch_count": metrics.auto_dispatch_count,
            "outcome_merged": metrics.outcome_merged,
            "outcome_rejected": metrics.outcome_rejected,
            "outcome_abandoned": metrics.outcome_abandoned,
            "avg_time_to_dispatch_hours": metrics.avg_time_to_dispatch_hours,
            "avg_time_to_close_hours": metrics.avg_time_to_close_hours,
        }

    # ── Background Review Fork APIs ──

    @app.get("/api/v1/evolution/review-fork/audits")
    async def list_review_fork_audits(
        agent_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> list[dict]:
        audits = await _review_audit_repo.list_all(agent_id=agent_id, limit=limit)
        return [a.model_dump(mode="json") for a in audits]

    @app.get("/api/v1/evolution/review-fork/status/{agent_id}")
    async def get_review_fork_status(
        agent_id: str,
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> dict:
        suspended = await review_fork.is_suspended(agent_id)
        rate, count = await review_fork.get_rejection_rate(agent_id)
        return {
            "agent_id": agent_id,
            "suspended": suspended,
            "rejection_rate": rate,
            "total_resolved_candidates": count,
        }

    @app.post("/api/v1/evolution/review-fork/resume/{agent_id}")
    async def resume_review_fork(
        agent_id: str,
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ) -> dict:
        await review_fork.resume(agent_id)
        return {"status": "resumed", "agent_id": agent_id}

    @app.post("/api/v1/evolution/review-fork/suspend/{agent_id}")
    async def suspend_review_fork(
        agent_id: str,
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ) -> dict:
        await review_fork.suspend_manually(agent_id)
        return {"status": "suspended", "agent_id": agent_id}

    # ── Evolution Engine Auto Trigger / Suspension APIs ──

    @app.get("/api/v1/evolution/engine/status/{agent_id}")
    async def get_evolution_engine_status(
        agent_id: str,
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> dict:
        engine = app.state.evolution_engine
        suspended = await engine.is_agent_suspended(agent_id)
        manually_suspended = agent_id in engine._manually_suspended
        return {
            "agent_id": agent_id,
            "suspended": suspended,
            "manually_suspended": manually_suspended,
        }

    @app.post("/api/v1/evolution/engine/resume/{agent_id}")
    async def resume_evolution_engine(
        agent_id: str,
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ) -> dict:
        engine = app.state.evolution_engine
        engine.resume_agent(agent_id)
        return {"status": "resumed", "agent_id": agent_id}

    @app.post("/api/v1/evolution/engine/suspend/{agent_id}")
    async def suspend_evolution_engine(
        agent_id: str,
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ) -> dict:
        engine = app.state.evolution_engine
        engine.suspend_agent(agent_id)
        return {"status": "suspended", "agent_id": agent_id}

    # ── Candidate Store & Promotion APIs ──

    @app.post("/api/v1/evolution/candidates")
    async def create_candidate(
        payload: dict,
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ) -> dict:
        from agent_platform.evolution.models import Candidate, CandidateType, CandidateStatus, PromotionTarget, RiskLevel

        if _auth.role != "platform_admin":
            payload["tenant_id"] = _auth.tenant_id

        try:
            cand = Candidate(
                candidate_type=CandidateType(payload["candidate_type"]),
                agent_id=payload["agent_id"],
                tenant_id=payload.get("tenant_id", "default"),
                environment=payload.get("environment", "prod"),
                generated_by=payload.get("generated_by", "hermes"),
                generator_role=payload.get("generator_role"),
                source_event_ids=payload.get("source_event_ids", []),
                evidence_ids=payload.get("evidence_ids", []),
                payload=payload.get("payload", {}),
                risk_level=RiskLevel(payload.get("risk_level", "low")),
                promotion_target=PromotionTarget(payload.get("promotion_target", "none")),
                status=CandidateStatus(payload.get("status", "draft")),
            )
            await _candidate_repo.create(cand)
            return cand.model_dump(mode="json")
        except KeyError as e:
            raise HTTPException(status_code=400, detail=f"Missing required field: {e}")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid field value: {e}")

    @app.get("/api/v1/evolution/candidates")
    async def list_candidates(
        candidate_type: str | None = None,
        agent_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> list[dict]:
        from agent_platform.evolution.models import CandidateType, CandidateStatus

        tenant_filter = None if _auth.role == "platform_admin" else _auth.tenant_id
        c_type = None
        if candidate_type:
            try:
                c_type = CandidateType(candidate_type)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid candidate_type: {candidate_type}")

        c_status = None
        if status:
            try:
                c_status = CandidateStatus(status)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

        candidates = await _candidate_repo.list_all(
            candidate_type=c_type,
            agent_id=agent_id,
            status=c_status,
            limit=limit,
        )
        if tenant_filter:
            candidates = [c for c in candidates if c.tenant_id == tenant_filter]
        return [c.model_dump(mode="json") for c in candidates]

    @app.get("/api/v1/evolution/candidates/{candidate_id}")
    async def get_candidate(
        candidate_id: str,
        _auth: AuthIdentity = _SCOPE_READ,
    ) -> dict:
        cand = await _candidate_repo.get(candidate_id)
        if cand is None:
            raise HTTPException(status_code=404, detail="Candidate not found")
        if _auth.role != "platform_admin" and cand.tenant_id != _auth.tenant_id:
            raise HTTPException(status_code=403, detail="Permission denied")
        return cand.model_dump(mode="json")

    @app.post("/api/v1/evolution/candidates/{candidate_id}/validate")
    async def validate_candidate(
        candidate_id: str,
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ) -> dict:
        from agent_platform.evolution.candidate_validator import CandidateValidator
        from agent_platform.evolution.models import CandidateStatus

        cand = await _candidate_repo.get(candidate_id)
        if cand is None:
            raise HTTPException(status_code=404, detail="Candidate not found")
        if _auth.role != "platform_admin" and cand.tenant_id != _auth.tenant_id:
            raise HTTPException(status_code=403, detail="Permission denied")

        validator = CandidateValidator()
        errors = validator.validate(cand)

        if errors:
            await _candidate_repo.update_status(
                candidate_id,
                CandidateStatus.REJECTED,
                validation_errors=errors,
            )
            return {
                "status": "rejected",
                "validation_passed": False,
                "errors": errors,
            }

        await _candidate_repo.update_status(
            candidate_id,
            CandidateStatus.VALIDATED,
            validation_errors=[],
        )
        return {
            "status": "validated",
            "validation_passed": True,
            "errors": [],
        }

    @app.post("/api/v1/evolution/candidates/{candidate_id}/approve")
    async def approve_candidate(
        candidate_id: str,
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ) -> dict:
        from agent_platform.evolution.models import CandidateStatus

        cand = await _candidate_repo.get(candidate_id)
        if cand is None:
            raise HTTPException(status_code=404, detail="Candidate not found")
        if _auth.role != "platform_admin" and cand.tenant_id != _auth.tenant_id:
            raise HTTPException(status_code=403, detail="Permission denied")

        if cand.status not in (CandidateStatus.DRAFT, CandidateStatus.VALIDATED):
            raise HTTPException(
                status_code=400,
                detail=f"Only DRAFT or VALIDATED candidates can be approved, current: {cand.status}",
            )

        # 流转到 APPROVED 状态
        await _candidate_repo.update_status(candidate_id, CandidateStatus.APPROVED)
        return {"status": "approved"}

    @app.post("/api/v1/evolution/candidates/{candidate_id}/promote")
    async def promote_candidate(
        candidate_id: str,
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ) -> dict:
        from agent_platform.evolution.models import CandidateStatus, RiskLevel
        from agent_platform.evolution.promotion import PromotionExecutor, PromotionError

        cand = await _candidate_repo.get(candidate_id)
        if cand is None:
            raise HTTPException(status_code=404, detail="Candidate not found")
        if _auth.role != "platform_admin" and cand.tenant_id != _auth.tenant_id:
            raise HTTPException(status_code=403, detail="Permission denied")

        # 策略自动审批：如果是 LOW 风险且状态是 VALIDATED，我们可以在此处直接晋升为 APPROVED
        if cand.status == CandidateStatus.VALIDATED and cand.risk_level == RiskLevel.LOW:
            await _candidate_repo.update_status(candidate_id, CandidateStatus.APPROVED)
            cand.status = CandidateStatus.APPROVED

        if cand.status != CandidateStatus.APPROVED:
            raise HTTPException(
                status_code=400,
                detail=f"Only APPROVED candidates can be promoted, current: {cand.status}. "
                       f"Please call /approve API first.",
            )

        executor = PromotionExecutor(
            proposal_repo=_evo_repo,
            memory_repo=_memory_repo,
            evolution_engine=evolution_engine,
        )

        try:
            result = await executor.promote(cand)
            # 在 repo 中更新状态（promote 内部已将 cand.status 设为 PROMOTED，这里更新持久层）
            await _candidate_repo.update_status(candidate_id, CandidateStatus.PROMOTED)
            return result
        except PromotionError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/v1/evolution/candidates/{candidate_id}/reject")
    async def reject_candidate(
        candidate_id: str,
        reason: dict | None = None,
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ) -> dict:
        from agent_platform.evolution.models import CandidateStatus

        cand = await _candidate_repo.get(candidate_id)
        if cand is None:
            raise HTTPException(status_code=404, detail="Candidate not found")
        if _auth.role != "platform_admin" and cand.tenant_id != _auth.tenant_id:
            raise HTTPException(status_code=403, detail="Permission denied")

        errors = [reason.get("reason", "rejected by user")] if reason else ["rejected by user"]
        await _candidate_repo.update_status(
            candidate_id,
            CandidateStatus.REJECTED,
            validation_errors=errors,
        )
        return {"status": "rejected", "errors": errors}

    @app.delete("/api/v1/evolution/candidates/{candidate_id}")
    async def delete_candidate(
        candidate_id: str,
        _auth: AuthIdentity = _SCOPE_ADMIN,
    ) -> dict:
        cand = await _candidate_repo.get(candidate_id)
        if cand is None:
            raise HTTPException(status_code=404, detail="Candidate not found")
        if _auth.role != "platform_admin" and cand.tenant_id != _auth.tenant_id:
            raise HTTPException(status_code=403, detail="Permission denied")

        await _candidate_repo.delete(candidate_id)
        return {"status": "deleted", "candidate_id": candidate_id}


    # ── OpenTelemetry FastAPI Instrumentation ──
    # 在所有路由注册完成后挂载，如未安装 opentelemetry-instrumentation-fastapi 则静默跳过
    from agent_platform.observability.fastapi_instrumentation import (
        instrument_app as _instrument_app,
    )
    _otel_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or getattr(
        settings,
        "otel_endpoint",
        None,
    )
    _instrument_app(
        app,
        service_name=getattr(settings, "otel_service_name", "agent-platform"),
        otlp_endpoint=_otel_endpoint,
    )

    return app


app = create_app()


def _error_response(
    request: AgentRequest,
    *,
    code: str,
    message: str,
    status_code: int,
    agent_id: str | None = None,
    agent_version: str = "unknown",
) -> AgentResponse:
    """构建一个标准的错误响应对象。"""
    return AgentResponse(
        request_id=request.request_id,
        session_id=request.session_id,
        agent=AgentIdentity(
            agent_id=agent_id or request.agent_id or "unknown",
            agent_version=agent_version,
        ),
        output=AgentOutput(
            status=OutputStatus.FAILED,
            text=ResponseText(display=message, tts=message),
        ),
        trace=ResponseTrace(run_id=f"run_{uuid4().hex}", error=code),
        error=AgentError(
            code=code,
            message=message,
            details={"http_status": status_code},
            retryable=False,
        ),
    )


def _missing_required_context(request: AgentRequest, required_paths: list[str]) -> list[str]:
    """检查请求负载中是否缺少必填的上下文路径。同时检查 alias 和 canonical 字段名。"""
    missing: list[str] = []
    payload_alias = request.model_dump(by_alias=True)
    payload_canonical = request.model_dump(by_alias=False)
    for path in required_paths:
        found = False
        for payload in (payload_alias, payload_canonical):
            value = payload
            for part in path.split("."):
                if not isinstance(value, dict) or part not in value:
                    value = None
                    break
                value = value[part]
            if value not in (None, ""):
                found = True
                break
        if not found:
            missing.append(path)
    return missing


def _deployment_status(channel: str, traffic_percent: int) -> AgentDeploymentStatus:
    """根据部署渠道和流量比例确定部署的状态。"""
    if channel == "staging":
        return AgentDeploymentStatus.STAGING
    if channel == "prod" and traffic_percent < 100:
        return AgentDeploymentStatus.PROD_CANARY
    if channel == "prod":
        return AgentDeploymentStatus.PROD
    return AgentDeploymentStatus.REGISTERED


def _parse_service_secrets(raw: str | None) -> dict[str, str] | None:
    """解析 'svc-id1:secret1,svc-id2:secret2' 格式的服务密钥。"""
    if not raw:
        return None
    result: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if ":" in pair:
            svc_id, secret = pair.split(":", 1)
            result[svc_id.strip()] = secret.strip()
    return result or None
