"""422 统一格式、分页参数校验、traffic_percent 范围校验的测试。"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from agent_platform.api.app import create_app


@pytest.fixture
def app():
    return create_app()


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestValidationErrorFormat:
    @pytest.mark.asyncio
    async def test_invalid_traffic_percent_returns_422(self, client):
        resp = await client.post(
            "/api/v1/agent-packages/test-agent/versions/1.0/deploy",
            json={"traffic_percent": 150},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert "details" in body["error"]

    @pytest.mark.asyncio
    async def test_negative_traffic_percent_returns_422(self, client):
        resp = await client.post(
            "/api/v1/agent-packages/test-agent/versions/1.0/deploy",
            json={"traffic_percent": -1},
        )
        assert resp.status_code == 422


class TestPaginationParams:
    @pytest.mark.asyncio
    async def test_devflow_jobs_limit_too_large(self, client):
        resp = await client.get("/api/v1/devflow/jobs?limit=1000")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_devflow_jobs_limit_zero(self, client):
        resp = await client.get("/api/v1/devflow/jobs?limit=0")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_devflow_jobs_valid_limit(self, client):
        resp = await client.get("/api/v1/devflow/jobs?limit=10")
        assert resp.status_code == 200
