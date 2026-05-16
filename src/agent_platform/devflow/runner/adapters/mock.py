from __future__ import annotations

from pathlib import Path

from agent_platform.devflow.runner.protocol import RunnerAdapterResult
from agent_platform.devflow.task_pack import DevelopmentTask


class MockRunnerAdapter:

    def __init__(self, *, should_fail: bool = False):
        self.should_fail = should_fail
        self._cancelled = False

    @property
    def adapter_type(self) -> str:
        return "mock"

    async def execute(
        self,
        *,
        workspace_dir: str,
        task: DevelopmentTask,
        timeout_seconds: int = 600,
    ) -> RunnerAdapterResult:
        if self.should_fail:
            return RunnerAdapterResult(
                exit_code=1,
                error_message="Mock adapter configured to fail",
            )

        ws = Path(workspace_dir)
        changed: list[str] = []
        for output in task.implementation.get("required_outputs", []):
            if " " in output or not output.strip():
                continue
            path = ws / output
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text(f"# Generated for {task.metadata.task_id}\n")
                changed.append(output)

        return RunnerAdapterResult(
            exit_code=0,
            changed_files=changed,
            stdout="Mock adapter completed successfully",
        )

    async def cancel(self) -> None:
        self._cancelled = True

    async def health_check(self) -> bool:
        return True
