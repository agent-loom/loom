from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agent_platform.domain.models import ToolCallTrace


@dataclass
class AgentTask:
    task_id: str
    query: str
    intent: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RouteScore:
    worker_name: str
    score: float
    reason: str = ""


@dataclass
class WorkerResult:
    worker_name: str
    display: str
    data: dict[str, Any] = field(default_factory=dict)
    status: str = "completed"
    commands: list[dict[str, Any]] = field(default_factory=list)
    tool_traces: list[ToolCallTrace] = field(default_factory=list)


@runtime_checkable
class AgentWorker(Protocol):
    name: str

    def can_handle(self, task: AgentTask) -> RouteScore: ...

    async def run(self, task: AgentTask) -> WorkerResult: ...
