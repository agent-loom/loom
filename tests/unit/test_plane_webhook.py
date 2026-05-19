import hashlib
import hmac
import os
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from agent_platform.config import get_settings
from agent_platform.integrations.plane.webhook import PlaneWebhookVerifier


def test_plane_webhook_verifier_accepts_valid_signature():
    body = b'{"event":"work_item.created"}'
    secret = "secret"
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    PlaneWebhookVerifier(secret).verify(body, signature)


def test_plane_webhook_endpoint_accepts_without_secret():
    # 明确清除 webhook secret，测试无鉴权场景
    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": "",
            "PLANE_WEBHOOK_SECRET": "",
            "PLANE_BASE_URL": "",
            "PLANE_API_KEY": "",
            "GITLAB_BASE_URL": "",
            "GITLAB_TOKEN": "",
            "GITLAB_PROJECT_ID": "",
        },
        clear=False,
    ):
        get_settings.cache_clear()
        from agent_platform.api.app import create_app
        test_app = create_app()
    get_settings.cache_clear()

    client = TestClient(test_app)

    response = client.post(
        "/api/v1/integrations/plane/webhook",
        json={"event": "work_item.created"},
        headers={
            "X-Plane-Delivery": f"delivery-{uuid4().hex}",
            "X-Plane-Event": "work_item.created",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


def test_plane_webhook_skips_platform_api_key_but_requires_signature():
    body = b'{"event":"work_item.created"}'
    secret = "plane-secret"
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": "",
            "AGENT_PLATFORM_API_KEY": "platform-key",
            "PLANE_WEBHOOK_SECRET": secret,
            "PLANE_BASE_URL": "",
            "PLANE_API_KEY": "",
            "GITLAB_BASE_URL": "",
            "GITLAB_TOKEN": "",
            "GITLAB_PROJECT_ID": "",
        },
        clear=False,
    ):
        get_settings.cache_clear()
        from agent_platform.api.app import create_app

        test_app = create_app()
    get_settings.cache_clear()

    client = TestClient(test_app)

    response = client.post(
        "/api/v1/integrations/plane/webhook",
        content=body,
        headers={
            "X-Plane-Delivery": f"delivery-signed-{uuid4().hex}",
            "X-Plane-Event": "work_item.created",
            "X-Plane-Signature": f"sha256={signature}",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


def test_plane_webhook_rejects_invalid_signature_even_when_api_key_is_skipped():
    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": "",
            "AGENT_PLATFORM_API_KEY": "platform-key",
            "PLANE_WEBHOOK_SECRET": "plane-secret",
            "PLANE_BASE_URL": "",
            "PLANE_API_KEY": "",
            "GITLAB_BASE_URL": "",
            "GITLAB_TOKEN": "",
            "GITLAB_PROJECT_ID": "",
        },
        clear=False,
    ):
        get_settings.cache_clear()
        from agent_platform.api.app import create_app

        test_app = create_app()
    get_settings.cache_clear()

    client = TestClient(test_app)

    response = client.post(
        "/api/v1/integrations/plane/webhook",
        content=b'{"event":"work_item.created"}',
        headers={
            "X-Plane-Delivery": f"delivery-bad-signature-{uuid4().hex}",
            "X-Plane-Event": "work_item.created",
            "X-Plane-Signature": "sha256=bad",
        },
    )

    assert response.status_code == 401
