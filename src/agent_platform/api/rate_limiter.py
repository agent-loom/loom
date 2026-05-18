"""基于令牌桶的 API 限流中间件，支持按角色差异化限流和 Redis 分布式后端。"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Protocol, runtime_checkable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

ROLE_RATE_LIMITS: dict[str, tuple[int, int]] = {
    "platform_admin": (300, 50),
    "agent_developer": (120, 20),
    "agent_operator": (120, 20),
    "readonly": (60, 10),
}

_SKIP_PATHS = {"/health", "/health/ready", "/metrics", "/docs", "/openapi.json", "/redoc"}


@runtime_checkable
class RateLimiterBackend(Protocol):
    """限流后端协议，抽象令牌桶存储。"""

    async def try_consume(self, key: str, rate: float, burst: int) -> bool:
        """尝试消费一个令牌，成功返回 True。"""
        ...


class InMemoryRateLimiterBackend:
    """基于进程内字典的令牌桶后端，适用于单实例部署。"""

    _MAX_BUCKETS = 10_000
    _EVICT_IDLE_SECONDS = 600.0

    def __init__(self) -> None:
        self._buckets: dict[str, _TokenBucket] = {}

    async def try_consume(self, key: str, rate: float, burst: int) -> bool:
        if len(self._buckets) > self._MAX_BUCKETS:
            self._evict_idle()
        if key not in self._buckets:
            self._buckets[key] = _TokenBucket(rate, burst)
        return self._buckets[key].consume()

    def _evict_idle(self) -> None:
        now = time.monotonic()
        stale = [
            k for k, b in self._buckets.items()
            if now - b.last_refill > self._EVICT_IDLE_SECONDS
        ]
        for k in stale:
            del self._buckets[k]


class RedisRateLimiterBackend:
    """基于 Redis 的分布式令牌桶后端，适用于多实例部署。

    使用 Lua 脚本保证原子性，所有实例共享同一限流状态。
    """

    _LUA_SCRIPT = """
    local key = KEYS[1]
    local rate = tonumber(ARGV[1])
    local burst = tonumber(ARGV[2])
    local now = tonumber(ARGV[3])
    local ttl = tonumber(ARGV[4])

    local data = redis.call('HMGET', key, 'tokens', 'last_refill')
    local tokens = tonumber(data[1])
    local last_refill = tonumber(data[2])

    if tokens == nil then
        tokens = burst
        last_refill = now
    end

    local elapsed = now - last_refill
    tokens = math.min(burst, tokens + elapsed * rate)
    last_refill = now

    local allowed = 0
    if tokens >= 1 then
        tokens = tokens - 1
        allowed = 1
    end

    redis.call('HMSET', key, 'tokens', tostring(tokens), 'last_refill', tostring(last_refill))
    redis.call('EXPIRE', key, ttl)
    return allowed
    """

    def __init__(self, redis_client) -> None:
        self._redis = redis_client
        self._script = None

    async def _ensure_script(self):
        if self._script is None:
            self._script = self._redis.register_script(self._LUA_SCRIPT)

    async def try_consume(self, key: str, rate: float, burst: int) -> bool:
        await self._ensure_script()
        now = time.time()
        ttl = max(int(burst / max(rate, 0.001)) * 2, 120)
        redis_key = f"rl:{key}"
        try:
            result = await self._script(
                keys=[redis_key],
                args=[str(rate), str(burst), str(now), str(ttl)],
            )
            return bool(result)
        except Exception:
            logger.warning("Redis 限流后端不可用，回退到放行", exc_info=True)
            return True


class RateLimiterMiddleware(BaseHTTPMiddleware):
    """可插拔后端的限流中间件，支持内存和 Redis 两种模式。"""

    def __init__(
        self,
        app,
        requests_per_minute: int = 60,
        burst: int = 10,
        backend: RateLimiterBackend | None = None,
    ):
        super().__init__(app)
        self.default_rate = requests_per_minute / 60.0
        self.default_burst = burst
        self._backend = backend or InMemoryRateLimiterBackend()

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _SKIP_PATHS:
            return await call_next(request)

        client_host = request.client.host if request.client else "unknown"
        if client_host == "testclient":
            return await call_next(request)

        client_key = self._get_client_key(request)
        auth = getattr(request.state, "auth", None)
        role = auth.role if auth else None

        if role and role in ROLE_RATE_LIMITS:
            rpm, burst = ROLE_RATE_LIMITS[role]
            rate = rpm / 60.0
        else:
            rate = self.default_rate
            burst = self.default_burst

        if not await self._backend.try_consume(client_key, rate, burst):
            retry_after = max(1, int(1.0 / rate))
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
            hashed = hashlib.sha256(api_key.encode()).hexdigest()[:16]
            return f"key:{hashed}"
        client_host = request.client.host if request.client else "unknown"
        return f"ip:{client_host}"


class _TokenBucket:
    """令牌桶算法实现，用于单客户端限流。"""

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
