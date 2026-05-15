import json
import logging
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import StreamingResponse

from agent_platform.api.rate_limiter import RateLimiterMiddleware
from agent_platform.api.streaming import stream_agent_response
from agent_platform.api.websocket import AgentWebSocketManager
from agent_platform.config import get_settings
from agent_platform.devflow.agents import ArchitectureDesignAgent, TestGenerationAgent
from agent_platform.devflow.issue_generator import IssueGenerator
from agent_platform.devflow.orchestrator import DevFlowOrchestrator
from agent_platform.devflow.requirement_parser import RequirementParser
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
from agent_platform.integrations.plane.adapter import PlaneAdapter
from agent_platform.integrations.plane.webhook import (
    PlaneWebhookError,
    PlaneWebhookVerifier,
)
from agent_platform.knowledge import KnowledgeService
from agent_platform.observability.logging_config import setup_logging
from agent_platform.observability.metrics import MetricsCollector
from agent_platform.policy import PolicyEngine
from agent_platform.registry.artifact import ArtifactStore
from agent_platform.registry.deployment import DeploymentAuditLog
from agent_platform.registry.registry import AgentNotFoundError, AgentRegistry
from agent_platform.router import AgentRouter
from agent_platform.router_semantic import SemanticRouter
from agent_platform.runtime.manager import RuntimeManager

logger = logging.getLogger(__name__)


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


class RequestContextMiddleware(BaseHTTPMiddleware):
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
    def __init__(self, app, api_key: str | None = None):
        super().__init__(app)
        self.api_key = api_key

    async def dispatch(self, request: Request, call_next):
        if not self.api_key:
            return await call_next(request)
        if request.url.path in {"/health", "/docs", "/openapi.json", "/redoc"}:
            return await call_next(request)
        auth_header = request.headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]
            if token == self.api_key:
                return await call_next(request)
        api_key_header = request.headers.get("x-api-key")
        if api_key_header == self.api_key:
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


