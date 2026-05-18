"""Claude Code 和 Codex 适配器的单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_platform.devflow.runner.adapters.claude_code import (
    ClaudeCodeAdapter,
)
from agent_platform.devflow.runner.adapters.codex import CodexAdapter
from agent_platform.devflow.runner.adapters.utils import build_safe_env
from agent_platform.devflow.task_pack import (
    DevelopmentTask,
    RepositoryTarget,
    RequirementSpec,
    TaskMetadata,
)


def _make_task() -> DevelopmentTask:
    return DevelopmentTask(
        metadata=TaskMetadata(title="测试任务", task_id="T-001", type="feature"),
        repository=RepositoryTarget(project_id="123", work_branch="feat/test"),
        requirement=RequirementSpec(
            background="修复登录页面的 XSS 漏洞",
            acceptance=["输入被正确转义"],
            user_scenarios=["用户输入 <script> 标签时不执行"],
            non_goals=["不修改后端逻辑"],
        ),
        scope={"write_allowed": ["src/"], "write_denied": ["config/"]},
        implementation={"constraints": ["使用 DOMPurify"], "required_outputs": ["tests"]},
        validation={"commands": ["pytest tests/"]},
    )


class TestBuildSafeEnv:
    def test_strips_secrets(self):
        with patch.dict("os.environ", {
            "PATH": "/usr/bin",
            "PLANE_API_KEY": "secret123",
            "GITLAB_TOKEN": "tok",
            "MY_PASSWORD": "pass",
            "HOME": "/home/user",
        }, clear=True):
            env = build_safe_env()
            assert "PLANE_API_KEY" not in env
            assert "GITLAB_TOKEN" not in env
            assert "MY_PASSWORD" not in env
            assert env["PATH"] == "/usr/bin"
            assert env["HOME"] == "/home/user"


class TestClaudeCodeAdapter:
    def test_adapter_type(self):
        adapter = ClaudeCodeAdapter()
        assert adapter.adapter_type == "claude_code"

    def test_custom_params(self):
        adapter = ClaudeCodeAdapter(
            cli_path="/usr/local/bin/claude",
            max_turns=50,
            model="opus",
        )
        assert adapter.cli_path == "/usr/local/bin/claude"
        assert adapter.max_turns == 50
        assert adapter.model == "opus"

    @pytest.mark.asyncio
    async def test_execute_success(self):
        adapter = ClaudeCodeAdapter()
        task = _make_task()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"output", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await adapter.execute(
                workspace_dir="/tmp/test", task=task, timeout_seconds=10,
            )
        assert result.exit_code == 0
        assert result.stdout == "output"

    @pytest.mark.asyncio
    async def test_execute_timeout(self):
        adapter = ClaudeCodeAdapter()
        task = _make_task()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError)
        mock_proc.returncode = None
        mock_proc.terminate = MagicMock()
        mock_proc.wait = AsyncMock()
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await adapter.execute(
                workspace_dir="/tmp/test", task=task, timeout_seconds=1,
            )
        assert result.exit_code == -1
        assert "timed out" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_health_check_success(self):
        adapter = ClaudeCodeAdapter()
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            ok = await adapter.health_check()
        assert ok is True

    @pytest.mark.asyncio
    async def test_health_check_fails(self):
        adapter = ClaudeCodeAdapter()

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError,
        ):
            ok = await adapter.health_check()
        assert ok is False

    def test_build_prompt_contains_sections(self):
        adapter = ClaudeCodeAdapter()
        task = _make_task()
        prompt = adapter._build_prompt(task)
        assert "测试任务" in prompt
        assert "XSS" in prompt
        assert "允许修改的路径" in prompt
        assert "禁止修改的路径" in prompt
        assert "验收标准" in prompt
        assert "非目标" in prompt
        assert "pytest tests/" in prompt


class TestCodexAdapter:
    def test_adapter_type(self):
        adapter = CodexAdapter()
        assert adapter.adapter_type == "codex"

    @pytest.mark.asyncio
    async def test_execute_success(self):
        adapter = CodexAdapter()
        task = _make_task()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"done", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await adapter.execute(
                workspace_dir="/tmp/test", task=task, timeout_seconds=10,
            )
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_execute_timeout(self):
        adapter = CodexAdapter()
        task = _make_task()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError)
        mock_proc.returncode = None
        mock_proc.terminate = MagicMock()
        mock_proc.wait = AsyncMock()
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await adapter.execute(
                workspace_dir="/tmp/test", task=task, timeout_seconds=1,
            )
        assert result.exit_code == -1
        assert "timed out" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_health_check_fails(self):
        adapter = CodexAdapter()
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError,
        ):
            ok = await adapter.health_check()
        assert ok is False

    def test_build_prompt_compact(self):
        adapter = CodexAdapter()
        task = _make_task()
        prompt = adapter._build_prompt(task)
        assert "测试任务" in prompt
        assert "XSS" in prompt
        assert "非目标" in prompt
