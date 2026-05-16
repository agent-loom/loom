"""Tests for admin API key management endpoints — src/agent_platform/api/admin.py"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.admin import router as admin_router
from agent_platform.api.admin_deps import AdminDeps


def _make_app(key_store=None) -> FastAPI:
    app = FastAPI()
    app.state.admin_deps = AdminDeps(
        registry=MagicMock(),
        runtime_manager=MagicMock(),
        audit_log=MagicMock(),
        tool_registry=MagicMock(),
        metrics=MagicMock(),
        key_store=key_store,
    )
    app.include_router(admin_router)
    return app


class TestCreateKey:
    def test_no_key_store_returns_501(self):
        client = TestClient(_make_app(key_store=None))
        resp = client.post("/api/v1/admin/keys", json={})
        assert resp.status_code == 501

    def test_create_key_returns_plaintext(self):
        store = MagicMock()
        store.add_key = AsyncMock()
        client = TestClient(_make_app(key_store=store))
        resp = client.post(
            "/api/v1/admin/keys",
            json={"tenant_id": "t1", "role": "readonly", "scopes": ["read"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["api_key"].startswith("ap_")
        assert body["key_id"].startswith("key_")
        assert body["tenant_id"] == "t1"
        assert body["role"] == "readonly"
        store.add_key.assert_called_once()

    def test_create_key_with_expiry(self):
        store = MagicMock()
        store.add_key = AsyncMock()
        client = TestClient(_make_app(key_store=store))
        resp = client.post(
            "/api/v1/admin/keys",
            json={"expires_in_hours": 24},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["expires_at"] is not None


class TestListKeys:
    def test_no_key_store_returns_501(self):
        client = TestClient(_make_app(key_store=None))
        resp = client.get("/api/v1/admin/keys")
        assert resp.status_code == 501

    def test_list_keys_returns_array(self):
        store = MagicMock()
        store.list_keys = AsyncMock(return_value=[
            {"key_id": "k-1", "tenant_id": "t1", "role": "admin"},
        ])
        client = TestClient(_make_app(key_store=store))
        resp = client.get("/api/v1/admin/keys")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_list_keys_with_tenant_filter(self):
        store = MagicMock()
        store.list_keys = AsyncMock(return_value=[])
        client = TestClient(_make_app(key_store=store))
        resp = client.get("/api/v1/admin/keys?tenant_id=t2")
        assert resp.status_code == 200
        store.list_keys.assert_called_once_with(tenant_id="t2")


class TestRevokeKey:
    def test_no_key_store_returns_501(self):
        client = TestClient(_make_app(key_store=None))
        resp = client.delete("/api/v1/admin/keys/k-1")
        assert resp.status_code == 501

    def test_revoke_existing_key(self):
        store = MagicMock()
        store.revoke_key = AsyncMock(return_value=True)
        client = TestClient(_make_app(key_store=store))
        resp = client.delete("/api/v1/admin/keys/k-1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "revoked"

    def test_revoke_nonexistent_key_returns_404(self):
        store = MagicMock()
        store.revoke_key = AsyncMock(return_value=False)
        client = TestClient(_make_app(key_store=store))
        resp = client.delete("/api/v1/admin/keys/k-nope")
        assert resp.status_code == 404
