"""Service Auth 中间件集成测试。"""

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from agent_platform.api.app import AuthMiddleware
from agent_platform.api.service_auth import ServiceAuthProvider, ServiceIdentity


def _create_test_app(
    *,
    api_key: str | None = None,
    service_auth: ServiceAuthProvider | None = None,
) -> FastAPI:
    """创建带 AuthMiddleware 的测试应用。"""
    app = FastAPI()

    app.add_middleware(
        AuthMiddleware,
        api_key=api_key,
        service_auth=service_auth,
    )

    @app.get("/test")
    async def test_endpoint(request: Request) -> dict:
        auth = getattr(request.state, "auth", None)
        if auth is None:
            return {"subject": "none"}
        return {
            "subject": auth.subject,
            "role": auth.role,
            "scopes": auth.scopes,
        }

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    return app


@pytest.fixture()
def service_auth() -> ServiceAuthProvider:
    return ServiceAuthProvider(
        jwt_secret="test-jwt-secret-key-for-hmac",
        shared_secrets={"svc-runner": "runner-secret-123"},
    )


class TestServiceTokenAuth:
    """Service JWT Token 认证测试。"""

    @pytest.mark.asyncio()
    async def test_valid_service_token(self, service_auth):
        identity = ServiceIdentity(
            service_id="svc-runner",
            permissions=["chat", "deploy"],
        )
        token = service_auth.issue_token(identity)
        app = _create_test_app(api_key="test-key", service_auth=service_auth)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as client:
            resp = await client.get("/test", headers={"x-service-token": token})
            assert resp.status_code == 200
            data = resp.json()
            assert data["subject"] == "svc-runner"
            assert data["role"] == "service"
            assert "chat" in data["scopes"]

    @pytest.mark.asyncio()
    async def test_invalid_service_token_rejected(self, service_auth):
        app = _create_test_app(api_key="test-key", service_auth=service_auth)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as client:
            resp = await client.get(
                "/test", headers={"x-service-token": "invalid.token.here"},
            )
            assert resp.status_code == 401
            assert "service token" in resp.json()["error"]["message"]

    @pytest.mark.asyncio()
    async def test_service_token_without_provider(self):
        """service_auth 未配置时，service token 被忽略。"""
        app = _create_test_app(api_key=None, service_auth=None)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as client:
            resp = await client.get(
                "/test", headers={"x-service-token": "any-token"},
            )
            assert resp.status_code == 200
            assert resp.json()["subject"] == "anonymous"


class TestSharedSecretAuth:
    """Shared Secret 认证测试。"""

    @pytest.mark.asyncio()
    async def test_valid_shared_secret(self, service_auth):
        app = _create_test_app(api_key="test-key", service_auth=service_auth)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as client:
            resp = await client.get("/test", headers={
                "x-service-id": "svc-runner",
                "x-service-secret": "runner-secret-123",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["subject"] == "svc-runner"
            assert data["role"] == "service"

    @pytest.mark.asyncio()
    async def test_invalid_shared_secret_rejected(self, service_auth):
        app = _create_test_app(api_key="test-key", service_auth=service_auth)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as client:
            resp = await client.get("/test", headers={
                "x-service-id": "svc-runner",
                "x-service-secret": "wrong-secret",
            })
            assert resp.status_code == 401

    @pytest.mark.asyncio()
    async def test_unknown_service_id_rejected(self, service_auth):
        app = _create_test_app(api_key="test-key", service_auth=service_auth)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as client:
            resp = await client.get("/test", headers={
                "x-service-id": "svc-unknown",
                "x-service-secret": "any",
            })
            assert resp.status_code == 401


class TestAuthPriority:
    """认证优先级测试。"""

    @pytest.mark.asyncio()
    async def test_service_token_over_api_key(self, service_auth):
        """Service Token 优先于 API Key。"""
        identity = ServiceIdentity(service_id="svc-priority")
        token = service_auth.issue_token(identity)
        app = _create_test_app(api_key="test-key", service_auth=service_auth)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as client:
            resp = await client.get("/test", headers={
                "x-service-token": token,
                "x-api-key": "test-key",
            })
            assert resp.status_code == 200
            assert resp.json()["subject"] == "svc-priority"

    @pytest.mark.asyncio()
    async def test_api_key_fallback(self, service_auth):
        """无 service token 时回退到 API Key。"""
        app = _create_test_app(api_key="test-key", service_auth=service_auth)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as client:
            resp = await client.get("/test", headers={"x-api-key": "test-key"})
            assert resp.status_code == 200
            assert resp.json()["subject"] == "api-key-user"

    @pytest.mark.asyncio()
    async def test_health_skips_auth(self, service_auth):
        app = _create_test_app(api_key="test-key", service_auth=service_auth)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as client:
            resp = await client.get("/health")
            assert resp.status_code == 200

    @pytest.mark.asyncio()
    async def test_no_credentials_rejected(self, service_auth):
        app = _create_test_app(api_key="test-key", service_auth=service_auth)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as client:
            resp = await client.get("/test")
            assert resp.status_code == 401
