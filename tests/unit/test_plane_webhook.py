import hashlib
import hmac

from fastapi.testclient import TestClient

from agent_platform.api.app import app
from agent_platform.integrations.plane.webhook import PlaneWebhookVerifier


def test_plane_webhook_verifier_accepts_valid_signature():
    body = b'{"event":"work_item.created"}'
    secret = "secret"
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    PlaneWebhookVerifier(secret).verify(body, signature)


def test_plane_webhook_endpoint_accepts_without_secret():
    client = TestClient(app)

    response = client.post(
        "/api/v1/integrations/plane/webhook",
        json={"event": "work_item.created"},
        headers={
            "X-Plane-Delivery": "delivery-1",
            "X-Plane-Event": "work_item.created",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"

