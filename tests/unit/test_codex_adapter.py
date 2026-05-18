"""Codex 适配器沙箱模式配置化的单元测试。"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_platform.devflow.runner.adapters.codex import CodexAdapter
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


class TestCodexSandboxModeInit:
    """测试 CodexAdapter 初始化时 sandbox_mode 参数的校验。"""

    def test_default_sandbox_mode_is_bypass(self):
        adapter = CodexAdapter()
        assert adapter.sandbox_mode == "bypass"

    def test_set_docker_mode(self):
        adapter = CodexAdapter(sandbox_mode="docker")
        assert adapter.sandbox_mode == "docker"

    def test_custom_docker_image(self):
        adapter = CodexAdapter(sandbox_mode="docker", docker_image="my-codex:latest")
        assert adapter.docker_image == "my-codex:latest"

    def test_default_docker_image(self):
        adapter = CodexAdapter()
        assert adapter.docker_image == "codex-runner"

    def test_invalid_sandbox_mode_raises(self):
        with pytest.raises(ValueError, match="无效的 sandbox_mode"):
            CodexAdapter(sandbox_mode="invalid")


class TestBypassModeCmdGeneration:
    """测试 bypass 模式下生成的命令包含 --dangerously-bypass-approvals-and-sandbox。"""

    def test_bypass_cmd_contains_dangerous_flag(self):
        adapter = CodexAdapter(sandbox_mode="bypass")
        task = _make_task()
        prompt = adapter._build_prompt(task)
        cmd = adapter._build_cmd(prompt, "/tmp/workspace")
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        assert cmd[0] == "codex"
        assert "exec" in cmd

    def test_bypass_cmd_contains_skip_git_and_ephemeral(self):
        adapter = CodexAdapter(sandbox_mode="bypass")
        task = _make_task()
        prompt = adapter._build_prompt(task)
        cmd = adapter._build_cmd(prompt, "/tmp/workspace")
        assert "--skip-git-repo-check" in cmd
        assert "--ephemeral" in cmd

    def test_bypass_cmd_with_profile_and_model(self):
        adapter = CodexAdapter(
            sandbox_mode="bypass", profile="my-profile", model="o3-mini",
        )
        task = _make_task()
        prompt = adapter._build_prompt(task)
        cmd = adapter._build_cmd(prompt, "/tmp/workspace")
        assert "-c" in cmd
        assert "profile=my-profile" in cmd
        assert "--model" in cmd
        assert "o3-mini" in cmd


class TestDockerModeCmdGeneration:
    """测试 docker 模式下生成的命令以 docker run 开头且不含 --dangerously-bypass。"""

    def test_docker_cmd_starts_with_docker_run(self):
        adapter = CodexAdapter(sandbox_mode="docker")
        task = _make_task()
        prompt = adapter._build_prompt(task)
        cmd = adapter._build_cmd(prompt, "/tmp/workspace")
        assert cmd[0] == "docker"
        assert cmd[1] == "run"
        assert "--rm" in cmd

    def test_docker_cmd_no_dangerous_flag(self):
        adapter = CodexAdapter(sandbox_mode="docker")
        task = _make_task()
        prompt = adapter._build_prompt(task)
        cmd = adapter._build_cmd(prompt, "/tmp/workspace")
        assert "--dangerously-bypass-approvals-and-sandbox" not in cmd

    def test_docker_cmd_mounts_workspace(self):
        adapter = CodexAdapter(sandbox_mode="docker")
        task = _make_task()
        prompt = adapter._build_prompt(task)
        cmd = adapter._build_cmd(prompt, "/tmp/workspace")
        assert "-v" in cmd
        mount_idx = cmd.index("-v")
        assert cmd[mount_idx + 1] == "/tmp/workspace:/workspace"

    def test_docker_cmd_disables_network(self):
        adapter = CodexAdapter(sandbox_mode="docker")
        task = _make_task()
        prompt = adapter._build_prompt(task)
        cmd = adapter._build_cmd(prompt, "/tmp/workspace")
        assert "--network" in cmd
        net_idx = cmd.index("--network")
        assert cmd[net_idx + 1] == "none"

    def test_docker_cmd_has_resource_limits(self):
        adapter = CodexAdapter(sandbox_mode="docker")
        task = _make_task()
        prompt = adapter._build_prompt(task)
        cmd = adapter._build_cmd(prompt, "/tmp/workspace")
        assert "--memory" in cmd
        assert "--cpus" in cmd

    def test_docker_cmd_uses_custom_image(self):
        adapter = CodexAdapter(sandbox_mode="docker", docker_image="my-codex:v2")
        task = _make_task()
        prompt = adapter._build_prompt(task)
        cmd = adapter._build_cmd(prompt, "/tmp/workspace")
        assert "my-codex:v2" in cmd

    def test_docker_cmd_contains_exec_skip_git_ephemeral(self):
        adapter = CodexAdapter(sandbox_mode="docker")
        task = _make_task()
        prompt = adapter._build_prompt(task)
        cmd = adapter._build_cmd(prompt, "/tmp/workspace")
        assert "exec" in cmd
        assert "--skip-git-repo-check" in cmd
        assert "--ephemeral" in cmd


class TestBypassWarningOnlyOnce:
    """测试 bypass 模式的警告日志只输出一次。"""

    def test_warning_logged_once(self):
        adapter = CodexAdapter(sandbox_mode="bypass")
        task = _make_task()
        prompt = adapter._build_prompt(task)

        with patch(
            "agent_platform.devflow.runner.adapters.codex.logger"
        ) as mock_logger:
            # 第一次调用，应该有 warning
            adapter._build_cmd(prompt, "/tmp/workspace")
            assert mock_logger.warning.call_count == 1
            assert "bypass" in mock_logger.warning.call_args[0][0]

            # 第二次调用，不应该再有 warning
            adapter._build_cmd(prompt, "/tmp/workspace")
            assert mock_logger.warning.call_count == 1

            # 第三次调用，仍然不应该有 warning
            adapter._build_cmd(prompt, "/tmp/workspace")
            assert mock_logger.warning.call_count == 1

    def test_docker_mode_no_bypass_warning(self):
        adapter = CodexAdapter(sandbox_mode="docker")
        task = _make_task()
        prompt = adapter._build_prompt(task)

        with patch(
            "agent_platform.devflow.runner.adapters.codex.logger"
        ) as mock_logger:
            adapter._build_cmd(prompt, "/tmp/workspace")
            # docker 模式不应该有 bypass 相关的 warning
            mock_logger.warning.assert_not_called()


class TestExecuteWithSandboxMode:
    """测试 execute 方法在不同沙箱模式下的行为。"""

    @pytest.mark.asyncio
    async def test_execute_bypass_mode(self):
        adapter = CodexAdapter(sandbox_mode="bypass")
        task = _make_task()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"done", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await adapter.execute(
                workspace_dir="/tmp/test", task=task, timeout_seconds=10,
            )
        assert result.exit_code == 0
        # 验证传递给 subprocess 的命令包含 bypass 标志
        call_args = mock_exec.call_args
        cmd_parts = call_args[0]
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd_parts
        # bypass 模式下 cwd 应该是工作目录
        assert call_args[1]["cwd"] == "/tmp/test"

    @pytest.mark.asyncio
    async def test_execute_docker_mode(self):
        adapter = CodexAdapter(sandbox_mode="docker")
        task = _make_task()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"done", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await adapter.execute(
                workspace_dir="/tmp/test", task=task, timeout_seconds=10,
            )
        assert result.exit_code == 0
        # 验证传递给 subprocess 的命令以 docker 开头
        call_args = mock_exec.call_args
        cmd_parts = call_args[0]
        assert cmd_parts[0] == "docker"
        assert cmd_parts[1] == "run"
        assert "--dangerously-bypass-approvals-and-sandbox" not in cmd_parts
        # docker 模式下 cwd 应该是 None
        assert call_args[1]["cwd"] is None
