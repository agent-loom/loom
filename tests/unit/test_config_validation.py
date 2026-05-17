"""config.py 配置加载与 _validate_startup_config 的单元测试。"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from agent_platform.config import Settings, get_settings


class TestSettings:
    def test_defaults(self):
        s = Settings()
        assert s.env == "dev"
        assert s.cors_allowed_origins == "*"
        assert s.devflow_runner_adapter == "mock"
        assert s.database_url == "sqlite+aiosqlite:///./agent_platform.db"
        assert s.max_request_body_bytes == 10 * 1024 * 1024

    def test_custom_values(self):
        s = Settings(
            env="production",
            cors_allowed_origins="https://app.example.com",
            devflow_runner_adapter="claude_code",
            redis_url="redis://localhost:6379",
        )
        assert s.env == "production"
        assert s.cors_allowed_origins == "https://app.example.com"
        assert s.devflow_runner_adapter == "claude_code"
        assert s.redis_url == "redis://localhost:6379"

    def test_optional_fields_default_none(self):
        s = Settings()
        assert s.plane_base_url is None
        assert s.gitlab_base_url is None
        assert s.langfuse_public_key is None
        assert s.weaviate_url is None
        assert s.service_jwt_secret is None


class TestGetSettings:
    def test_returns_settings_from_env(self):
        get_settings.cache_clear()
        env = {
            "AGENT_PLATFORM_ENV": "staging",
            "CORS_ALLOWED_ORIGINS": "https://staging.example.com",
        }
        with patch.dict(os.environ, env, clear=False):
            s = get_settings()
            assert s.env == "staging"
            assert s.cors_allowed_origins == "https://staging.example.com"
        get_settings.cache_clear()


class TestValidateStartupConfig:
    def test_warns_mock_adapter(self, caplog):
        from agent_platform.api.app import _validate_startup_config
        s = Settings(devflow_runner_adapter="mock")
        with caplog.at_level("WARNING"):
            _validate_startup_config(s)
        assert any("mock" in r.message for r in caplog.records)

    def test_warns_no_api_key_production(self, caplog):
        from agent_platform.api.app import _validate_startup_config
        s = Settings(env="production", api_key=None)
        with caplog.at_level("WARNING"):
            _validate_startup_config(s)
        assert any("unauthenticated" in r.message for r in caplog.records)

    def test_warns_cors_star_production(self, caplog):
        from agent_platform.api.app import _validate_startup_config
        s = Settings(env="production", cors_allowed_origins="*")
        with caplog.at_level("WARNING"):
            _validate_startup_config(s)
        assert any("CORS" in r.message for r in caplog.records)

    def test_no_warning_non_production(self, caplog):
        from agent_platform.api.app import _validate_startup_config
        s = Settings(env="dev", cors_allowed_origins="*")
        with caplog.at_level("WARNING"):
            _validate_startup_config(s)
        cors_warnings = [r for r in caplog.records if "CORS" in r.message]
        assert len(cors_warnings) == 0

    def test_production_mock_adapter_with_repo_raises(self):
        """生产环境配置了 DevFlow 仓库但使用 mock adapter 时应阻断启动。"""
        from agent_platform.api.app import _validate_startup_config
        s = Settings(
            env="production",
            devflow_runner_adapter="mock",
            devflow_repo_url="https://gitlab.example.com/repo.git",
        )
        with pytest.raises(ValueError, match="mock adapter"):
            _validate_startup_config(s)

    def test_production_mock_adapter_without_repo_warns(self, caplog):
        """生产环境未配置 DevFlow 仓库时 mock adapter 仅告警不阻断。"""
        from agent_platform.api.app import _validate_startup_config
        s = Settings(
            env="production",
            devflow_runner_adapter="mock",
            devflow_repo_url=None,
        )
        with caplog.at_level("WARNING"):
            _validate_startup_config(s)
        assert any("mock" in r.message for r in caplog.records)

    def test_production_real_adapter_passes(self, caplog):
        """生产环境使用真实 adapter 时不应报错。"""
        from agent_platform.api.app import _validate_startup_config
        s = Settings(
            env="production",
            devflow_runner_adapter="claude_code",
            devflow_repo_url="https://gitlab.example.com/repo.git",
            api_key="secret",
            cors_allowed_origins="https://app.example.com",
            service_jwt_secret="jwt-secret",
        )
        _validate_startup_config(s)
