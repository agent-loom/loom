"""Runner 适配器工厂，根据类型名创建对应适配器实例。"""

from __future__ import annotations

from agent_platform.devflow.runner.adapters.claude_code import ClaudeCodeAdapter
from agent_platform.devflow.runner.adapters.codex import CodexAdapter
from agent_platform.devflow.runner.adapters.mock import MockRunnerAdapter
from agent_platform.devflow.runner.protocol import RunnerAdapter


def create_adapter(adapter_type: str, **kwargs) -> RunnerAdapter:
    """根据适配器类型名创建对应的 Runner 适配器实例。"""
    adapters: dict[str, type] = {
        "claude_code": ClaudeCodeAdapter,
        "codex": CodexAdapter,
        "mock": MockRunnerAdapter,
    }
    cls = adapters.get(adapter_type)
    if cls is None:
        available = list(adapters.keys())
        raise ValueError(f"Unknown adapter type: {adapter_type}. Available: {available}")
    return cls(**kwargs)
