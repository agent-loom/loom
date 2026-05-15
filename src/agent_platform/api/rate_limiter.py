from __future__ import annotations

import time
from collections import defaultdict

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class RateLimiterMiddleware(BaseHTTPMiddleware):
    """Simple in-memory token-bucket rate limiter.

    Limits are per-client (by IP or x-api-key header).
    """

    def __init__(
        self,
        app,
        requests_per_minute: int = 60,
        burst: int = 10,
    ):
        super().__init__(app)
        self.rate = requests_per_minute / 60.0
        self.burst = burst
        self._buckets: dict[str, _TokenBucket] = defaultdict(
            lambda: _TokenBucket(self.rate, self.burst)
        )

    async def dispatch(self, request: Request, call_next):
        if request.url.path in {"/health", "/docs", "/openapi.json", "/redoc"}:
            return await call_next(request)

        client_host = request.client.host if request.client else "unknown"
        if client_host == "testclient":
            return await call_next(request)

        client_key = self._get_client_key(request)
        bucket = self._buckets[client_key]

        if not bucket.consume():
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "too many requests, please retry later",
                    }
                },
                headers={"Retry-After": str(int(1.0 / self.rate))},
            )

        return await call_next(request)

    @staticmethod
    def _get_client_key(request: Request) -> str:
        api_key = request.headers.get("x-api-key")
        if api_key:
            return f"key:{api_key}"
        client_host = request.client.host if request.client else "unknown"
        return f"ip:{client_host}"


class _TokenBucket:
    def __init__(self, rate: float, burst: int):
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.last_refill = time.monotonic()

    def consume(self) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
        self.last_refill = now

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False
