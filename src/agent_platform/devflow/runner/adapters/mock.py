"""Mock 适配器，用于测试场景的虚拟 Runner。"""

from __future__ import annotations

from pathlib import Path

from agent_platform.devflow.runner.protocol import RunnerAdapterResult
from agent_platform.devflow.task_pack import DevelopmentTask


class MockRunnerAdapter:
    """用于测试的模拟 Runner 适配器。"""

    def __init__(self, *, should_fail: bool = False):
        """初始化 Mock 适配器。"""
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
        """模拟执行编码任务，根据配置返回成功或失败。"""
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
        """标记任务为已取消。"""
        self._cancelled = True

    async def health_check(self) -> bool:
        """始终返回健康状态。"""
        return True
