"""Tests for real runner adapters: ClaudeCodeAdapter, CodexAdapter, MockRunnerAdapter.

Covers protocol compliance, prompt building, secret env stripping, timeout,
cancel, and health_check without requiring actual CLI binaries.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_platform.devflow.runner.adapters.claude_code import (
    ClaudeCodeAdapter,
)
from agent_platform.devflow.runner.adapters.codex import CodexAdapter
from agent_platform.devflow.runner.adapters.mock import MockRunnerAdapter
from agent_platform.devflow.runner.adapters.utils import build_safe_env
from agent_platform.devflow.runner.protocol import RunnerAdapter
from agent_platform.devflow.task_pack import (
    DevelopmentTask,
    RepositoryTarget,
    RequirementSpec,
    TaskMetadata,
)


def _make_task(**overrides) -> DevelopmentTask:
    defaults = dict(
        metadata=TaskMetadata(
            task_id="t-1", title="Test Task", type="platform:change", source={},
        ),
        repository=RepositoryTarget(
            project_id="proj-1",
            work_branch="feat/t-1",
        ),
        requirement=RequirementSpec(
            background="Build a feature",
            user_scenarios=["user does X"],
            acceptance=["feature works"],
            non_goals=["do not break prod"],
        ),
        scope={"write_allowed": ["src/**"], "write_denied": [".env", "secrets/**"]},
        implementation={
            "required_outputs": ["src/main.py"],
            "constraints": ["no secrets"],
        },
        validation={"commands": ["pytest tests/unit"]},
    )
    defaults.update(overrides)
    return DevelopmentTask(**defaults)


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    def test_claude_code_adapter_implements_protocol(self):
        adapter = ClaudeCodeAdapter()
        assert isinstance(adapter, RunnerAdapter)

    def test_codex_adapter_implements_protocol(self):
        adapter = CodexAdapter()
        assert isinstance(adapter, RunnerAdapter)

    def test_mock_adapter_implements_protocol(self):
        adapter = MockRunnerAdapter()
        assert isinstance(adapter, RunnerAdapter)

    def test_claude_code_adapter_type(self):
        assert ClaudeCodeAdapter().adapter_type == "claude_code"

    def test_codex_adapter_type(self):
        assert CodexAdapter().adapter_type == "codex"

    def test_mock_adapter_type(self):
        assert MockRunnerAdapter().adapter_type == "mock"


# ---------------------------------------------------------------------------
# Secret env stripping
# ---------------------------------------------------------------------------


class TestSafeEnv:
    def test_strips_secret_keywords(self):
        test_env = {
            "PATH": "/usr/bin",
            "HOME": "/home/user",
            "PLANE_API_KEY": "secret-plane",
            "GITLAB_TOKEN": "secret-gitlab",
            "MY_SECRET_VAR": "secret-val",
            "DB_PASSWORD": "secret-db",
            "AWS_CREDENTIAL_FILE": "/creds",
            "LANG": "en_US.UTF-8",
        }
        with patch.dict(os.environ, test_env, clear=True):
            safe = build_safe_env()
            assert "PATH" in safe
            assert "HOME" in safe
            assert "LANG" in safe
            assert "PLANE_API_KEY" not in safe
            assert "GITLAB_TOKEN" not in safe
            assert "MY_SECRET_VAR" not in safe
            assert "DB_PASSWORD" not in safe
            assert "AWS_CREDENTIAL_FILE" not in safe

    def test_only_whitelisted_vars(self):
        test_env = {"RANDOM_VAR": "val", "PATH": "/bin"}
        with patch.dict(os.environ, test_env, clear=True):
            safe = build_safe_env()
            assert "RANDOM_VAR" not in safe
            assert "PATH" in safe


# ---------------------------------------------------------------------------
# ClaudeCodeAdapter prompt building
# ---------------------------------------------------------------------------


class TestClaudeCodePrompt:
    def test_prompt_contains_task_title(self):
        adapter = ClaudeCodeAdapter()
        task = _make_task()
        prompt = adapter._build_prompt(task)
        assert "Test Task" in prompt

    def test_prompt_contains_scope(self):
        adapter = ClaudeCodeAdapter()
        task = _make_task()
        prompt = adapter._build_prompt(task)
        assert "src/**" in prompt
        assert ".env" in prompt

    def test_prompt_contains_acceptance(self):
        adapter = ClaudeCodeAdapter()
        task = _make_task()
        prompt = adapter._build_prompt(task)
        assert "feature works" in prompt

    def test_prompt_contains_non_goals(self):
        adapter = ClaudeCodeAdapter()
        task = _make_task()
        prompt = adapter._build_prompt(task)
        assert "do not break prod" in prompt

    def test_prompt_contains_validation_commands(self):
        adapter = ClaudeCodeAdapter()
        task = _make_task()
        prompt = adapter._build_prompt(task)
        assert "pytest tests/unit" in prompt

    def test_prompt_contains_user_scenarios(self):
        adapter = ClaudeCodeAdapter()
        task = _make_task()
        prompt = adapter._build_prompt(task)
        assert "user does X" in prompt


# ---------------------------------------------------------------------------
# CodexAdapter prompt building
# ---------------------------------------------------------------------------


class TestCodexPrompt:
    def test_prompt_contains_task_title(self):
        adapter = CodexAdapter()
        task = _make_task()
        prompt = adapter._build_prompt(task)
        assert "Test Task" in prompt

    def test_prompt_contains_scope(self):
        adapter = CodexAdapter()
        task = _make_task()
        prompt = adapter._build_prompt(task)
        assert "src/**" in prompt

    def test_prompt_contains_acceptance(self):
        adapter = CodexAdapter()
        task = _make_task()
        prompt = adapter._build_prompt(task)
        assert "feature works" in prompt

    def test_prompt_contains_non_goals(self):
        adapter = CodexAdapter()
        task = _make_task()
        prompt = adapter._build_prompt(task)
        assert "do not break prod" in prompt


# ---------------------------------------------------------------------------
# ClaudeCodeAdapter execution (subprocess mocked)
# ---------------------------------------------------------------------------


class TestClaudeCodeExecution:
    @pytest.mark.asyncio
    async def test_successful_execution(self):
        adapter = ClaudeCodeAdapter(cli_path="claude", max_turns=10)
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b'{"result": "ok"}', b"")
        )
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await adapter.execute(
                workspace_dir="/tmp/ws",
                task=_make_task(),
                timeout_seconds=30,
            )

        assert result.exit_code == 0
        assert result.stdout == '{"result": "ok"}'
        assert result.stderr == ""

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self):
        adapter = ClaudeCodeAdapter()
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError)
        mock_proc.returncode = None
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock(side_effect=TimeoutError)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch("asyncio.wait_for", side_effect=TimeoutError):
                result = await adapter.execute(
                    workspace_dir="/tmp/ws",
                    task=_make_task(),
                    timeout_seconds=1,
                )

        assert result.exit_code == -1
        assert "timed out" in result.error_message

    @pytest.mark.asyncio
    async def test_nonzero_exit_code(self):
        adapter = ClaudeCodeAdapter()
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b"error: something went wrong")
        )
        mock_proc.returncode = 1

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await adapter.execute(
                workspace_dir="/tmp/ws",
                task=_make_task(),
            )

        assert result.exit_code == 1
        assert "something went wrong" in result.stderr

    @pytest.mark.asyncio
    async def test_model_flag_passed(self):
        adapter = ClaudeCodeAdapter(model="claude-sonnet-4-6")
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await adapter.execute(workspace_dir="/tmp/ws", task=_make_task())
            call_args = mock_exec.call_args[0]
            assert "--model" in call_args
            assert "claude-sonnet-4-6" in call_args

    @pytest.mark.asyncio
    async def test_health_check_succeeds(self):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock()

        adapter = ClaudeCodeAdapter()
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            assert await adapter.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_fails_on_missing_cli(self):
        adapter = ClaudeCodeAdapter(cli_path="/nonexistent/claude")
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError,
        ):
            assert await adapter.health_check() is False


# ---------------------------------------------------------------------------
# CodexAdapter execution (subprocess mocked)
# ---------------------------------------------------------------------------


class TestCodexExecution:
    @pytest.mark.asyncio
    async def test_successful_execution(self):
        adapter = CodexAdapter()
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"output", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await adapter.execute(
                workspace_dir="/tmp/ws",
                task=_make_task(),
            )

        assert result.exit_code == 0
        assert result.stdout == "output"

    @pytest.mark.asyncio
    async def test_codex_passes_full_auto_flag(self):
        adapter = CodexAdapter()
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await adapter.execute(workspace_dir="/tmp/ws", task=_make_task())
            call_args = mock_exec.call_args[0]
            assert "--approval-mode" in call_args
            assert "full-auto" in call_args

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self):
        adapter = CodexAdapter()
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError)
        mock_proc.returncode = None
        mock_proc.terminate = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch("asyncio.wait_for", side_effect=TimeoutError):
                result = await adapter.execute(
                    workspace_dir="/tmp/ws",
                    task=_make_task(),
                    timeout_seconds=1,
                )

        assert result.exit_code == -1
        assert "timed out" in result.error_message

    @pytest.mark.asyncio
    async def test_health_check_succeeds(self):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock()

        adapter = CodexAdapter()
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            assert await adapter.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_fails(self):
        adapter = CodexAdapter(cli_path="/nonexistent/codex")
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError,
        ):
            assert await adapter.health_check() is False

    @pytest.mark.asyncio
    async def test_cancel_terminates_process(self):
        adapter = CodexAdapter()
        mock_proc = AsyncMock()
        mock_proc.returncode = None
        mock_proc.terminate = MagicMock()
        mock_proc.wait = AsyncMock()
        mock_proc.kill = MagicMock()
        adapter._process = mock_proc

        await adapter.cancel()
        mock_proc.terminate.assert_called_once()


# ---------------------------------------------------------------------------
# MockRunnerAdapter
# ---------------------------------------------------------------------------


class TestMockAdapter:
    @pytest.mark.asyncio
    async def test_success_creates_files(self, tmp_path):
        adapter = MockRunnerAdapter()
        task = _make_task()
        result = await adapter.execute(
            workspace_dir=str(tmp_path),
            task=task,
        )
        assert result.exit_code == 0
        assert result.success

    @pytest.mark.asyncio
    async def test_failure_mode(self):
        adapter = MockRunnerAdapter(should_fail=True)
        result = await adapter.execute(
            workspace_dir="/tmp/ws",
            task=_make_task(),
        )
        assert result.exit_code == 1
        assert not result.success
        assert "fail" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_health_check_always_true(self):
        adapter = MockRunnerAdapter()
        assert await adapter.health_check() is True

    @pytest.mark.asyncio
    async def test_cancel_sets_flag(self):
        adapter = MockRunnerAdapter()
        await adapter.cancel()
        assert adapter._cancelled is True
