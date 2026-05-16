from __future__ import annotations

import pytest

from agent_platform.devflow.runner.adapters.mock import MockRunnerAdapter
from agent_platform.devflow.task_pack import (
    DevelopmentTask,
    RepositoryTarget,
    RequirementSpec,
    TaskMetadata,
)


def _make_task(*, required_outputs: list[str] | None = None) -> DevelopmentTask:
    return DevelopmentTask(
        metadata=TaskMetadata(task_id="t1", title="test", type="platform:change", source={}),
        repository=RepositoryTarget(project_id="p1", work_branch="feat/t1", base_branch="main"),
        requirement=RequirementSpec(background="bg"),
        implementation={"required_outputs": required_outputs or ["src/new_file.py"]},
    )


class TestMockRunnerAdapter:
    @pytest.mark.asyncio
    async def test_success(self, tmp_path):
        adapter = MockRunnerAdapter()
        task = _make_task(required_outputs=["output.txt"])
        result = await adapter.execute(workspace_dir=str(tmp_path), task=task)
        assert result.success
        assert result.exit_code == 0
        assert "output.txt" in result.changed_files
        assert (tmp_path / "output.txt").exists()

    @pytest.mark.asyncio
    async def test_failure(self, tmp_path):
        adapter = MockRunnerAdapter(should_fail=True)
        task = _make_task()
        result = await adapter.execute(workspace_dir=str(tmp_path), task=task)
        assert not result.success
        assert result.exit_code == 1
        assert result.error_message is not None

    @pytest.mark.asyncio
    async def test_skips_non_path_outputs(self, tmp_path):
        adapter = MockRunnerAdapter()
        task = _make_task(required_outputs=["docs update if contract changes", "src/real.py"])
        result = await adapter.execute(workspace_dir=str(tmp_path), task=task)
        assert result.success
        assert "src/real.py" in result.changed_files
        assert "docs update if contract changes" not in result.changed_files

    @pytest.mark.asyncio
    async def test_creates_nested_dirs(self, tmp_path):
        adapter = MockRunnerAdapter()
        task = _make_task(required_outputs=["a/b/c/deep.py"])
        result = await adapter.execute(workspace_dir=str(tmp_path), task=task)
        assert result.success
        assert (tmp_path / "a" / "b" / "c" / "deep.py").exists()

    def test_adapter_type(self):
        assert MockRunnerAdapter().adapter_type == "mock"

    @pytest.mark.asyncio
    async def test_health_check(self):
        assert await MockRunnerAdapter().health_check() is True

    @pytest.mark.asyncio
    async def test_cancel(self):
        adapter = MockRunnerAdapter()
        await adapter.cancel()
        assert adapter._cancelled is True
