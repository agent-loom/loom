"""S9 Phase 9: RuntimeMemory REST API 集成与权限测试。"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from agent_platform.api.app import app
from agent_platform.api.auth import AuthIdentity


@pytest.fixture
def auth_headers() -> dict[str, str]:
    # 模拟平台管理员权限身份
    from agent_platform.api.app import AuthMiddleware
    # 模拟 API 密钥验证或直通
    return {"Authorization": "Bearer dev-e2e-test-key"}


@pytest.mark.asyncio
async def test_runtime_memory_endpoints(auth_headers):
    """测试 RuntimeMemory 的 HTTP REST API 完整生命周期。"""
    # 注入测试专用的管理员 mock 密钥或直通
    app.state.runtime_memory_repo._store.clear()

    # 通过 AsyncClient 模拟发起 API 请求
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. 创建 RuntimeMemory
        payload = {
            "agent_id": "test-api-agent",
            "tenant_id": "t1",
            "scope": "user",
            "subject_id": "user_api_123",
            "type": "preference",
            "content": "用户倾向于详细解答",
            "confidence": 0.9,
            "ttl_seconds": 3600,
        }
        resp = await client.post(
            "/api/v1/runtime-memory",
            json=payload,
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == "用户倾向于详细解答"
        assert data["scope"] == "user"
        memory_id = data["memory_id"]
        assert memory_id is not None

        # 2. 查询单个 RuntimeMemory
        resp = await client.get(
            f"/api/v1/runtime-memory/{memory_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["content"] == "用户倾向于详细解答"

        # 3. 列表查询（按 agent 过滤）
        resp = await client.get(
            "/api/v1/runtime-memory?agent_id=test-api-agent",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        mems = resp.json()
        assert len(mems) == 1
        assert mems[0]["memory_id"] == memory_id

        # 4. 删除 RuntimeMemory
        resp = await client.delete(
            f"/api/v1/runtime-memory/{memory_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "deleted", "memory_id": memory_id}

        # 再次获取，应该返回 404
        resp = await client.get(
            f"/api/v1/runtime-memory/{memory_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 404
