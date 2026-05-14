import json
import logging
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from agent_platform.config import get_settings
from agent_platform.devflow.orchestrator import DevFlowOrchestrator
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
from agent_platform.integrations.gitlab.adapter import GitLabAdapter
from agent_platform.integrations.plane.adapter import PlaneAdapter
from agent_platform.integrations.plane.webhook import PlaneWebhookError, PlaneWebhookVerifier
from agent_platform.registry.registry import AgentNotFoundError, AgentRegistry
from agent_platform.router import AgentRouter
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
    eval_passed: bool = True


class CreateTaskPackRequest(BaseModel):
    task_id: str
    title: str
    task_type: str
    project_id: str
    background: str
    agent_id: str | None = None


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or f"req_{uuid4().hex}"
        tenant_id = request.headers.get("x-tenant-id")
        request.state.request_id = request_id
        request.state.tenant_id = tenant_id
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


def create_app() -> FastAPI:
    settings = get_settings()
    registry = AgentRegistry(Path(settings.registry_root))
    router = AgentRouter(registry, settings)
    runtime_manager = RuntimeManager()
    eval_runner = EvalRunner(runtime_manager)
    task_pack_generator = TaskPackGenerator()

    app = FastAPI(title="Agent Platform", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    devflow: DevFlowOrchestrator | None = None
    if settings.plane_base_url and settings.plane_api_key and settings.gitlab_base_url:
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
            gitlab_project_id=settings.plane_workspace_slug,
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

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
        if payload.channel in {"staging", "prod"} and not payload.eval_passed:
            raise HTTPException(status_code=409, detail="eval gate must pass before deployment")

        status = _deployment_status(payload.channel, payload.traffic_percent)
        deployment = registry.deploy(
            agent_id=agent_id,
            version=version,
            channel=payload.channel,
            status=status,
            tenant_id=payload.tenant_id,
            traffic_percent=payload.traffic_percent,
        )
        return deployment.model_dump(mode="json")

    @app.post("/api/v1/evals/run", response_model=EvalReport)
    async def run_eval(payload: RunEvalRequest) -> EvalReport:
        try:
            spec = registry.get(payload.agent_id)
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return await eval_runner.run_agent(spec)

    @app.post("/api/v1/devflow/task-packs")
    async def create_task_pack(payload: CreateTaskPackRequest):
        return task_pack_generator.from_requirement(**payload.model_dump())

    @app.post("/api/v1/agent/chat", response_model=AgentResponse)
    async def chat(request: AgentRequest, raw_request: Request) -> AgentResponse:
        if not request.request_id:
            req_id = getattr(raw_request.state, "request_id", None)
            request.request_id = req_id or f"req_{uuid4().hex}"
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

        runtime_response = await runtime_manager.run(
            RuntimeRequest(
                request=request,
                agent_spec=route.agent_spec,
                route_reason=route.reason,
                deployment_id=route.deployment_id,
            )
        )
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
    payload = request.model_dump()
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
