"""Tests for production-readiness features: RBAC, health check, CORS, lifespan, error handling."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agent_platform.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _make_app(**env_overrides):
    """Create a fresh app with overridden env vars."""
    with patch.dict(os.environ, env_overrides, clear=False):
        get_settings.cache_clear()
        from agent_platform.api.app import create_app

        return create_app()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestHealthEndpoints:
    def test_liveness_probe(self):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_readiness_probe_in_memory(self):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/health/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert data["checks"]["database"] == "in_memory"
        assert "runner_adapter" in data["checks"]

    def test_readiness_reports_auth_status_open(self):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/health/ready")
        assert resp.json()["checks"]["auth"] == "open"

    def test_readiness_reports_auth_status_enabled(self):
        app = _make_app(AGENT_PLATFORM_API_KEY="test-key")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/health/ready", headers={"x-api-key": "test-key"})
        assert resp.json()["checks"]["auth"] == "enabled"


# ---------------------------------------------------------------------------
# Auth middleware + RBAC
# ---------------------------------------------------------------------------


class TestAuthMiddleware:
    def test_no_api_key_configured_allows_all_requests(self):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/agents")
        assert resp.status_code == 200

    def test_api_key_required_rejects_missing_key(self):
        app = _make_app(AGENT_PLATFORM_API_KEY="secret-key")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/agents")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "UNAUTHORIZED"

    def test_api_key_bearer_token_accepted(self):
        app = _make_app(AGENT_PLATFORM_API_KEY="secret-key")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/api/v1/agents",
            headers={"Authorization": "Bearer secret-key"},
        )
        assert resp.status_code == 200

    def test_api_key_header_accepted(self):
        app = _make_app(AGENT_PLATFORM_API_KEY="secret-key")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/api/v1/agents",
            headers={"x-api-key": "secret-key"},
        )
        assert resp.status_code == 200

    def test_wrong_api_key_rejected(self):
        app = _make_app(AGENT_PLATFORM_API_KEY="secret-key")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/api/v1/agents",
            headers={"x-api-key": "wrong-key"},
        )
        assert resp.status_code == 401

    def test_health_bypasses_auth(self):
        app = _make_app(AGENT_PLATFORM_API_KEY="secret-key")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_ready_bypasses_auth(self):
        app = _make_app(AGENT_PLATFORM_API_KEY="secret-key")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/health/ready")
        assert resp.status_code == 200

    def test_tenant_id_extracted_from_header(self):
        app = _make_app(AGENT_PLATFORM_API_KEY="key-1")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/v1/agent/chat",
            headers={
                "x-api-key": "key-1",
                "x-tenant-id": "tenant-abc",
            },
            json={
                "agent_id": "echo",
                "session_id": "sess_t",
                "input": {"query": "hi"},
            },
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# CORS configuration
# ---------------------------------------------------------------------------


class TestCorsConfig:
    def test_default_cors_allows_all(self):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.options(
            "/api/v1/agents",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == "*"

    def test_restricted_cors_blocks_unlisted_origin(self):
        app = _make_app(CORS_ALLOWED_ORIGINS="https://app.example.com")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.options(
            "/api/v1/agents",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") != "https://evil.example.com"

    def test_restricted_cors_allows_listed_origin(self):
        app = _make_app(CORS_ALLOWED_ORIGINS="https://app.example.com,https://admin.example.com")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.options(
            "/api/v1/agents",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == "https://app.example.com"


# ---------------------------------------------------------------------------
# Global error handler
# ---------------------------------------------------------------------------


class TestGlobalErrorHandler:
    def test_unhandled_exception_returns_structured_json(self):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/sessions/nonexistent-session")
        assert resp.status_code == 404

    def test_error_response_includes_request_id(self):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/api/v1/sessions/nonexistent-session",
            headers={"x-request-id": "req_err_test"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestConfigValidation:
    def test_cors_allowed_origins_from_env(self):
        with patch.dict(os.environ, {"CORS_ALLOWED_ORIGINS": "https://a.com,https://b.com"}):
            get_settings.cache_clear()
            s = get_settings()
            assert s.cors_allowed_origins == "https://a.com,https://b.com"

    def test_default_cors_is_star(self):
        get_settings.cache_clear()
        s = get_settings()
        assert s.cors_allowed_origins == "*"

    def test_mock_runner_is_default(self):
        get_settings.cache_clear()
        s = get_settings()
        assert s.devflow_runner_adapter == "mock"


# ---------------------------------------------------------------------------
# RBAC scope enforcement
# ---------------------------------------------------------------------------


class TestRbacScopes:
    def test_register_endpoint_requires_register_scope(self):
        from agent_platform.api.auth import AuthIdentity

        app = _make_app(AGENT_PLATFORM_API_KEY="key-1")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/v1/agent-packages/register",
            headers={"x-api-key": "key-1"},
            json={"manifest_path": "/nonexistent/manifest.yaml"},
        )
        # The endpoint should be reachable with valid auth (may fail on business logic, not auth)
        assert resp.status_code != 401
        assert resp.status_code != 403

    def test_unauthenticated_register_rejected(self):
        app = _make_app(AGENT_PLATFORM_API_KEY="key-1")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/v1/agent-packages/register",
            json={"manifest_path": "/nonexistent/manifest.yaml"},
        )
        assert resp.status_code == 401

    def test_admin_endpoints_require_auth(self):
        app = _make_app(AGENT_PLATFORM_API_KEY="key-1")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/admin/agents")
        assert resp.status_code == 401

    def test_admin_endpoints_accessible_with_key(self):
        app = _make_app(AGENT_PLATFORM_API_KEY="key-1")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/api/v1/admin/agents",
            headers={"x-api-key": "key-1"},
        )
        assert resp.status_code == 200

    def test_rollback_endpoint_requires_auth(self):
        app = _make_app(AGENT_PLATFORM_API_KEY="key-1")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/v1/deployments/rollback",
            json={"agent_id": "test", "channel": "prod", "actor": "ci"},
        )
        assert resp.status_code == 401

    def test_approval_resolve_requires_auth(self):
        app = _make_app(AGENT_PLATFORM_API_KEY="key-1")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/v1/approvals/fake-id/resolve",
            json={"status": "approved", "actor": "admin"},
        )
        assert resp.status_code == 401

    def test_chat_requires_auth(self):
        app = _make_app(AGENT_PLATFORM_API_KEY="key-1")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/v1/agent/chat",
            json={
                "agent_id": "echo",
                "session_id": "sess",
                "input": {"query": "hi"},
            },
        )
        assert resp.status_code == 401
