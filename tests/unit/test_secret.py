from __future__ import annotations

import pytest

from agent_platform.policy.secret import (
    EnvSecretBackend,
    SecretNotFoundError,
    SecretResolver,
    find_secret_refs,
    validate_secret_refs,
)


class TestEnvSecretBackend:
    def test_get_from_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MY_API_KEY", "secret-value")
        backend = EnvSecretBackend()
        assert backend.get("MY_API_KEY") == "secret-value"

    def test_get_missing_returns_none(self):
        backend = EnvSecretBackend()
        assert backend.get("DEFINITELY_NOT_SET_12345") is None

    def test_get_with_tenant_scoped(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TENANT_ABC_API_KEY", "tenant-secret")
        backend = EnvSecretBackend()
        assert backend.get("API_KEY", tenant_id="tenant-abc") == "tenant-secret"

    def test_get_tenant_falls_back_to_global(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("API_KEY", "global-secret")
        backend = EnvSecretBackend()
        assert backend.get("API_KEY", tenant_id="tenant-xyz") == "global-secret"

    def test_exists(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("EXISTS_KEY", "yes")
        backend = EnvSecretBackend()
        assert backend.exists("EXISTS_KEY") is True
        assert backend.exists("NOPE_KEY_99") is False


class TestSecretResolver:
    def test_resolve_simple_ref(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
        resolver = SecretResolver(EnvSecretBackend())
        config = {"api_key": "$secret:OPENAI_API_KEY", "endpoint": "https://api.example.com"}
        resolved, secrets = resolver.resolve_config(config)
        assert resolved["api_key"] == "sk-test-123"
        assert resolved["endpoint"] == "https://api.example.com"
        assert "sk-test-123" in secrets

    def test_resolve_nested_config(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DB_PASSWORD", "p@ss")
        resolver = SecretResolver(EnvSecretBackend())
        config = {"database": {"password": "$secret:DB_PASSWORD", "host": "localhost"}}
        resolved, secrets = resolver.resolve_config(config)
        assert resolved["database"]["password"] == "p@ss"
        assert resolved["database"]["host"] == "localhost"
        assert "p@ss" in secrets

    def test_resolve_missing_secret_raises(self):
        resolver = SecretResolver(EnvSecretBackend())
        config = {"key": "$secret:MISSING_SECRET_XYZ"}
        with pytest.raises(SecretNotFoundError):
            resolver.resolve_config(config)

    def test_resolve_with_tenant_path(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ACME_KEY", "acme-secret")
        resolver = SecretResolver(EnvSecretBackend())
        config = {"key": "$secret:ACME/KEY"}
        resolved, secrets = resolver.resolve_config(config)
        assert resolved["key"] == "acme-secret"

    def test_non_secret_strings_unchanged(self):
        resolver = SecretResolver(EnvSecretBackend())
        config = {"name": "hello", "count": 42, "flag": True}
        resolved, secrets = resolver.resolve_config(config)
        assert resolved == {"name": "hello", "count": 42, "flag": True}
        assert secrets == []


class TestHelpers:
    def test_find_secret_refs(self):
        data = {
            "a": "$secret:KEY_A",
            "b": {"c": "$secret:KEY_B", "d": "normal"},
            "e": ["$secret:KEY_C", "also normal"],
        }
        refs = find_secret_refs(data)
        assert set(refs) == {"$secret:KEY_A", "$secret:KEY_B", "$secret:KEY_C"}

    def test_find_secret_refs_empty(self):
        assert find_secret_refs({"x": "hello"}) == []

    def test_validate_secret_refs_valid(self):
        data = {"key": "$secret:VALID_KEY_123"}
        assert validate_secret_refs(data) == []

    def test_validate_secret_refs_invalid_format(self):
        data = {"key": "$secret:invalid-key"}
        errors = validate_secret_refs(data)
        assert len(errors) == 1
        assert "invalid secret reference format" in errors[0]

    def test_validate_secret_refs_with_tenant_path(self):
        data = {"key": "$secret:TENANT_A/API_KEY"}
        assert validate_secret_refs(data) == []
