import hashlib
import hmac
from dataclasses import dataclass
from typing import Any


class PlaneWebhookError(ValueError):
    pass


@dataclass(frozen=True)
class PlaneWebhookEvent:
    delivery_id: str
    event: str
    payload: dict[str, Any]


class PlaneWebhookVerifier:
    def __init__(self, secret: str):
        self.secret = secret.encode()

    def verify(self, raw_body: bytes, signature: str | None) -> None:
        if not signature:
            raise PlaneWebhookError("missing Plane webhook signature")

        expected = hmac.new(self.secret, raw_body, hashlib.sha256).hexdigest()
        normalized = signature.removeprefix("sha256=")
        if not hmac.compare_digest(expected, normalized):
            raise PlaneWebhookError("invalid Plane webhook signature")

