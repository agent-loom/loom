"""Runner 适配器工厂，根据类型名创建对应适配器实例。"""

from __future__ import annotations

from agent_platform.devflow.runner.adapters.claude_code import ClaudeCodeAdapter
from agent_platform.devflow.runner.adapters.codex import CodexAdapter
from agent_platform.devflow.runner.adapters.mock import MockRunnerAdapter
from agent_platform.devflow.runner.protocol import RunnerAdapter


def create_adapter(
    adapter_type: str,
    *,
    codex_profile: str | None = None,
    sandbox_mode: str = "bypass",
    docker_image: str = "codex-runner",
    **kwargs,
) -> RunnerAdapter:
    """根据适配器类型名创建对应的 Runner 适配器实例。"""
    if adapter_type == "codex":
        codex_kwargs: dict = {**kwargs}
        if codex_profile:
            codex_kwargs["profile"] = codex_profile
        codex_kwargs["sandbox_mode"] = sandbox_mode
        codex_kwargs["docker_image"] = docker_image
        return CodexAdapter(**codex_kwargs)

    adapters: dict[str, type] = {
        "claude_code": ClaudeCodeAdapter,
        "mock": MockRunnerAdapter,
    }
    cls = adapters.get(adapter_type)
    if cls is None:
        available = ["claude_code", "codex", "mock"]
        raise ValueError(f"Unknown adapter type: {adapter_type}. Available: {available}")
    return cls(**kwargs)
