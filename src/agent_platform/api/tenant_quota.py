"""多租户配额管理：按租户追踪 token 用量、API 调用次数，执行配额检查。

支持内存和 Redis 两种持久化后端。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TenantQuota(BaseModel):
    """单租户的配额设置。"""

    tenant_id: str
    max_requests_per_day: int = 10000
    max_tokens_per_day: int = 5_000_000
    max_storage_mb: int = 1024
    max_agents: int = 50


class TenantUsage(BaseModel):
    """单租户的实时用量快照。"""

    tenant_id: str
    requests_today: int = 0
    tokens_today: int = 0
    storage_mb: float = 0.0
    agent_count: int = 0
    last_reset: float = Field(default_factory=time.time)


class QuotaExceededError(Exception):
    """配额超限异常。"""

    def __init__(self, tenant_id: str, resource: str, limit: int, current: int):
        self.tenant_id = tenant_id
        self.resource = resource
        self.limit = limit
        self.current = current
        super().__init__(
            f"租户 {tenant_id} 的 {resource} 配额已用尽: "
            f"当前 {current}/{limit}"
        )


DEFAULT_QUOTA = TenantQuota(tenant_id="__default__")


@runtime_checkable
class QuotaBackend(Protocol):
    """配额存储后端协议。"""

    async def get_usage(self, tenant_id: str) -> TenantUsage | None: ...
    async def save_usage(self, usage: TenantUsage) -> None: ...
    async def get_quota(self, tenant_id: str) -> TenantQuota | None: ...
    async def save_quota(self, quota: TenantQuota) -> None: ...
    async def list_quotas(self) -> list[TenantQuota]: ...


class InMemoryQuotaBackend:
    """基于进程内字典的配额后端，适用于单实例部署。"""

    def __init__(self) -> None:
        self._quotas: dict[str, TenantQuota] = {}
        self._usage: dict[str, TenantUsage] = {}

    async def get_usage(self, tenant_id: str) -> TenantUsage | None:
        return self._usage.get(tenant_id)

    async def save_usage(self, usage: TenantUsage) -> None:
        self._usage[usage.tenant_id] = usage

    async def get_quota(self, tenant_id: str) -> TenantQuota | None:
        return self._quotas.get(tenant_id)

    async def save_quota(self, quota: TenantQuota) -> None:
        self._quotas[quota.tenant_id] = quota

    async def list_quotas(self) -> list[TenantQuota]:
        return list(self._quotas.values())


class RedisQuotaBackend:
    """基于 Redis 的配额后端，支持跨实例共享用量状态。

    键结构:
      quota:{tenant_id} — 配额设置 JSON
      usage:{tenant_id} — 用量计数器 Hash
      quota:__index__   — 所有配置了配额的租户 ID 集合
    """

    def __init__(self, redis_client) -> None:
        self._redis = redis_client

    async def get_usage(self, tenant_id: str) -> TenantUsage | None:
        key = f"usage:{tenant_id}"
        try:
            data = await self._redis.hgetall(key)
            if not data:
                return None
            return TenantUsage(
                tenant_id=tenant_id,
                requests_today=int(data.get(b"requests_today", data.get("requests_today", 0))),
                tokens_today=int(data.get(b"tokens_today", data.get("tokens_today", 0))),
                storage_mb=float(data.get(b"storage_mb", data.get("storage_mb", 0))),
                agent_count=int(data.get(b"agent_count", data.get("agent_count", 0))),
                last_reset=float(data.get(b"last_reset", data.get("last_reset", time.time()))),
            )
        except Exception:
            logger.warning("Redis 配额后端读取失败", exc_info=True)
            return None

    async def save_usage(self, usage: TenantUsage) -> None:
        key = f"usage:{usage.tenant_id}"
        try:
            await self._redis.hset(key, mapping={
                "requests_today": str(usage.requests_today),
                "tokens_today": str(usage.tokens_today),
                "storage_mb": str(usage.storage_mb),
                "agent_count": str(usage.agent_count),
                "last_reset": str(usage.last_reset),
            })
            remaining = max(0, 86400 - int(time.time() - usage.last_reset))
            await self._redis.expire(key, remaining or 86400)
        except Exception:
            logger.warning("Redis 配额后端写入失败", exc_info=True)

    async def get_quota(self, tenant_id: str) -> TenantQuota | None:
        key = f"quota:{tenant_id}"
        try:
            data = await self._redis.get(key)
            if data is None:
                return None
            return TenantQuota.model_validate_json(data)
        except Exception:
            logger.warning("Redis 配额后端读取失败", exc_info=True)
            return None

    async def save_quota(self, quota: TenantQuota) -> None:
        key = f"quota:{quota.tenant_id}"
        try:
            await self._redis.set(key, quota.model_dump_json())
            await self._redis.sadd("quota:__index__", quota.tenant_id)
        except Exception:
            logger.warning("Redis 配额后端写入失败", exc_info=True)

    async def list_quotas(self) -> list[TenantQuota]:
        try:
            members = await self._redis.smembers("quota:__index__")
            result = []
            for tid in members:
                if isinstance(tid, bytes):
                    tid = tid.decode()
                q = await self.get_quota(tid)
                if q:
                    result.append(q)
            return result
        except Exception:
            logger.warning("Redis 配额后端列表失败", exc_info=True)
            return []


class TenantQuotaManager:
    """按租户管理配额与用量，支持可插拔的存储后端。"""

    def __init__(self, backend: QuotaBackend | None = None) -> None:
        self._backend = backend or InMemoryQuotaBackend()
        self._cache_quotas: dict[str, TenantQuota] = {}
        self._cache_usage: dict[str, TenantUsage] = {}

    # ── 配额管理 ─────────────────────────────────────────────

    def set_quota(self, quota: TenantQuota) -> None:
        self._cache_quotas[quota.tenant_id] = quota

    async def set_quota_async(self, quota: TenantQuota) -> None:
        self._cache_quotas[quota.tenant_id] = quota
        await self._backend.save_quota(quota)

    def get_quota(self, tenant_id: str) -> TenantQuota:
        return self._cache_quotas.get(tenant_id, DEFAULT_QUOTA)

    async def get_quota_async(self, tenant_id: str) -> TenantQuota:
        if tenant_id in self._cache_quotas:
            return self._cache_quotas[tenant_id]
        q = await self._backend.get_quota(tenant_id)
        if q:
            self._cache_quotas[tenant_id] = q
            return q
        return DEFAULT_QUOTA

    def list_quotas(self) -> list[TenantQuota]:
        return list(self._cache_quotas.values())

    # ── 用量追踪 ─────────────────────────────────────────────

    def _get_usage(self, tenant_id: str) -> TenantUsage:
        if tenant_id not in self._cache_usage:
            self._cache_usage[tenant_id] = TenantUsage(tenant_id=tenant_id)
        usage = self._cache_usage[tenant_id]
        self._maybe_reset(usage)
        return usage

    def _maybe_reset(self, usage: TenantUsage) -> None:
        now = time.time()
        if now - usage.last_reset >= 86400:
            usage.requests_today = 0
            usage.tokens_today = 0
            usage.last_reset = now

    def record_request(self, tenant_id: str, tokens: int = 0) -> None:
        usage = self._get_usage(tenant_id)
        usage.requests_today += 1
        usage.tokens_today += tokens

    async def record_request_async(self, tenant_id: str, tokens: int = 0) -> None:
        usage = self._get_usage(tenant_id)
        usage.requests_today += 1
        usage.tokens_today += tokens
        await self._backend.save_usage(usage)

    def record_storage(self, tenant_id: str, storage_mb: float) -> None:
        usage = self._get_usage(tenant_id)
        usage.storage_mb = storage_mb

    def record_agent_count(self, tenant_id: str, count: int) -> None:
        usage = self._get_usage(tenant_id)
        usage.agent_count = count

    def get_usage(self, tenant_id: str) -> TenantUsage:
        return self._get_usage(tenant_id)

    async def sync_from_backend(self, tenant_id: str) -> None:
        """从后端加载用量到本地缓存（启动时或周期同步）。"""
        remote = await self._backend.get_usage(tenant_id)
        if remote:
            self._cache_usage[tenant_id] = remote

    # ── 配额检查 ─────────────────────────────────────────────

    def check_request_quota(self, tenant_id: str) -> None:
        quota = self.get_quota(tenant_id)
        usage = self._get_usage(tenant_id)
        if usage.requests_today >= quota.max_requests_per_day:
            raise QuotaExceededError(
                tenant_id, "requests_per_day",
                quota.max_requests_per_day, usage.requests_today,
            )

    def check_token_quota(self, tenant_id: str, additional_tokens: int = 0) -> None:
        quota = self.get_quota(tenant_id)
        usage = self._get_usage(tenant_id)
        projected = usage.tokens_today + additional_tokens
        if projected > quota.max_tokens_per_day:
            raise QuotaExceededError(
                tenant_id, "tokens_per_day",
                quota.max_tokens_per_day, usage.tokens_today,
            )

    def check_agent_quota(self, tenant_id: str) -> None:
        quota = self.get_quota(tenant_id)
        usage = self._get_usage(tenant_id)
        if usage.agent_count >= quota.max_agents:
            raise QuotaExceededError(
                tenant_id, "max_agents",
                quota.max_agents, usage.agent_count,
            )

    def check_all(self, tenant_id: str) -> list[str]:
        violations: list[str] = []
        quota = self.get_quota(tenant_id)
        usage = self._get_usage(tenant_id)
        if usage.requests_today >= quota.max_requests_per_day:
            violations.append(
                f"requests: {usage.requests_today}/{quota.max_requests_per_day}"
            )
        if usage.tokens_today >= quota.max_tokens_per_day:
            violations.append(
                f"tokens: {usage.tokens_today}/{quota.max_tokens_per_day}"
            )
        if usage.storage_mb >= quota.max_storage_mb:
            violations.append(
                f"storage: {usage.storage_mb:.1f}/{quota.max_storage_mb} MB"
            )
        if usage.agent_count >= quota.max_agents:
            violations.append(
                f"agents: {usage.agent_count}/{quota.max_agents}"
            )
        return violations

    # ── 报告 ─────────────────────────────────────────────────

    def get_tenant_report(self, tenant_id: str) -> dict[str, Any]:
        quota = self.get_quota(tenant_id)
        usage = self._get_usage(tenant_id)
        return {
            "tenant_id": tenant_id,
            "quota": quota.model_dump(),
            "usage": usage.model_dump(),
            "utilization": {
                "requests_pct": round(
                    usage.requests_today / max(1, quota.max_requests_per_day) * 100, 1,
                ),
                "tokens_pct": round(
                    usage.tokens_today / max(1, quota.max_tokens_per_day) * 100, 1,
                ),
                "storage_pct": round(
                    usage.storage_mb / max(1, quota.max_storage_mb) * 100, 1,
                ),
                "agents_pct": round(
                    usage.agent_count / max(1, quota.max_agents) * 100, 1,
                ),
            },
        }
