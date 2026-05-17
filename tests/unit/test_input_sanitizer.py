"""输入消毒中间件测试。"""

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from agent_platform.api.input_sanitizer import (
    InputSanitizationMiddleware,
    check_payload_safety,
)


def _create_test_app(max_body_bytes: int = 1024) -> FastAPI:
    app = FastAPI()
    app.add_middleware(InputSanitizationMiddleware, max_body_bytes=max_body_bytes)

    @app.post("/api/test")
    async def test_post(request: Request) -> dict:
        body = await request.json()
        return {"received": body}

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/test")
    async def test_get() -> dict:
        return {"ok": True}

    return app


class TestPayloadSafety:
    """check_payload_safety 函数测试。"""

    def test_safe_text(self):
        assert check_payload_safety("hello world") is None

    def test_xss_script_tag(self):
        assert check_payload_safety("<script>alert(1)</script>") == "XSS"

    def test_xss_javascript_proto(self):
        assert check_payload_safety("javascript:void(0)") == "XSS"

    def test_xss_event_handler(self):
        assert check_payload_safety('onerror="alert(1)"') == "XSS"

    def test_xss_iframe(self):
        assert check_payload_safety("<iframe src=x>") == "XSS"

    def test_sql_drop_table(self):
        assert check_payload_safety("; DROP TABLE users") == "SQL_INJECTION"

    def test_sql_union_select(self):
        assert check_payload_safety("1 UNION SELECT * FROM users") == "SQL_INJECTION"

    def test_sql_or_bypass(self):
        assert check_payload_safety("' OR '1'='1") == "SQL_INJECTION"

    def test_normal_sql_keywords_safe(self):
        """正常使用 SQL 关键词不应被误判。"""
        assert check_payload_safety("Please select your table preference") is None


class TestInputSanitizationMiddleware:
    """中间件集成测试。"""

    @pytest.mark.asyncio()
    async def test_get_requests_pass_through(self):
        app = _create_test_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as client:
            resp = await client.get("/api/test")
            assert resp.status_code == 200

    @pytest.mark.asyncio()
    async def test_health_skips_check(self):
        app = _create_test_app(max_body_bytes=1)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as client:
            resp = await client.get("/health")
            assert resp.status_code == 200

    @pytest.mark.asyncio()
    async def test_normal_post_passes(self):
        app = _create_test_app(max_body_bytes=10000)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/test",
                json={"name": "test"},
            )
            assert resp.status_code == 200
            assert resp.json()["received"]["name"] == "test"

    @pytest.mark.asyncio()
    async def test_oversized_body_rejected(self):
        app = _create_test_app(max_body_bytes=10)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/test",
                json={"data": "x" * 100},
                headers={"content-length": "500"},
            )
            assert resp.status_code == 413
            assert "PAYLOAD_TOO_LARGE" in resp.json()["error"]["code"]

    @pytest.mark.asyncio()
    async def test_wrong_content_type_rejected(self):
        app = _create_test_app(max_body_bytes=10000)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/test",
                content=b"<xml>data</xml>",
                headers={
                    "content-type": "application/xml",
                    "content-length": "15",
                },
            )
            assert resp.status_code == 415