def create_app() -> FastAPI:
    setup_logging()

    settings = get_settings()
    registry = AgentRegistry(Path(settings.registry_root))
    app_semantic_router = SemanticRouter()
    router = AgentRouter(registry, settings, semantic_router=app_semantic_router)

    app_policy_engine = PolicyEngine()
    app_knowledge_service = KnowledgeService()
    app_hook_registry = HookRegistry()
    app_metrics = MetricsCollector()

    runtime_manager = RuntimeManager(
        policy_engine=app_policy_engine,
        hook_registry=app_hook_registry,
        metrics_collector=app_metrics,
    )
    eval_runner = EvalRunner(runtime_manager)
    task_pack_generator = TaskPackGenerator()
    webhook_deliveries: set[str] = set()

    requirement_parser = RequirementParser()
    issue_generator = IssueGenerator()
    scaffolder = AgentScaffolder(settings.registry_root)
    architect_agent = ArchitectureDesignAgent()
    test_agent = TestGenerationAgent()
    audit_log = DeploymentAuditLog()
    artifact_store = ArtifactStore()
    ws_manager = AgentWebSocketManager(router, runtime_manager)

    app = FastAPI(title="Agent Platform", version="0.2.0")
    app.state.policy_engine = app_policy_engine
    app.state.knowledge_service = app_knowledge_service
    app.state.hook_registry = app_hook_registry
    app.state.semantic_router = app_semantic_router
    app.state.metrics = app_metrics

    if settings.api_key:
        app.add_middleware(AuthMiddleware, api_key=settings.api_key)

    app.add_middleware(RateLimiterMiddleware, requests_per_minute=120, burst=20)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestContextMiddleware)
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
        devflow = DevFlowOrchestrator(
            plane=plane_adapter,
            gitlab=gitlab_adapter,
            gitlab_project_id=settings.gitlab_project_id,
        )
    app.state.devflow_enabled = devflow is not None

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

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
            for spec in registry.list_agents()
        ]

    @app.post("/api/v1/agent-packages/register")
    async def register_agent(payload: RegisterAgentRequest) -> dict[str, str]:
        spec = registry.loader.load_file(Path(payload.manifest_path))
        registry.register(spec)
        return {"agent_id": spec.agent_id, "version": spec.version, "status": "registered"}

    @app.get("/api/v1/agent-runs")
    async def list_agent_runs() -> list[dict]:
        return [run.model_dump(mode="json") for run in runtime_manager.run_store.list_runs()]

    @app.get("/api/v1/agent-deployments")
    async def list_agent_deployments() -> list[dict]:
        return [deployment.model_dump(mode="json") for deployment in registry.list_deployments()]

    @app.get("/api/v1/sessions")
    async def list_sessions(agent_id: str | None = None) -> list[dict]:
        sessions = runtime_manager.session_store.list_sessions(agent_id)
        return [s.model_dump(mode="json") for s in sessions]

    @app.get("/api/v1/sessions/{session_id}")
    async def get_session(session_id: str) -> dict:
        session = runtime_manager.session_store.load(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
        return session.model_dump(mode="json")

    @app.post("/api/v1/agent-packages/{agent_id}/versions/{version}/deploy")
    async def deploy_agent(agent_id: str, version: str, payload: DeployAgentRequest) -> dict:
        try:
            spec = registry.get(agent_id)
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

        status = _deployment_status(payload.channel, payload.traffic_percent)
        previous_deployment = registry.resolve_deployment(
            agent_id=agent_id,
            channel=payload.channel,
            tenant_id=payload.tenant_id,
        )
        deployment = registry.deploy(
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

        audit_log.record_deploy(
            deployment,
            previous_version=previous_deployment.version if previous_deployment else None,
            artifact_id=artifact_meta.artifact_id,
        )
        result = deployment.model_dump(mode="json")
        result["artifact_id"] = artifact_meta.artifact_id
        if eval_report:
            result["eval"] = eval_report.model_dump(mode="json")
        return result

    @app.post("/api/v1/evals/run", response_model=EvalReport)
    async def run_eval(payload: RunEvalRequest) -> EvalReport:
        try:
            spec = registry.get(payload.agent_id)
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return await eval_runner.run_agent(spec)

    @app.post("/api/v1/evals/ci-callback")
    async def eval_ci_callback(
        agent_id: str,
        project_id: str | None = None,
        mr_iid: int | None = None,
        work_item_id: str | None = None,
    ) -> dict:
        try:
            spec = registry.get(agent_id)
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        report = await eval_runner.run_agent(spec)
        result: dict = {"agent_id": agent_id, "gate_passed": report.gate_passed}

        if devflow and project_id and mr_iid:
            from agent_platform.evals.feedback import EvalFeedback
            gitlab_adapter = devflow.gitlab
            feedback = EvalFeedback(gitlab=gitlab_adapter)
            await feedback.post_to_gitlab(report, project_id, mr_iid)
            result["gitlab_comment_posted"] = True

        return result

    @app.post("/api/v1/devflow/task-packs")
    async def create_task_pack(payload: CreateTaskPackRequest):
        return task_pack_generator.from_requirement(**payload.model_dump())

    @app.post("/api/v1/devflow/parse-requirement")
    async def parse_requirement(payload: ParseRequirementRequest):
        return requirement_parser.parse(
            payload.text, payload.context or {},
        ).model_dump()

    @app.post("/api/v1/devflow/generate-issues")
    async def generate_issues(payload: GenerateIssuesRequest):
        parsed = requirement_parser.parse(
            payload.text, payload.project_context or {},
        )
        issues = issue_generator.generate(
            parsed, payload.project_context or {},
        )
        return [i.model_dump() for i in issues]

    @app.post("/api/v1/devflow/scaffold-agent")
    async def scaffold_agent(payload: ScaffoldAgentRequest):
        path = scaffolder.create(**payload.model_dump())
        return {"agent_id": payload.agent_id, "path": str(path)}

    @app.post("/api/v1/devflow/design-analysis")
    async def design_analysis(payload: DesignAnalysisRequest):
        brief = architect_agent.analyze(
            payload.requirement_text, payload.context,
        )
        return brief.model_dump()

    @app.post("/api/v1/devflow/test-plan")
    async def test_plan(payload: TestPlanRequest):
        plan = test_agent.generate_plan(
            payload.agent_id,
            payload.change_type,
            payload.changed_files,
        )
        return plan.model_dump()

    @app.post("/api/v1/deployments/rollback")
    async def rollback_deployment(payload: RollbackRequest):
        rollback_info = audit_log.get_rollback_version(
            payload.agent_id, payload.channel,
        )
        if not rollback_info:
            raise HTTPException(
                status_code=404,
                detail=f"no rollback target for {payload.agent_id}:{payload.channel}",
            )
        target_version, _rollback_artifact_id = rollback_info
        current_deployment = registry.resolve_deployment(
            agent_id=payload.agent_id,
            channel=payload.channel,
        )
        current_version = current_deployment.version if current_deployment else None

        deployment = registry.deploy(
            agent_id=payload.agent_id,
            version=target_version,
            channel=payload.channel,
            status=AgentDeploymentStatus.ROLLED_BACK,
        )
        audit_log.record_rollback(
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
        events = audit_log.list_events(agent_id, channel, limit)
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
        return Response(
            content=data,
            media_type="application/gzip",
            headers={"Content-Disposition": f"attachment; filename={artifact_id.replace('@', '_')}.tar.gz"},
        )

    @app.websocket("/ws/agent/chat")
    async def websocket_chat(websocket: WebSocket, session_id: str | None = None):
        await ws_manager.handle(websocket, session_id)

    @app.post("/api/v1/agent/chat", response_model=AgentResponse)
    async def chat(request: AgentRequest, raw_request: Request) -> AgentResponse:
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
            route = router.route(request)
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

    @app.post("/api/v1/integrations/plane/webhook")
    async def plane_webhook(
        request: Request,
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

        if x_plane_delivery and x_plane_delivery in webhook_deliveries:
            return {
                "status": "duplicate",
                "delivery_id": x_plane_delivery,
                "event": x_plane_event,
            }
        if x_plane_delivery:
            webhook_deliveries.add(x_plane_delivery)

        result: dict[str, str | None] = {
            "status": "accepted",
            "delivery_id": x_plane_delivery,
            "event": x_plane_event,
        }

        if devflow and x_plane_event:
            try:
                payload = json.loads(raw_body) if raw_body else {}
                devflow_result = await devflow.handle_webhook_event(x_plane_event, payload)
                if devflow_result:
                    result["devflow_branch"] = devflow_result.branch
                    result["devflow_mr_url"] = devflow_result.mr_url
            except Exception:
                logger.exception("DevFlow orchestration failed for event %s", x_plane_event)

        return result

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
    if channel == "staging":
        return AgentDeploymentStatus.STAGING
    if channel == "prod" and traffic_percent < 100:
        return AgentDeploymentStatus.PROD_CANARY
    if channel == "prod":
        return AgentDeploymentStatus.PROD
    return AgentDeploymentStatus.REGISTERED
