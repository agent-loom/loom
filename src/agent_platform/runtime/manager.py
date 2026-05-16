import asyncio
import logging
from time import perf_counter
from typing import Any
from uuid import uuid4

from agent_platform.domain.models import (
    AgentError,
    AgentIdentity,
    AgentOutput,
    AgentResponse,
    AgentRun,
    AgentRunStatus,
    AgentSession,
    OutputStatus,
    ResponseText,
    ResponseTrace,
    RuntimeRequest,
    RuntimeResponse,
)
from agent_platform.observability.sanitizer import TraceSanitizer
from agent_platform.persistence.memory import (
    InMemoryAgentRunRepository,
    InMemoryAgentSessionRepository,
)
from agent_platform.persistence.repositories import (
    AgentRunRepository,
    AgentSessionRepository,
)
from agent_platform.runtime.hermes import HermesRuntimeBackend
from agent_platform.runtime.langgraph import LangGraphRuntimeBackend
from agent_platform.runtime.model_gateway import ModelGateway
from agent_platform.runtime.native import NativeRuntimeBackend
from agent_platform.tools.executor import ToolExecutor

logger = logging.getLogger(__name__)


class RuntimeManager:
    """Agent 运行时管理器。
    
    负责管理不同运行时后端（如 Native, Hermes, LangGraph）的生命周期，
    处理请求的策略检查、会话管理、Hooks 触发和指标收集，并将请求路由到对应的后端。
    """
    def __init__(
        self,
        run_store: AgentRunRepository | None = None,
        session_store: AgentSessionRepository | None = None,
        policy_engine: Any | None = None,
        hook_registry: Any | None = None,
        metrics_collector: Any | None = None,
        model_gateway: ModelGateway | None = None,
        tool_executor: ToolExecutor | None = None,
        knowledge_service: Any | None = None,
    ):
        self._backends = {
            NativeRuntimeBackend.name: NativeRuntimeBackend(tool_executor=tool_executor),
            HermesRuntimeBackend.name: HermesRuntimeBackend(
                model_gateway=model_gateway,
                tool_executor=tool_executor,
            ),
            LangGraphRuntimeBackend.name: LangGraphRuntimeBackend(tool_executor=tool_executor),
        }
        self.run_store = run_store or InMemoryAgentRunRepository()
        self.session_store: AgentSessionRepository = (
            session_store or InMemoryAgentSessionRepository()
        )
        self.policy_engine = policy_engine
        self.hook_registry = hook_registry
        self.metrics_collector = metrics_collector

    def register(self, backend) -> None:
        """注册一个新的运行时后端。"""
        self._backends[backend.name] = backend

    async def run(self, request: RuntimeRequest) -> RuntimeResponse:
        """执行运行时请求。
        
        该方法涵盖了完整的请求生命周期，包括：
        1. 检查输入策略。
        2. 触发相关 Hooks。
        3. 加载并更新会话信息。
        4. 调用指定的运行时后端处理请求。
        5. 检查输出策略并保存运行结果。
        """
        run_id = f"run_{uuid4().hex}"
        started = perf_counter()
        backend_name = request.agent_spec.manifest.runtime.backend
        try:
            backend = self._backends[backend_name]
        except KeyError as exc:
            raise ValueError(f"runtime backend not registered: {backend_name}") from exc

        # 策略检查：校验输入 (check_input)
        if self.policy_engine:
            policy_set = self.policy_engine.load_policies(request.agent_spec)
            violations = self.policy_engine.check_input(
                request.request.input.query, policy_set
            )
            if violations:
                error = AgentError(
                    code="INPUT_POLICY_VIOLATION",
                    message="; ".join(v.message for v in violations),
                    retryable=False,
                )
                latency_ms = self._latency_ms(started)
                return await self._build_error_response(
                    request, run_id, backend_name, latency_ms, error,
                )

        # 钩子触发：路由后触发 (on_route)
        if self.hook_registry:
            try:
                await self.hook_registry.emit(
                    "on_route", {"backend": backend_name, "run_id": run_id},
                )
            except Exception:
                logger.exception("hook on_route failed")

        # 钩子触发：运行前触发 (pre_run)
        if self.hook_registry:
            try:
                await self.hook_registry.emit("pre_run", {"request": request, "run_id": run_id})
            except Exception:
                logger.exception("hook pre_run failed")

        session = await self._load_session(request)
        if session:
            session.add_message("user", request.request.input.query)

        timeout_ms = request.agent_spec.manifest.runtime.timeout_ms
        timeout_sec = timeout_ms / 1000.0

        try:
            response = await asyncio.wait_for(backend.run(request), timeout=timeout_sec)
        except TimeoutError:
            latency_ms = self._latency_ms(started)
            error = AgentError(
                code="RUNTIME_TIMEOUT",
                message=f"agent runtime timed out after {timeout_ms}ms",
                retryable=True,
            )
            if self.hook_registry:
                try:
                    await self.hook_registry.emit("on_error", {"error": error, "run_id": run_id})
                except Exception:
                    logger.exception("hook on_error failed")
            if self.metrics_collector:
                try:
                    self.metrics_collector.record_request(request.agent_spec.agent_id, "failed")
                except Exception:
                    logger.exception("metrics record_request failed")
            return await self._build_error_response(
                request, run_id, backend_name, latency_ms, error,
            )
        except Exception as exc:
            latency_ms = self._latency_ms(started)
            error = AgentError(
                code="RUNTIME_ERROR",
                message=str(exc),
                retryable=False,
            )
            if self.hook_registry:
                try:
                    await self.hook_registry.emit("on_error", {"error": error, "run_id": run_id})
                except Exception:
                    logger.exception("hook on_error failed")
            if self.metrics_collector:
                try:
                    self.metrics_collector.record_request(request.agent_spec.agent_id, "failed")
                except Exception:
                    logger.exception("metrics record_request failed")
            return await self._build_error_response(
                request, run_id, backend_name, latency_ms, error,
            )

        latency_ms = self._latency_ms(started)
        trace = response.response.trace or ResponseTrace()
        trace.run_id = trace.run_id or run_id
        trace.route_reason = trace.route_reason or request.route_reason
        if trace.traffic_bucket is None:
            trace.traffic_bucket = request.traffic_bucket
        trace.latency_ms = latency_ms
        response.response.trace = trace

        # 策略检查：校验输出 (check_output)
        if self.policy_engine:
            policy_set = self.policy_engine.load_policies(request.agent_spec)
            output_violations = self.policy_engine.check_output(
                response.response.output.text.display, policy_set
            )
            if output_violations:
                logger.warning("output policy violations: %s", output_violations)

        if session:
            display = response.response.output.text.display
            session.add_message("assistant", display)
            await self.session_store.save(session)
            if self.metrics_collector:
                try:
                    sessions = await self.session_store.list_sessions()
                    self.metrics_collector.set_active_sessions(len(sessions))
                except Exception:
                    logger.exception("metrics set_active_sessions failed")

        # 钩子触发：运行后触发 (post_run)
        if self.hook_registry:
            try:
                await self.hook_registry.emit("post_run", {"response": response, "run_id": run_id})
            except Exception:
                logger.exception("hook post_run failed")

        # 指标收集：记录成功请求
        if self.metrics_collector:
            try:
                agent_id = request.agent_spec.agent_id
                self.metrics_collector.record_request(agent_id, "success")
                self.metrics_collector.record_duration(agent_id, latency_ms / 1000.0)
            except Exception:
                logger.exception("metrics recording failed")

        await self._record_run(
            request=request,
            run_id=trace.run_id,
            backend_name=backend_name,
            status=AgentRunStatus.SUCCEEDED,
            latency_ms=latency_ms,
            response=response.response,
        )
        return response

    async def _load_session(self, request: RuntimeRequest) -> AgentSession | None:
        """从存储中加载当前请求对应的会话。如果不存在则创建一个新会话。"""
        session_id = request.request.session_id
        if not session_id:
            return None
        session = await self.session_store.load(session_id)
        if session is None:
            session = AgentSession(
                session_id=session_id,
                agent_id=request.agent_spec.agent_id,
                tenant_id=request.request.context.tenant.tenant_id,
                location_id=request.request.context.location.location_id,
                user_id=request.request.context.user.user_id,
                channel_id=request.request.context.channel.channel_id,
            )
        return session

    async def _build_error_response(
        self,
        request: RuntimeRequest,
        run_id: str,
        backend_name: str,
        latency_ms: int,
        error: AgentError,
    ) -> RuntimeResponse:
        """构建并返回一个表示运行失败的响应，并记录失败的运行状态。"""
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
                traffic_bucket=request.traffic_bucket,
                latency_ms=latency_ms,
                error=error.code,
            ),
            error=error,
        )
        await self._record_run(
            request=request,
            run_id=run_id,
            backend_name=backend_name,
            status=AgentRunStatus.FAILED,
            latency_ms=latency_ms,
            response=failed_response,
        )
        return RuntimeResponse(response=failed_response)

    @staticmethod
    def _latency_ms(started: float) -> int:
        """计算当前执行的延迟时间（毫秒）。"""
        return max(0, round((perf_counter() - started) * 1000))

    async def _record_run(
        self,
        *,
        request: RuntimeRequest,
        run_id: str,
        backend_name: str,
        status: AgentRunStatus,
        latency_ms: int,
        response: AgentResponse,
    ) -> None:
        """将当前 Agent 运行的结果和状态持久化记录到数据库或内存中。"""
        trace = response.trace or ResponseTrace()
        run = AgentRun(
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
        TraceSanitizer.sanitize_run(run)
        await self.run_store.record(run)
