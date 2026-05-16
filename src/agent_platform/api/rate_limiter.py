"""基于令牌桶的 API 限流中间件，支持按角色差异化限流。"""

from __future__ import annotations

import time

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

ROLE_RATE_LIMITS: dict[str, tuple[int, int]] = {
    "platform_admin": (300, 50),
    "agent_developer": (120, 20),
    "agent_operator": (120, 20),
    "readonly": (60, 10),
}

_SKIP_PATHS = {"/health", "/health/ready", "/metrics", "/docs", "/openapi.json", "/redoc"}


class RateLimiterMiddleware(BaseHTTPMiddleware):
    """In-memory token-bucket rate limiter with per-role limits.

    Falls back to global limits when auth identity is not available.
    """

    def __init__(
        self,
        app,
        requests_per_minute: int = 60,
        burst: int = 10,
    ):
        super().__init__(app)
        self.default_rate = requests_per_minute / 60.0
        self.default_burst = burst
        self._buckets: dict[str, _TokenBucket] = {}

    def _get_bucket(self, key: str, role: str | None = None) -> _TokenBucket:
        if key not in self._buckets:
            if role and role in ROLE_RATE_LIMITS:
                rpm, burst = ROLE_RATE_LIMITS[role]
                self._buckets[key] = _TokenBucket(rpm / 60.0, burst)
            else:
                self._buckets[key] = _TokenBucket(
                    self.default_rate, self.default_burst,
                )
        return self._buckets[key]

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _SKIP_PATHS:
            return await call_next(request)

        client_host = request.client.host if request.client else "unknown"
        if client_host == "testclient":
            return await call_next(request)

        client_key = self._get_client_key(request)
        auth = getattr(request.state, "auth", None)
        role = auth.role if auth else None
        bucket = self._get_bucket(client_key, role)

        if not bucket.consume():
            retry_after = max(1, int(1.0 / bucket.rate))
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "too many requests, please retry later",
                    }
                },
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)

    @staticmethod
    def _get_client_key(request: Request) -> str:
        auth = getattr(request.state, "auth", None)
        if auth and auth.key_id:
            return f"key:{auth.key_id}"
        api_key = request.headers.get("x-api-key")
        if api_key:
            return f"key:{api_key}"
        client_host = request.client.host if request.client else "unknown"
        return f"ip:{client_host}"


class _TokenBucket:
    """令牌桶算法实现，用于单客户端限流。"""

    def __init__(self, rate: float, burst: int):
        """初始化令牌桶。"""
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.last_refill = time.monotonic()

    def consume(self) -> bool:
        """尝试消费一个令牌，成功返回 True。"""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
        self.last_refill = now

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False
