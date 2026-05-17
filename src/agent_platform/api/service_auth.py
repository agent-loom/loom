"""服务间鉴权：JWT / Shared Secret 双模式，用于内部服务调用认证。"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ServiceIdentity(BaseModel):
    """内部服务调用者身份。"""

    service_id: str
    service_name: str = ""
    permissions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ServiceToken(BaseModel):
    """服务间 JWT 令牌的载荷结构。"""

    sub: str  # service_id
    name: str = ""
    permissions: list[str] = Field(default_factory=list)
    iat: int = 0  # issued at (Unix timestamp)
    exp: int = 0  # expiration (Unix timestamp)


class ServiceAuthError(Exception):
    """服务间鉴权失败异常。"""


class ServiceAuthProvider:
    """服务间鉴权提供者，支持 JWT (HMAC-SHA256) 和 Shared Secret 两种模式。

    JWT 模式：签发和验证基于 HMAC-SHA256 的 JWT 令牌。
    Shared Secret 模式：验证请求头中的预共享密钥。
    """

    def __init__(
        self,
        *,
        jwt_secret: str | None = None,
        shared_secrets: dict[str, str] | None = None,
        token_ttl_seconds: int = 3600,
    ):
        """初始化鉴权提供者。

        Args:
            jwt_secret: HMAC-SHA256 签名密钥，None 则禁用 JWT 模式
            shared_secrets: service_id → secret 映射，None 则禁用 shared secret 模式
            token_ttl_seconds: JWT 令牌有效期（秒）
        """
        self._jwt_secret = jwt_secret
        self._shared_secrets = shared_secrets or {}
        self._token_ttl = token_ttl_seconds

    # ── JWT 模式 ─────────────────────────────────────────

    def issue_token(self, identity: ServiceIdentity) -> str:
        """为指定服务签发 JWT 令牌。"""
        if not self._jwt_secret:
            raise ServiceAuthError("JWT secret not configured")

        now = int(time.time())
        payload = ServiceToken(
            sub=identity.service_id,
            name=identity.service_name,
            permissions=identity.permissions,
            iat=now,
            exp=now + self._token_ttl,
        )
        return self._encode_jwt(payload.model_dump())

    def verify_token(self, token: str) -> ServiceIdentity:
        """验证 JWT 令牌并返回服务身份。"""
        if not self._jwt_secret:
            raise ServiceAuthError("JWT secret not configured")

        payload = self._decode_jwt(token)

        now = int(time.time())
        if payload.get("exp", 0) < now:
            raise ServiceAuthError("token expired")

        return ServiceIdentity(
            service_id=payload["sub"],
            service_name=payload.get("name", ""),
            permissions=payload.get("permissions", []),
        )

    # ── Shared Secret 模式 ───────────────────────────────

    def verify_shared_secret(
        self, service_id: str, secret: str,
    ) -> ServiceIdentity:
        """验证预共享密钥并返回服务身份。"""
        expected = self._shared_secrets.get(service_id)
        if expected is None:
            raise ServiceAuthError(f"unknown service: {service_id}")

        if not hmac.compare_digest(expected, secret):
            raise ServiceAuthError("invalid shared secret")

        return ServiceIdentity(
            service_id=service_id,
            permissions=["service"],
        )

    def register_service(
        self, service_id: str, secret: str,
    ) -> None:
        """注册一个服务的共享密钥。"""
        self._shared_secrets[service_id] = secret

    # ── 统一验证入口 ─────────────────────────────────────

    def authenticate(
        self,
        *,
        token: str | None = None,
        service_id: str | None = None,
        secret: str | None = None,
    ) -> ServiceIdentity:
        """统一鉴权入口：优先 JWT，其次 Shared Secret。"""
        if token:
            return self.verify_token(token)
        if service_id and secret:
            return self.verify_shared_secret(service_id, secret)
        raise ServiceAuthError("no credentials provided")

    # ── JWT 编解码（HMAC-SHA256，不依赖 PyJWT） ─────────

    def _encode_jwt(self, payload: dict[str, Any]) -> str:
        """编码 JWT（Header.Payload.Signature）。"""
        header = {"alg": "HS256", "typ": "JWT"}
        h = _b64_encode(json.dumps(header, separators=(",", ":")))
        p = _b64_encode(json.dumps(payload, separators=(",", ":")))
        signing_input = f"{h}.{p}"
        sig = self._sign(signing_input.encode())
        return f"{signing_input}.{sig}"

    def _decode_jwt(self, token: str) -> dict[str, Any]:
        """解码并验证 JWT 签名。"""
        parts = token.split(".")
        if len(parts) != 3:
            raise ServiceAuthError("malformed JWT: expected 3 parts")

        signing_input = f"{parts[0]}.{parts[1]}"
        expected_sig = self._sign(signing_input.encode())

        if not hmac.compare_digest(parts[2], expected_sig):
            raise ServiceAuthError("invalid JWT signature")

        try:
            payload_json = _b64_decode(parts[1])
            return json.loads(payload_json)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ServiceAuthError(f"invalid JWT payload: {exc}") from exc

    def _sign(self, data: bytes) -> str:
        """HMAC-SHA256 签名。"""
        assert self._jwt_secret is not None
        digest = hmac.new(
            self._jwt_secret.encode(), data, hashlib.sha256,
        ).digest()
        return urlsafe_b64encode(digest).rstrip(b"=").decode()


def _b64_encode(data: str) -> str:
    """URL-safe Base64 编码（去除填充 =）。"""
    return urlsafe_b64encode(data.encode()).rstrip(b"=").decode()


def _b64_decode(data: str) -> str:
    """URL-safe Base64 解码（自动补齐填充 =）。"""
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    return urlsafe_b64decode(data).decode()
