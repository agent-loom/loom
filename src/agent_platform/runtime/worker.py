from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class AgentTask:
    intent: str
    query: str
    context: dict[str, Any]
    parameters: dict[str, Any]


@dataclass
class RouteScore:
    score: float
    worker_name: str
    reason: str


@dataclass
class WorkerResult:
    status: str
    output: dict[str, Any]
    tool_calls: list[dict[str, Any]]
    metadata: dict[str, Any]


class AgentWorker(Protocol):
    name: str

    def can_handle(self, task: AgentTask) -> RouteScore:
        ...

    async def run(self, task: AgentTask) -> WorkerResult:
        ...
