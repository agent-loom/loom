from time import perf_counter
from uuid import uuid4

from agent_platform.domain.models import (
    AgentError,
    AgentIdentity,
    AgentOutput,
    AgentResponse,
    AgentRun,
    AgentRunStatus,
    OutputStatus,
    ResponseText,
    ResponseTrace,
    RuntimeRequest,
    RuntimeResponse,
)
from agent_platform.observability.trace import InMemoryRunStore, RunStore
from agent_platform.runtime.hermes import HermesRuntimeBackend
from agent_platform.runtime.native import NativeRuntimeBackend


class RuntimeManager:
    def __init__(self, run_store: RunStore | None = None):
        self._backends = {
            NativeRuntimeBackend.name: NativeRuntimeBackend(),
            HermesRuntimeBackend.name: HermesRuntimeBackend(),
        }
        self.run_store = run_store or InMemoryRunStore()

    def register(self, backend) -> None:
        self._backends[backend.name] = backend

    async def run(self, request: RuntimeRequest) -> RuntimeResponse:
        run_id = f"run_{uuid4().hex}"
        started = perf_counter()
        backend_name = request.agent_spec.manifest.runtime.backend
        try:
            backend = self._backends[backend_name]
        except KeyError as exc:
            raise ValueError(f"runtime backend not registered: {backend_name}") from exc

        try:
            response = await backend.run(request)
        except Exception as exc:
            latency_ms = self._latency_ms(started)
            error = AgentError(
                code="RUNTIME_ERROR",
                message=str(exc),
                retryable=False,
            )
            agent = request.agent_spec
            failed_response = AgentResponse(
                request_id=request.request.request_id,
                session_id=request.request.session_id,
                agent=AgentIdentity(
                    agent_id=agent.agent_id,
                    agent_version=agent.version,
                    deployment_id=request.deployment_id,
                ),
                output=AgentOutput(
                    status=OutputStatus.FAILED,
                    text=ResponseText(display="Agent runtime failed", tts="Agent runtime failed"),
                ),
                trace=ResponseTrace(
                    run_id=run_id,
                    route_reason=request.route_reason,
                    latency_ms=latency_ms,
                    error=error.code,
                ),
                error=error,
            )
            self._record_run(
                request=request,
                run_id=run_id,
                backend_name=backend_name,
                status=AgentRunStatus.FAILED,
                latency_ms=latency_ms,
                response=failed_response,
            )
            return RuntimeResponse(response=failed_response)

        latency_ms = self._latency_ms(started)
        trace = response.response.trace or ResponseTrace()
        trace.run_id = trace.run_id or run_id
        trace.route_reason = trace.route_reason or request.route_reason
        trace.latency_ms = latency_ms
        response.response.trace = trace
        self._record_run(
            request=request,
            run_id=trace.run_id,
            backend_name=backend_name,
            status=AgentRunStatus.SUCCEEDED,
            latency_ms=latency_ms,
            response=response.response,
        )
        return response

    @staticmethod
    def _latency_ms(started: float) -> int:
        return max(0, round((perf_counter() - started) * 1000))

    def _record_run(
        self,
        *,
        request: RuntimeRequest,
        run_id: str,
        backend_name: str,
        status: AgentRunStatus,
        latency_ms: int,
        response: AgentResponse,
    ) -> None:
        trace = response.trace or ResponseTrace()
        self.run_store.record(
            AgentRun(
                run_id=run_id,
                request_id=response.request_id,
                session_id=response.session_id,
                agent_id=request.agent_spec.agent_id,
                agent_version=request.agent_spec.version,
                route_reason=trace.route_reason,
                runtime_backend=backend_name,
                status=status,
                latency_ms=latency_ms,
                tool_calls=trace.tool_calls,
                error=response.error,
                metadata={"debug": request.request.options.debug},
            )
        )
