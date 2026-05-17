"""多租户配额管理：按租户追踪 token 用量、API 调用次数，执行配额检查。"""

from __future__ import annotations

import logging
import time
from typing import Any

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


# 默认配额 — 未显式设置的租户使用此值
DEFAULT_QUOTA = TenantQuota(tenant_id="__default__")


class TenantQuotaManager:
    """按租户管理配额与用量，支持日重置。"""

    def __init__(self) -> None:
        self._quotas: dict[str, TenantQuota] = {}
        self._usage: dict[str, TenantUsage] = {}

    # ── 配额管理 ─────────────────────────────────────────────

    def set_quota(self, quota: TenantQuota) -> None:
        """设置或更新租户配额。"""
        self._quotas[quota.tenant_id] = quota

    def get_quota(self, tenant_id: str) -> TenantQuota:
        """获取租户配额，不存在则返回默认值。"""
        return self._quotas.get(tenant_id, DEFAULT_QUOTA)

    def list_quotas(self) -> list[TenantQuota]:
        return list(self._quotas.values())

    # ── 用量追踪 ─────────────────────────────────────────────

    def _get_usage(self, tenant_id: str) -> TenantUsage:
        if tenant_id not in self._usage:
            self._usage[tenant_id] = TenantUsage(tenant_id=tenant_id)
        usage = self._usage[tenant_id]
        self._maybe_reset(usage)
        return usage

    def _maybe_reset(self, usage: TenantUsage) -> None:
        """每 24 小时重置日用量。"""
        now = time.time()
        if now - usage.last_reset >= 86400:
            usage.requests_today = 0
            usage.tokens_today = 0
            usage.last_reset = now

    def record_request(self, tenant_id: str, tokens: int = 0) -> None:
        """记录一次 API 调用和 token 用量。"""
        usage = self._get_usage(tenant_id)
        usage.requests_today += 1
        usage.tokens_today += tokens

    def record_storage(self, tenant_id: str, storage_mb: float) -> None:
        """更新租户的存储用量。"""
        usage = self._get_usage(tenant_id)
        usage.storage_mb = storage_mb

    def record_agent_count(self, tenant_id: str, count: int) -> None:
        """更新租户的 agent 数量。"""
        usage = self._get_usage(tenant_id)
        usage.agent_count = count

    def get_usage(self, tenant_id: str) -> TenantUsage:
        """获取租户用量快照。"""
        return self._get_usage(tenant_id)

    # ── 配额检查 ─────────────────────────────────────────────

    def check_request_quota(self, tenant_id: str) -> None:
        """检查请求配额，超限则抛出 QuotaExceededError。"""
        quota = self.get_quota(tenant_id)
        usage = self._get_usage(tenant_id)
        if usage.requests_today >= quota.max_requests_per_day:
            raise QuotaExceededError(
                tenant_id, "requests_per_day",
                quota.max_requests_per_day, usage.requests_today,
            )

    def check_token_quota(self, tenant_id: str, additional_tokens: int = 0) -> None:
        """检查 token 配额。"""
        quota = self.get_quota(tenant_id)
        usage = self._get_usage(tenant_id)
        projected = usage.tokens_today + additional_tokens
        if projected > quota.max_tokens_per_day:
            raise QuotaExceededError(
                tenant_id, "tokens_per_day",
                quota.max_tokens_per_day, usage.tokens_today,
            )

    def check_agent_quota(self, tenant_id: str) -> None:
        """检查 agent 数量配额。"""
        quota = self.get_quota(tenant_id)
        usage = self._get_usage(tenant_id)
        if usage.agent_count >= quota.max_agents:
            raise QuotaExceededError(
                tenant_id, "max_agents",
                quota.max_agents, usage.agent_count,
            )

    def check_all(self, tenant_id: str) -> list[str]:
        """执行全部配额检查，返回违规项列表（空表示全部通过）。"""
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
        """生成租户的配额使用报告。"""
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
