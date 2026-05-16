from __future__ import annotations

import httpx
import pytest

from agent_platform.integrations.errors import IntegrationError, ScmError
from agent_platform.integrations.http_client import (
    RETRYABLE_STATUS_CODES,
    HttpClient,
)


def _make_client(handler, *, max_retries: int = 1) -> HttpClient:
    return HttpClient(
        base_url="https://api.test",
        headers={"Authorization": "Bearer tok"},
        max_retries=max_retries,
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_successful_get():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client = _make_client(handler)
    result = await client.request("GET", "/health")
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_successful_post():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        return httpx.Response(201, json={"id": 1})

    client = _make_client(handler)
    result = await client.request("POST", "/items", json={"name": "x"})
    assert result == {"id": 1}
    assert captured["method"] == "POST"
    assert captured["path"] == "/items"


@pytest.mark.asyncio
async def test_4xx_raises_error_cls():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    client = _make_client(handler)
    with pytest.raises(ScmError) as exc_info:
        await client.request("GET", "/missing", error_cls=ScmError)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_5xx_retries_then_raises():
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(502, text="bad gateway")

    client = _make_client(handler, max_retries=2)
    with pytest.raises(IntegrationError):
        await client.request("GET", "/flaky")
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_5xx_succeeds_on_retry():
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, json={"recovered": True})

    client = _make_client(handler, max_retries=3)
    result = await client.request("GET", "/retry")
    assert result == {"recovered": True}
    assert call_count == 2


@pytest.mark.asyncio
async def test_retryable_status_codes_set():
    assert 500 in RETRYABLE_STATUS_CODES
    assert 429 in RETRYABLE_STATUS_CODES
    assert 400 not in RETRYABLE_STATUS_CODES


@pytest.mark.asyncio
async def test_close():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    client = _make_client(handler)
    await client.request("GET", "/x")
    assert client._client is not None
    await client.close()
    assert client._client is None


@pytest.mark.asyncio
async def test_close_idempotent():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    client = _make_client(handler)
    await client.close()
    await client.close()


@pytest.mark.asyncio
async def test_default_error_cls_is_integration_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    client = _make_client(handler)
    with pytest.raises(IntegrationError) as exc_info:
        await client.request("GET", "/secret")
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_headers_sent():
    seen_headers: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(dict(request.headers))
        return httpx.Response(200, json={})

    client = _make_client(handler)
    await client.request("GET", "/check-headers")
    assert seen_headers["authorization"] == "Bearer tok"
