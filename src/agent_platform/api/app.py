import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import StreamingResponse

from agent_platform.api.access_log import AccessLogMiddleware
from agent_platform.api.admin import router as admin_router
from agent_platform.api.admin_deps import AdminDeps
from agent_platform.api.auth import AuthIdentity, require_role, require_scope
from agent_platform.api.rate_limiter import RateLimiterMiddleware
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
from agent_platform.hooks import HookRegistry
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
    InMemoryToolAuditRepository,
    InMemoryWebhookDeliveryRepository,
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
    traffic_percent: int = 100
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
    支持同步 ApiKeyStore（内存）和异步 SqlApiKeyStore（持久化）。
    """

    _ALL_SCOPES = ["chat", "deploy", "admin", "eval", "register", "rollback", "read"]
    _SKIP_PATHS = {"/health", "/health/ready", "/metrics", "/docs", "/openapi.json", "/redoc"}

    def __init__(self, app, api_key: str | None = None, key_store=None):
        super().__init__(app)
        self.api_key = api_key
        self.key_store = key_store

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self._SKIP_PATHS:
            return await call_next(request)

        if not self.api_key and self.key_store is None:
            self._set_auth_context(request, AuthIdentity(
                subject="anonymous",
                tenant_id=request.headers.get("x-tenant-id", "default"),
                role="platform_admin",
                scopes=self._ALL_SCOPES,
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

        if self.api_key and token == self.api_key:
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


@asynccontextmanager
async def _app_lifespan(app: FastAPI):
    """Application lifespan: startup validation and graceful shutdown."""
    settings = getattr(app.state, "_settings", None)
    if settings:
        _validate_startup_config(settings)
    # 记录启动时间，供 /status 端点计算 uptime
    app.state.started_at = time.time()
    logger.info("Agent Platform started (env=%s)", settings.env if settings else "unknown")
    yield
    for name, resource in getattr(app.state, "_closeables", []):
        try:
            if hasattr(resource, "close"):
                await resource.close()
            elif hasattr(resource, "dispose"):
                await resource.dispose()
            logger.info("Closed %s", name)
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

            db_engine = create_async_engine(settings.database_url, echo=False)
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
            SqlDeploymentAuditRepository,
            SqlEvalRunRepository,
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
    else:
        run_repo = InMemoryAgentRunRepository()
        session_repo = InMemoryAgentSessionRepository()
        webhook_repo = InMemoryWebhookDeliveryRepository()
        audit_repo = InMemoryDeploymentAuditRepository()
        eval_repo = InMemoryEvalRunRepository()
        definition_repo = None
        deployment_repo = None
        tool_audit_repo = InMemoryToolAuditRepository()

    coding_job_repo = InMemoryCodingJobRepository()
    tool_executor.audit_repo = tool_audit_repo

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
    dlq = InMemoryDeadLetterQueue()
    webhook_retry_service = WebhookRetryService(dlq=dlq)

    key_store = None
    if db_session_factory is not None:
        from agent_platform.persistence.sql import SqlApiKeyStore
        key_store = SqlApiKeyStore(db_session_factory)
        if settings.api_key:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is None:
                asyncio.run(key_store.add_key(
                    settings.api_key,
                    key_id="bootstrap-key",
                    role="platform_admin",
                    created_by="bootstrap",
                ))

    ws_manager = AgentWebSocketManager(
        router, runtime_manager,
        api_key=settings.api_key,
        key_store=key_store,
    )

    app = FastAPI(title="Agent Platform", version="0.2.0", lifespan=_app_lifespan)
    app.state._settings = settings
    app.state._closeables = []
    if db_engine:
        app.state._closeables.append(("SQLAlchemy engine", db_engine))
    if langfuse_tracer.enabled:
        app.state._closeables.append(("LangfuseTracer", langfuse_tracer))
    app.state.policy_engine = app_policy_engine
    app.state.knowledge_service = app_knowledge_service
    app.state.langfuse = langfuse_tracer
    app.state.hook_registry = app_hook_registry
    app.state.semantic_router = app_semantic_router
    app.state.metrics = app_metrics
    app.state.webhook_repo = webhook_repo
    app.state.audit_repo = audit_repo
    app.state.eval_repo = eval_repo
    app.state.db_session_factory = db_session_factory
    app.state.approval_gate = approval_gate

    # ── 多租户配额管理 ──
    from agent_platform.api.tenant_quota import TenantQuotaManager
    quota_manager = TenantQuotaManager()
    app.state.quota_manager = quota_manager

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
    )
    app.include_router(
        admin_router,
        dependencies=[_ROLE_ADMIN],
    )

    app.state.key_store = key_store

    app.add_middleware(
        AuthMiddleware, api_key=settings.api_key, key_store=key_store,
    )
    app.add_middleware(RateLimiterMiddleware, requests_per_minute=120, burst=20)
    app.add_middleware(AccessLogMiddleware)

    cors_raw = settings.cors_allowed_origins
    if cors_raw and cors_raw != "*":
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

    devflow: DevFlowOrchestrator | None = None
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
        workspace_manager = WorkspaceManager(base_dir=workspace_base)
        adapter = create_adapter(settings.devflow_runner_adapter)
        coding_runner = CodingAgentRunner(
            adapter=adapter,
            workspace_manager=workspace_manager,
            gitlab=gitlab_adapter,
            plane=plane_adapter,
            gitlab_project_id=settings.gitlab_project_id,
            repo_url=settings.devflow_repo_url,
            testing_state_id=settings.plane_testing_state_id,
            job_repo=coding_job_repo,
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
        )
        logger.info(
            "DevFlow enabled: adapter=%s, project=%s",
            settings.devflow_runner_adapter,
            settings.gitlab_project_id,
        )

    # GitLab reverse sync handler
    gitlab_event_handler: GitLabEventHandler | None = None
    if devflow is not None:
        gitlab_event_handler = GitLabEventHandler(
            plane=plane_adapter,
            webhook_repo=webhook_repo,
            testing_state_id=settings.plane_testing_state_id,
            human_review_state_id=settings.plane_human_review_state_id,
            staging_state_id=settings.plane_staging_state_id,
            done_state_id=settings.plane_done_state_id,
            ai_developing_state_id=settings.plane_ai_developing_state_id,
        )

    app.state.devflow_enabled = devflow is not None

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
            except Exception as exc:
                checks["database"] = f"error: {exc}"
                overall = False
        else:
            checks["database"] = "in_memory"

        checks["devflow"] = "enabled" if devflow is not None else "disabled"
        checks["runner_adapter"] = settings.devflow_runner_adapter
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
            checks["weaviate"] = settings.weaviate_url

        if settings.redis_url:
            checks["redis"] = settings.redis_url

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
    async def list_agents() -> list[dict[str, str]]:
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
        spec = registry.loader.load_file(Path(payload.manifest_path))
        await registry.register(spec)
        return {"agent_id": spec.agent_id, "version": spec.version, "status": "registered"}

    @app.get("/api/v1/agents/{agent_id}/health")
    async def agent_health(agent_id: str) -> dict:
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
    async def list_agent_deployments() -> list[dict]:
        deployments = await registry.list_deployments()
        return [deployment.model_dump(mode="json") for deployment in deployments]

    @app.get("/api/v1/agent-deployments/{deployment_id}/metrics")
    async def deployment_metrics(deployment_id: str) -> dict:
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
    async def list_sessions(agent_id: str | None = None) -> list[dict]:
        sessions = await runtime_manager.list_sessions(agent_id=agent_id)
        return [s.model_dump(mode="json") for s in sessions]

    @app.get("/api/v1/sessions/{session_id}")
    async def get_session(session_id: str) -> dict:
        session = await runtime_manager.load_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
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
            if not tool_registry.get(tool_name):
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

        await audit_log.record_deploy(
            deployment,
            previous_version=previous_deployment.version if previous_deployment else None,
            artifact_id=artifact_meta.artifact_id,
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
        status: str | None = None, limit: int = 50,
    ) -> list[dict]:
        return await coding_job_repo.list_jobs(status=status, limit=limit)

    @app.get("/api/v1/devflow/jobs/{job_id}")
    async def get_devflow_job(job_id: str) -> dict:
        job = await coding_job_repo.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
        return job

    @app.get("/api/v1/devflow/status")
    async def devflow_status() -> dict[str, Any]:
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
    ):
        events = await audit_log.list_events(agent_id, channel, limit)
        return [e.model_dump(mode="json") for e in events]

    @app.get("/api/v1/artifacts")
    async def list_artifacts(agent_id: str | None = None) -> list[dict]:
        return [a.model_dump(mode="json") for a in artifact_store.list_artifacts(agent_id)]

    @app.get("/api/v1/artifacts/{artifact_id}")
    async def get_artifact(artifact_id: str) -> dict:
        meta = artifact_store.get_metadata(artifact_id)
        if not meta:
            raise HTTPException(status_code=404, detail=f"artifact not found: {artifact_id}")
        return meta.model_dump(mode="json")

    @app.get("/api/v1/artifacts/{artifact_id}/download")
    async def download_artifact(artifact_id: str) -> Response:
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
    async def list_pending_approvals() -> list[dict]:
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
    """检查请求负载中是否缺少必填的上下文路径。"""
    missing: list[str] = []
    payload = request.model_dump(by_alias=True)
    for path in required_paths:
        value = payload
        for part in path.split("."):
            if not isinstance(value, dict) or part not in value:
                value = None
                break
            value = value[part]
        if value in (None, ""):
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
