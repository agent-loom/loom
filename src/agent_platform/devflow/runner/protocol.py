from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_platform.devflow.task_pack import DevelopmentTask


@runtime_checkable
class RunnerAdapter(Protocol):

    @property
    def adapter_type(self) -> str: ...

    async def execute(
        self,
        *,
        workspace_dir: str,
        task: DevelopmentTask,
        timeout_seconds: int = 600,
    ) -> RunnerAdapterResult: ...

    async def cancel(self) -> None: ...

    async def health_check(self) -> bool: ...


class RunnerAdapterResult:

    def __init__(
        self,
        *,
        exit_code: int,
        changed_files: list[str] | None = None,
        stdout: str = "",
        stderr: str = "",
        error_message: str | None = None,
    ):
        self.exit_code = exit_code
        self.changed_files = changed_files or []
        self.stdout = stdout
        self.stderr = stderr
        self.error_message = error_message

    @property
    def success(self) -> bool:
        return self.exit_code == 0
