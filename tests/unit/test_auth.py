"""Tests for authentication & authorization — src/agent_platform/api/auth.py"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from agent_platform.api.auth import (
    ROLE_PERMISSIONS,
    ApiKeyRecord,
    AuthIdentity,
    InMemoryApiKeyStore,
    require_role,
    require_scope,
)
from agent_platform.persistence.context import get_audit_context

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    *,
    key_id: str = "k-1",
    tenant_id: str = "tenant-a",
    role: str = "platform_admin",
    scopes: list[str] | None = None,
    created_by: str = "test",
    expires_at: datetime | None = None,
    active: bool = True,
) -> ApiKeyRecord:
    return ApiKeyRecord(
        key_id=key_id,
        key_hash="placeholder",
        tenant_id=tenant_id,
        role=role,
        scopes=scopes or ["chat", "deploy", "admin"],
        created_by=created_by,
        expires_at=expires_at,
        active=active,
    )


class _InjectAuthMiddleware(BaseHTTPMiddleware):
    """Test-only middleware that attaches an AuthIdentity to request.state."""

    def __init__(self, app, *, auth: AuthIdentity | None = None):
        super().__init__(app)
        self.auth = auth

    async def dispatch(self, request: Request, call_next):
        if self.auth is not None:
            request.state.auth = self.auth
        return await call_next(request)


def _make_test_app(auth: AuthIdentity | None = None) -> FastAPI:
    """Build a tiny FastAPI app with role/scope-protected endpoints."""
    app = FastAPI()

    @app.get("/test-role")
    async def test_role_endpoint(identity: AuthIdentity = require_role("platform_admin")):
        return {"role": identity.role}

    @app.get("/test-scope")
    async def test_scope_endpoint(identity: AuthIdentity = require_scope("deploy")):
        return {"scopes": identity.scopes}

    app.add_middleware(_InjectAuthMiddleware, auth=auth)
    return app


def _make_auth_middleware_app(key_store: InMemoryApiKeyStore) -> FastAPI:
    from agent_platform.api.app import AuthMiddleware

    app = FastAPI()

    @app.get("/whoami")
    async def whoami(request: Request):
        auth = request.state.auth
        audit = get_audit_context()
        return {
            "tenant_id": auth.tenant_id,
            "audit_tenant_id": audit.tenant_id,
        }

    app.add_middleware(AuthMiddleware, key_store=key_store)
    return app


# ---------------------------------------------------------------------------
# Tests — InMemoryApiKeyStore
# ---------------------------------------------------------------------------


def test_in_memory_key_store_add_and_verify():
    """Adding a key and verifying with the same plaintext should succeed."""
    store = InMemoryApiKeyStore()
    record = _make_record()
    store.add_key("super-secret", record)

    result = store.verify("super-secret")
    assert result is not None
    assert result.key_id == "k-1"
    assert result.tenant_id == "tenant-a"


def test_auth_middleware_preserves_persisted_key_tenant_binding():
    store = InMemoryApiKeyStore()
    store.add_key("tenant-key", _make_record(tenant_id="tenant-a", scopes=["read"]))
    app = _make_auth_middleware_app(store)
    client = TestClient(app)

    response = client.get(
        "/whoami",
        headers={
            "x-api-key": "tenant-key",
            "x-tenant-id": "tenant-b",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "tenant_id": "tenant-a",
        "audit_tenant_id": "tenant-a",
    }


def test_in_memory_key_store_wrong_key():
    """Verifying with a wrong plaintext should return None."""
    store = InMemoryApiKeyStore()
    store.add_key("correct-key", _make_record())

    assert store.verify("wrong-key") is None


def test_in_memory_key_store_expired_key():
    """An expired key should return None even if the plaintext matches."""
    store = InMemoryApiKeyStore()
    record = _make_record(expires_at=datetime.now(UTC) - timedelta(hours=1))
    store.add_key("expired-key", record)

    assert store.verify("expired-key") is None


def test_in_memory_key_store_inactive_key():
    """An inactive key should return None even if the plaintext matches."""
    store = InMemoryApiKeyStore()
    record = _make_record(active=False)
    store.add_key("inactive-key", record)

    assert store.verify("inactive-key") is None


# ---------------------------------------------------------------------------
# Tests — AuthIdentity model
# ---------------------------------------------------------------------------


def test_auth_identity_creation():
    """AuthIdentity should validate and store all fields correctly."""
    identity = AuthIdentity(
        subject="user-42",
        tenant_id="tenant-b",
        role="agent_developer",
        scopes=["chat", "eval"],
        key_id="k-99",
    )
    assert identity.subject == "user-42"
    assert identity.tenant_id == "tenant-b"
    assert identity.role == "agent_developer"
    assert identity.scopes == ["chat", "eval"]
    assert identity.key_id == "k-99"


def test_auth_identity_optional_key_id():
    """key_id defaults to None when omitted."""
    identity = AuthIdentity(
        subject="svc-1",
        tenant_id="t",
        role="readonly",
        scopes=["read"],
    )
    assert identity.key_id is None


# ---------------------------------------------------------------------------
# Tests — require_role
# ---------------------------------------------------------------------------


def test_require_role_passes():
    """Endpoint should return 200 when the caller has the required role."""
    auth = AuthIdentity(
        subject="admin-1",
        tenant_id="t",
        role="platform_admin",
        scopes=["admin"],
    )
    app = _make_test_app(auth=auth)
    client = TestClient(app)

    resp = client.get("/test-role")
    assert resp.status_code == 200
    assert resp.json() == {"role": "platform_admin"}


def test_require_role_fails():
    """Endpoint should return 403 when the caller lacks the required role."""
    auth = AuthIdentity(
        subject="dev-1",
        tenant_id="t",
        role="readonly",
        scopes=["read"],
    )
    app = _make_test_app(auth=auth)
    client = TestClient(app)

    resp = client.get("/test-role")
    assert resp.status_code == 403
    assert "insufficient role" in resp.json()["detail"]


def test_require_role_unauthenticated():
    """Endpoint should return 401 when no auth identity is present."""
    app = _make_test_app(auth=None)
    client = TestClient(app)

    resp = client.get("/test-role")
    assert resp.status_code == 401
    assert "not authenticated" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Tests — require_scope
# ---------------------------------------------------------------------------


def test_require_scope_passes():
    """Endpoint should return 200 when the caller has the required scope."""
    auth = AuthIdentity(
        subject="op-1",
        tenant_id="t",
        role="agent_operator",
        scopes=["chat", "deploy", "eval"],
    )
    app = _make_test_app(auth=auth)
    client = TestClient(app)

    resp = client.get("/test-scope")
    assert resp.status_code == 200
    assert resp.json() == {"scopes": ["chat", "deploy", "eval"]}


def test_require_scope_fails():
    """Endpoint should return 403 when the caller is missing the required scope."""
    auth = AuthIdentity(
        subject="reader-1",
        tenant_id="t",
        role="readonly",
        scopes=["read"],
    )
    app = _make_test_app(auth=auth)
    client = TestClient(app)

    resp = client.get("/test-scope")
    assert resp.status_code == 403
    assert "missing scope: deploy" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Tests — ROLE_PERMISSIONS constant
# ---------------------------------------------------------------------------


def test_role_permissions_contains_expected_roles():
    """ROLE_PERMISSIONS should define exactly the four standard roles."""
    assert set(ROLE_PERMISSIONS.keys()) == {
        "platform_admin",
        "agent_developer",
        "agent_operator",
        "readonly",
    }


def test_platform_admin_has_all_core_scopes():
    """platform_admin should have the broadest scope set."""
    perms = ROLE_PERMISSIONS["platform_admin"]
    assert "admin" in perms
    assert "chat" in perms
    assert "deploy" in perms
    assert "eval" in perms
