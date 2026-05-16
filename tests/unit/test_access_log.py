"""Tests for AccessLogMiddleware — src/agent_platform/api/access_log.py"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.access_log import AccessLogMiddleware


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AccessLogMiddleware)

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/api/v1/test")
    async def test():
        return {"data": "ok"}

    return app


def test_health_not_logged(caplog):
    client = TestClient(_make_app())
    with caplog.at_level(logging.INFO, logger="agent_platform.access"):
        caplog.clear()
        client.get("/health")
    access_records = [
        r for r in caplog.records if r.name == "agent_platform.access"
    ]
    assert len(access_records) == 0


def test_api_request_logged(caplog):
    client = TestClient(_make_app())
    with caplog.at_level(logging.INFO, logger="agent_platform.access"):
        caplog.clear()
        client.get("/api/v1/test")
    access_records = [
        r for r in caplog.records if r.name == "agent_platform.access"
    ]
    assert len(access_records) == 1
    record = access_records[0]
    assert record.method == "GET"
    assert record.path == "/api/v1/test"
    assert record.status == 200
    assert isinstance(record.latency_ms, float)


def test_metrics_not_logged(caplog):
    app = FastAPI()
    app.add_middleware(AccessLogMiddleware)

    @app.get("/metrics")
    async def metrics():
        return "ok"

    client = TestClient(app)
    with caplog.at_level(logging.INFO, logger="agent_platform.access"):
        caplog.clear()
        client.get("/metrics")
    access_records = [
        r for r in caplog.records if r.name == "agent_platform.access"
    ]
    assert len(access_records) == 0
