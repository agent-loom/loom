"""Worker 编排器，包含多种 Worker 实现和基于评分的路由机制。"""

from __future__ import annotations

import logging

from agent_platform.domain.models import (
    AgentIdentity,
    AgentOutput,
    AgentResponse,
    OutputStatus,
    ResponseCommand,
    ResponseText,
    ResponseTrace,
    RuntimeRequest,
    RuntimeResponse,
)
from agent_platform.runtime.worker import AgentTask, AgentWorker, RouteScore, WorkerResult
from agent_platform.tools.executor import ToolExecutor

logger = logging.getLogger(__name__)


class DirectReplyWorker:
    """兜底 Worker，返回简单的文本直接回复。"""

    name = "direct_reply"

    def can_handle(self, task: AgentTask) -> RouteScore:
        """返回最低优先级评分作为兜底。"""
        return RouteScore(worker_name=self.name, score=0.1, reason="fallback")

    async def run(self, task: AgentTask) -> WorkerResult:
        """生成直接回复文本。"""
        prompt_path = task.metadata.get("direct_reply_prompt")
        if prompt_path:
            from pathlib import Path
            p = Path(prompt_path)
            if p.is_file():
                template = p.read_text(encoding="utf-8")
                display = f"{template[:200]}..."
            else:
                display = f"收到您的问题：{task.query}"
        else:
            display = f"收到您的问题：{task.query}"
        return WorkerResult(
            worker_name=self.name,
            display=display,
            data={},
        )


class HandoffWorker:
    """转人工 Worker，处理用户的人工客服转接请求。"""

    name = "handoff"

    def __init__(self, handoff_intents: list[str] | None = None):
        """初始化转人工 Worker，配置触发意图关键词。"""
        self.handoff_intents = handoff_intents or ["转人工"]

    def can_handle(self, task: AgentTask) -> RouteScore:
        """检测用户消息中是否包含转人工关键词。"""
        for intent in self.handoff_intents:
            if intent in task.query:
                return RouteScore(worker_name=self.name, score=1.0, reason="handoff_keyword")
        return RouteScore(worker_name=self.name, score=0.0, reason="no_match")

    async def run(self, task: AgentTask) -> WorkerResult:
        """执行转人工流程，返回转接提示和指令。"""
        return WorkerResult(
            worker_name=self.name,
            display="正在为您转接人工客服，请稍候...",
            status="handoff_required",
            commands=[{"name": "human.handoff", "data": {"reason": task.query}}],
        )


class ToolWorker:
    """基于关键词匹配的工具调用 Worker。"""

    def __init__(
        self,
        name: str,
        tool_name: str,
        keywords: list[str],
        tool_executor: ToolExecutor | None = None,
    ):
        """初始化工具 Worker，配置关键词和工具执行器。"""
        self.name = name
        self.tool_name = tool_name
        self.keywords = keywords
        self.tool_executor = tool_executor

    def can_handle(self, task: AgentTask) -> RouteScore:
        """根据关键词匹配比例计算路由评分。"""
        matches = sum(1 for kw in self.keywords if kw in task.query)
        if not self.keywords:
            return RouteScore(worker_name=self.name, score=0.0, reason="no_keywords")
        score = matches / len(self.keywords)
        return RouteScore(
            worker_name=self.name,
            score=score,
            reason=f"matched {matches}/{len(self.keywords)} keywords",
        )

    async def run(self, task: AgentTask) -> WorkerResult:
        """执行关联工具并返回结果。"""
        if self.tool_executor:
            result = await self.tool_executor.execute(
                self.tool_name,
                {"query": task.query},
                allowed_tools=task.metadata.get("allowed_tools", [self.tool_name]),
                timeout_ms=task.metadata.get("timeout_ms", 3000),
            )
            return WorkerResult(
                worker_name=self.name,
                display=result.output.get("summary", str(result.output)),
                data=result.output,
                tool_traces=[result.trace],
            )
        return WorkerResult(
            worker_name=self.name,
            display=f"[{self.name}] Would call {self.tool_name} for: {task.query}",
            data={"tool": self.tool_name},
        )


class WorkerOrchestrator:
    """Worker 编排器，基于评分路由将任务分发给最佳 Worker。

    当没有 Worker 评分超过阈值时回退到默认 Worker。
    """

    def __init__(self, default_worker_name: str = "direct_reply"):
        """初始化编排器，指定默认回退 Worker 名称。"""
        self._workers: dict[str, AgentWorker] = {}
        self._default_worker_name = default_worker_name

    def register(self, worker: AgentWorker) -> None:
        """注册一个 Worker 到编排器。"""
        self._workers[worker.name] = worker

    async def route_and_run(self, request: RuntimeRequest) -> RuntimeResponse:
        """评分路由请求到最佳 Worker 并执行，返回运行时响应。"""
        spec = request.agent_spec
        query = request.request.input.query

        task = AgentTask(
            task_id=request.request.request_id or "",
            query=query,
            intent="",
            metadata={
                "allowed_tools": spec.manifest.tools.allow,
                "timeout_ms": spec.manifest.tools.timeout_ms,
                "direct_reply_prompt": str(
                    spec.package_path
                    / spec.manifest.prompts.get("direct_reply", "")
                ),
            },
        )

        best_worker = None
        best_score = RouteScore(worker_name="", score=0.0, reason="")

        for worker in self._workers.values():
            score = worker.can_handle(task)
            if score.score > best_score.score:
                best_score = score
                best_worker = worker

        if best_worker is None or best_score.score < 0.1:
            best_worker = self._workers.get(self._default_worker_name)
            if best_worker is None:
                best_worker = DirectReplyWorker()
            best_score = RouteScore(
                worker_name=best_worker.name,
                score=0.0,
                reason="default_fallback",
            )

        logger.info(
            "routing to worker %s (score=%.2f, reason=%s)",
            best_worker.name,
            best_score.score,
            best_score.reason,
        )
        result = await best_worker.run(task)

        status = OutputStatus.COMPLETED
        if result.status == "handoff_required":
            status = OutputStatus.HANDOFF_REQUIRED

        command_allowlist = spec.manifest.output.command_allowlist
        commands = []
        for cmd in (result.commands or []):
            if command_allowlist and cmd.get("name") not in command_allowlist:
                continue
            commands.append(ResponseCommand(**cmd))

        tool_traces = result.tool_traces or []

        response = AgentResponse(
            request_id=request.request.request_id,
            session_id=request.request.session_id,
            agent=AgentIdentity(
                agent_id=spec.agent_id,
                agent_version=spec.version,
                deployment_id=request.deployment_id,
            ),
            output=AgentOutput(
                status=status,
                text=ResponseText(display=result.display, tts=result.display),
                commands=commands,
            ),
            trace=ResponseTrace(
                route_reason=f"worker:{best_worker.name}({best_score.reason})",
                tool_calls=tool_traces,
            ),
            debug={
                "worker": best_worker.name,
                "score": best_score.score,
                "reason": best_score.reason,
            },
        )
        return RuntimeResponse(response=response)
