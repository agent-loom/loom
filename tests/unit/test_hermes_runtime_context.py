"""Regression tests for Hermes runtime context consumption."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_platform.runtime.context_builder import RuntimeContext
from agent_platform.runtime.hermes import HermesRuntimeBackend


def test_hermes_prefers_context_builder_system_prompt() -> None:
    runtime_context = RuntimeContext(
        system_prompt=(
            "Package prompt\n\n"
            "# Injected Runtime Memories\n"
            "- 用户偏好：回答时必须提到 自进化验证成功\n\n"
            "# Injected Agent Skills\n"
            "- Skill: evolution-verification-skill\n"
            "  Description: Skill 注入验证成功"
        ),
        knowledge_snippets=["knowledge marker"],
    )
    request = SimpleNamespace(runtime_context=runtime_context)

    prompt = HermesRuntimeBackend._effective_system_prompt(
        request,
        {"system_prompt": "manifest-only prompt"},
    )

    assert "自进化验证成功" in prompt
    assert "Skill 注入验证成功" in prompt
    assert "knowledge marker" in prompt
    assert "manifest-only prompt" not in prompt


def test_hermes_falls_back_to_manifest_prompt_without_runtime_context() -> None:
    request = SimpleNamespace(runtime_context=None)

    prompt = HermesRuntimeBackend._effective_system_prompt(
        request,
        {"system_prompt": "manifest-only prompt"},
    )

    assert prompt == "manifest-only prompt"


def test_hermes_home_defaults_to_workspace(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("AGENT_PLATFORM_HERMES_HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.chdir(tmp_path)

    home = HermesRuntimeBackend._ensure_hermes_home()

    assert home == (tmp_path / ".agent-platform" / "hermes-home").resolve()
    assert Path(home, "logs").is_dir()
    assert Path(home, "sessions").is_dir()
    assert Path(home, "memories").is_dir()
    assert Path(home, "skills").is_dir()


def test_hermes_home_prefers_agent_platform_override(
    monkeypatch, tmp_path,
) -> None:
    override = tmp_path / "custom-hermes-home"
    monkeypatch.setenv("AGENT_PLATFORM_HERMES_HOME", str(override))
    monkeypatch.delenv("HERMES_HOME", raising=False)

    home = HermesRuntimeBackend._ensure_hermes_home()

    assert home == override
    assert Path(home, "logs").is_dir()
