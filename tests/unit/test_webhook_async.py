import json

from fastapi.testclient import TestClient

from agent_platform.api.app import create_app
from agent_platform.config import get_settings


def _make_devflow_app(monkeypatch):
    monkeypatch.setenv("PLANE_BASE_URL", "http://plane.local")
    monkeypatch.setenv("PLANE_API_KEY", "plane-key")
    monkeypatch.setenv("PLANE_WORKSPACE_SLUG", "ws")
    monkeypatch.setenv("GITLAB_BASE_URL", "http://gitlab.local")
    monkeypatch.setenv("GITLAB_TOKEN", "gitlab-token")
    monkeypatch.setenv("GITLAB_PROJECT_ID", "123")
    monkeypatch.delenv("PLANE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    get_settings.cache_clear()
    app = create_app()
    get_settings.cache_clear()
    return app


def test_webhook_returns_accepted_immediately(monkeypatch):
    app = _make_devflow_app(monkeypatch)
    client = TestClient(app)

    response = client.post(
        "/api/v1/integrations/plane/webhook",
        content=json.dumps({"data": {"id": "wi-1", "state_detail": {"name": "Backlog"}}}),
        headers={
            "x-plane-event": "work_item.updated",
            "x-plane-delivery": "delivery-001",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "accepted"


def test_webhook_devflow_status_queued_when_devflow_enabled(monkeypatch):
    app = _make_devflow_app(monkeypatch)
    client = TestClient(app)

    response = client.post(
        "/api/v1/integrations/plane/webhook",
        content=json.dumps({"data": {"id": "wi-2"}}),
        headers={
            "x-plane-event": "work_item.updated",
            "x-plane-delivery": "delivery-002",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["devflow_status"] == "queued"


def test_webhook_duplicate_delivery_returns_duplicate(monkeypatch):
    app = _make_devflow_app(monkeypatch)
    client = TestClient(app)

    first = client.post(
        "/api/v1/integrations/plane/webhook",
        content=json.dumps({"data": {}}),
        headers={
            "x-plane-event": "work_item.updated",
            "x-plane-delivery": "dup-001",
        },
    )
    assert first.status_code == 200
    assert first.json()["status"] == "accepted"

    second = client.post(
        "/api/v1/integrations/plane/webhook",
        content=json.dumps({"data": {}}),
        headers={
            "x-plane-event": "work_item.updated",
            "x-plane-delivery": "dup-001",
        },
    )
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"


def test_webhook_no_devflow_status_when_devflow_disabled():
    from agent_platform.api.app import app

    client = TestClient(app)

    response = client.post(
        "/api/v1/integrations/plane/webhook",
        content=json.dumps({"data": {}}),
        headers={
            "x-plane-event": "work_item.updated",
            "x-plane-delivery": "delivery-no-devflow",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "accepted"
    assert "devflow_status" not in data
