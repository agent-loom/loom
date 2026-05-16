"""Worker 抽象层，定义任务、路由评分和工作单元协议。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agent_platform.domain.models import ToolCallTrace


@dataclass
class AgentTask:
    """封装一次 Agent 处理任务的输入信息。"""

    task_id: str
    query: str
    intent: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RouteScore:
    """Worker 路由评分结果。"""

    worker_name: str
    score: float
    reason: str = ""


@dataclass
class WorkerResult:
    """Worker 执行结果，包含展示文本和工具追踪。"""

    worker_name: str
    display: str
    data: dict[str, Any] = field(default_factory=dict)
    status: str = "completed"
    commands: list[dict[str, Any]] = field(default_factory=list)
    tool_traces: list[ToolCallTrace] = field(default_factory=list)


@runtime_checkable
class AgentWorker(Protocol):
    """Agent Worker 协议，定义任务匹配和执行接口。"""

    name: str

    def can_handle(self, task: AgentTask) -> RouteScore:
        """评估当前 Worker 处理该任务的匹配度。"""
        ...

    async def run(self, task: AgentTask) -> WorkerResult:
        """执行任务并返回结果。"""
        ...
