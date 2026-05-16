"""Plane Webhook 签名验证与事件解析。"""

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any


class PlaneWebhookError(ValueError):
    """Plane Webhook 验证失败时抛出的异常。"""
    pass


@dataclass(frozen=True)
class PlaneWebhookEvent:
    """解析后的 Plane Webhook 事件。"""
    delivery_id: str
    event: str
    payload: dict[str, Any]


class PlaneWebhookVerifier:
    """使用 HMAC-SHA256 验证 Plane Webhook 请求签名。"""

    def __init__(self, secret: str):
        """初始化验证器。"""
        self.secret = secret.encode()

    def verify(self, raw_body: bytes, signature: str | None) -> None:
        """验证请求签名，签名无效时抛出 PlaneWebhookError。"""
        if not signature:
            raise PlaneWebhookError("missing Plane webhook signature")

        expected = hmac.new(self.secret, raw_body, hashlib.sha256).hexdigest()
        normalized = signature.removeprefix("sha256=")
        if not hmac.compare_digest(expected, normalized):
            raise PlaneWebhookError("invalid Plane webhook signature")

