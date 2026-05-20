"""Tests for runner adapter environment filtering."""

from __future__ import annotations

import os
from unittest.mock import patch

from agent_platform.devflow.runner.adapters.utils import build_safe_env


def test_build_safe_env_keeps_anthropic_compat_variables() -> None:
    env = {
        "ANTHROPIC_AUTH_TOKEN": "token-123",
        "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
        "HOME": "/tmp/home",
        "UNSAFE_SECRET": "should-not-pass",
    }
    with patch.dict(os.environ, env, clear=True):
        safe_env = build_safe_env()

    assert safe_env["ANTHROPIC_AUTH_TOKEN"] == "token-123"
    assert safe_env["ANTHROPIC_BASE_URL"] == "https://open.bigmodel.cn/api/anthropic"
    assert safe_env["HOME"] == "/tmp/home"
    assert "UNSAFE_SECRET" not in safe_env
