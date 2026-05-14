from __future__ import annotations

from typing import Protocol

from agent_platform.domain.models import AgentRun


class RunStore(Protocol):
    def record(self, run: AgentRun) -> None:
        ...

    def list_runs(self) -> list[AgentRun]:
        ...

    def get(self, run_id: str) -> AgentRun | None:
        ...


class InMemoryRunStore:
    def __init__(self) -> None:
        self._runs: list[AgentRun] = []

    def record(self, run: AgentRun) -> None:
        self._runs.append(run)

    def list_runs(self) -> list[AgentRun]:
        return list(self._runs)

    def get(self, run_id: str) -> AgentRun | None:
        return next((run for run in self._runs if run.run_id == run_id), None)
