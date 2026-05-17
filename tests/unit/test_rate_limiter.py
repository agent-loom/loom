"""Tests for RateLimiterMiddleware — src/agent_platform/api/rate_limiter.py"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.rate_limiter import (
    ROLE_RATE_LIMITS,
    InMemoryRateLimiterBackend,
    RateLimiterMiddleware,
    _TokenBucket,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(requests_per_minute: int = 60, burst: int = 3) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        RateLimiterMiddleware,
        requests_per_minute=requests_per_minute,
        burst=burst,
    )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/docs")
    async def docs():
        return {"docs": True}

    @app.get("/api/v1/test")
    async def test_endpoint():
        return {"data": "ok"}

    return app


# ---------------------------------------------------------------------------
# Tests — Health / bypass paths
# ---------------------------------------------------------------------------

def test_health_endpoint_bypasses_rate_limiter():
    app = _make_app(burst=1)
    client = TestClient(app)
    for _ in range(20):
        resp = client.get("/health")
        assert resp.status_code == 200


def test_docs_endpoint_bypasses_rate_limiter():
    app = _make_app(burst=1)
    client = TestClient(app)
    for _ in range(10):
        resp = client.get("/docs")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests — testclient host bypass
# ---------------------------------------------------------------------------

def test_testclient_host_bypasses_rate_limiter():
    app = _make_app(burst=1)
    client = TestClient(app)
    for _ in range(20):
        resp = client.get("/api/v1/test")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests — TokenBucket unit tests
# ---------------------------------------------------------------------------

def test_token_bucket_within_burst_succeeds():
    bucket = _TokenBucket(rate=1.0, burst=5)
    results = [bucket.consume() for _ in range(5)]
    assert all(results)


def test_token_bucket_exceeding_burst_fails():
    bucket = _TokenBucket(rate=1.0, burst=3)
    for _ in range(3):
        assert bucket.consume() is True
    assert bucket.consume() is False


def test_token_bucket_refills_over_time():
    bucket = _TokenBucket(rate=10.0, burst=5)
    for _ in range(5):
        bucket.consume()
    assert bucket.consume() is False
    bucket.last_refill = time.monotonic() - 0.5
    assert bucket.consume() is True


# ---------------------------------------------------------------------------
# Tests — InMemoryRateLimiterBackend
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inmemory_backend_within_burst():
    backend = InMemoryRateLimiterBackend()
    results = [await backend.try_consume("k1", 1.0, 3) for _ in range(3)]
    assert all(results)


@pytest.mark.asyncio
async def test_inmemory_backend_exceeds_burst():
    backend = InMemoryRateLimiterBackend()
    for _ in range(3):
        await backend.try_consume("k1", 1.0, 3)
    assert await backend.try_consume("k1", 1.0, 3) is False


@pytest.mark.asyncio
async def test_inmemory_backend_separate_keys():
    backend = InMemoryRateLimiterBackend()
    for _ in range(3):
        await backend.try_consume("k1", 1.0, 3)
    assert await backend.try_consume("k1", 1.0, 3) is False
    assert await backend.try_consume("k2", 1.0, 3) is True


# ---------------------------------------------------------------------------
# Tests — Middleware 分发
# ---------------------------------------------------------------------------

def test_retry_after_value():
    app = _make_app(burst=2)
    middleware = RateLimiterMiddleware(app, requests_per_minute=60, burst=2)
    expected_retry_after = str(max(1, int(1.0 / middleware.default_rate)))
    assert expected_retry_after == "1"


# ---------------------------------------------------------------------------
# Tests — Client key extraction
# ---------------------------------------------------------------------------

def _mock_request(auth=None, headers=None, client_host="1.2.3.4"):
    mock = MagicMock()
    mock.headers = headers or {}
    if client_host:
        mock.client = MagicMock()
        mock.client.host = client_host
    else:
        mock.client = None
    mock.state = MagicMock()
    mock.state.auth = auth
    return mock


def test_auth_key_id_used_as_client_key():
    auth = MagicMock()
    auth.key_id = "k-123"
    req = _mock_request(auth=auth)
    key = RateLimiterMiddleware._get_client_key(req)
    assert key == "key:k-123"


def test_api_key_header_used_when_no_auth():
    req = _mock_request(auth=None, headers={"x-api-key": "test-key-123"})
    key = RateLimiterMiddleware._get_client_key(req)
    assert key == "key:test-key-123"


def test_ip_used_when_no_auth_no_api_key():
    req = _mock_request(auth=None, headers={}, client_host="192.168.1.100")
    key = RateLimiterMiddleware._get_client_key(req)
    assert key == "ip:192.168.1.100"


def test_unknown_client_key_when_no_client():
    req = _mock_request(auth=None, headers={}, client_host=None)
    key = RateLimiterMiddleware._get_client_key(req)
    assert key == "ip:unknown"


# ---------------------------------------------------------------------------
# Tests — Per-role rate limits
# ---------------------------------------------------------------------------

def test_role_rate_limits_platform_admin():
    rpm, burst = ROLE_RATE_LIMITS["platform_admin"]
    assert rpm == 300
    assert burst == 50


def test_role_rate_limits_readonly():
    rpm, burst = ROLE_RATE_LIMITS["readonly"]
    assert rpm == 60
    assert burst == 10
