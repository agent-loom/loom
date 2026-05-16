"""Agent 运行记录的存储协议与内存实现。"""

from __future__ import annotations

from typing import Protocol

from agent_platform.domain.models import AgentRun


class RunStore(Protocol):
    """运行记录存储协议，定义记录、列举和查询接口。"""

    def record(self, run: AgentRun) -> None:
        """保存一条运行记录。"""
        ...

    def list_runs(self) -> list[AgentRun]:
        """返回所有运行记录。"""
        ...

    def get(self, run_id: str) -> AgentRun | None:
        """按 run_id 查询运行记录，不存在返回 None。"""
        ...


class InMemoryRunStore:
    """基于内存列表的 RunStore 实现。"""

    def __init__(self) -> None:
        """初始化空的运行记录列表。"""
        self._runs: list[AgentRun] = []

    def record(self, run: AgentRun) -> None:
        """追加一条运行记录到内存列表。"""
        self._runs.append(run)

    def list_runs(self) -> list[AgentRun]:
        """返回所有运行记录的副本。"""
        return list(self._runs)

    def get(self, run_id: str) -> AgentRun | None:
        """按 run_id 查询运行记录。"""
        return next((run for run in self._runs if run.run_id == run_id), None)
