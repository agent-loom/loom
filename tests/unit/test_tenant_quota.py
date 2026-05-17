"""TenantQuotaManager + InMemoryQuotaBackend 的单元测试。"""

from __future__ import annotations

import time

import pytest

from agent_platform.api.tenant_quota import (
    DEFAULT_QUOTA,
    InMemoryQuotaBackend,
    QuotaExceededError,
    TenantQuota,
    TenantQuotaManager,
    TenantUsage,
)


@pytest.fixture
def manager():
    return TenantQuotaManager()


class TestInMemoryQuotaBackend:
    @pytest.mark.asyncio
    async def test_save_and_get_quota(self):
        backend = InMemoryQuotaBackend()
        q = TenantQuota(tenant_id="t1", max_requests_per_day=100)
        await backend.save_quota(q)
        result = await backend.get_quota("t1")
        assert result is not None
        assert result.max_requests_per_day == 100

    @pytest.mark.asyncio
    async def test_get_nonexistent_quota_returns_none(self):
        backend = InMemoryQuotaBackend()
        assert await backend.get_quota("nope") is None

    @pytest.mark.asyncio
    async def test_save_and_get_usage(self):
        backend = InMemoryQuotaBackend()
        u = TenantUsage(tenant_id="t1", requests_today=5, tokens_today=100)
        await backend.save_usage(u)
        result = await backend.get_usage("t1")
        assert result is not None
        assert result.requests_today == 5

    @pytest.mark.asyncio
    async def test_list_quotas(self):
        backend = InMemoryQuotaBackend()
        await backend.save_quota(TenantQuota(tenant_id="t1"))
        await backend.save_quota(TenantQuota(tenant_id="t2"))
        quotas = await backend.list_quotas()
        assert len(quotas) == 2


class TestTenantQuotaManager:
    def test_default_quota_returned(self, manager):
        q = manager.get_quota("unknown-tenant")
        assert q.max_requests_per_day == DEFAULT_QUOTA.max_requests_per_day

    def test_set_and_get_quota(self, manager):
        manager.set_quota(TenantQuota(tenant_id="t1", max_requests_per_day=50))
        q = manager.get_quota("t1")
        assert q.max_requests_per_day == 50

    def test_record_request_increments(self, manager):
        manager.record_request("t1", tokens=100)
        usage = manager.get_usage("t1")
        assert usage.requests_today == 1
        assert usage.tokens_today == 100

    def test_record_multiple_requests(self, manager):
        for _ in range(5):
            manager.record_request("t1", tokens=10)
        usage = manager.get_usage("t1")
        assert usage.requests_today == 5
        assert usage.tokens_today == 50

    def test_check_request_quota_passes(self, manager):
        manager.set_quota(TenantQuota(tenant_id="t1", max_requests_per_day=100))
        manager.record_request("t1")
        manager.check_request_quota("t1")

    def test_check_request_quota_exceeds(self, manager):
        manager.set_quota(TenantQuota(tenant_id="t1", max_requests_per_day=2))
        manager.record_request("t1")
        manager.record_request("t1")
        with pytest.raises(QuotaExceededError, match="requests_per_day"):
            manager.check_request_quota("t1")

    def test_check_token_quota_exceeds(self, manager):
        manager.set_quota(TenantQuota(tenant_id="t1", max_tokens_per_day=100))
        manager.record_request("t1", tokens=80)
        with pytest.raises(QuotaExceededError, match="tokens_per_day"):
            manager.check_token_quota("t1", additional_tokens=30)

    def test_check_agent_quota_exceeds(self, manager):
        manager.set_quota(TenantQuota(tenant_id="t1", max_agents=2))
        manager.record_agent_count("t1", 2)
        with pytest.raises(QuotaExceededError, match="max_agents"):
            manager.check_agent_quota("t1")

    def test_check_all_returns_violations(self, manager):
        manager.set_quota(TenantQuota(tenant_id="t1", max_requests_per_day=1))
        manager.record_request("t1")
        violations = manager.check_all("t1")
        assert len(violations) >= 1
        assert any("requests" in v for v in violations)

    def test_check_all_no_violations(self, manager):
        manager.set_quota(TenantQuota(tenant_id="t1", max_requests_per_day=100))
        violations = manager.check_all("t1")
        assert len(violations) == 0

    def test_daily_reset(self, manager):
        manager.record_request("t1", tokens=10)
        usage = manager.get_usage("t1")
        usage.last_reset = time.time() - 86401
        manager.record_request("t1", tokens=5)
        refreshed = manager.get_usage("t1")
        assert refreshed.requests_today == 1
        assert refreshed.tokens_today == 5

    def test_tenant_report(self, manager):
        manager.set_quota(TenantQuota(tenant_id="t1", max_requests_per_day=100))
        manager.record_request("t1", tokens=50)
        report = manager.get_tenant_report("t1")
        assert report["tenant_id"] == "t1"
        assert report["utilization"]["requests_pct"] == 1.0

    def test_record_storage(self, manager):
        manager.record_storage("t1", 512.5)
        usage = manager.get_usage("t1")
        assert usage.storage_mb == 512.5

    def test_list_quotas(self, manager):
        manager.set_quota(TenantQuota(tenant_id="t1"))
        manager.set_quota(TenantQuota(tenant_id="t2"))
        assert len(manager.list_quotas()) == 2


class TestTenantQuotaManagerAsync:
    @pytest.mark.asyncio
    async def test_set_quota_async(self):
        backend = InMemoryQuotaBackend()
        mgr = TenantQuotaManager(backend=backend)
        await mgr.set_quota_async(TenantQuota(tenant_id="t1", max_requests_per_day=42))
        q = mgr.get_quota("t1")
        assert q.max_requests_per_day == 42
        persisted = await backend.get_quota("t1")
        assert persisted is not None
        assert persisted.max_requests_per_day == 42

    @pytest.mark.asyncio
    async def test_get_quota_async_from_backend(self):
        backend = InMemoryQuotaBackend()
        await backend.save_quota(TenantQuota(tenant_id="t1", max_requests_per_day=77))
        mgr = TenantQuotaManager(backend=backend)
        q = await mgr.get_quota_async("t1")
        assert q.max_requests_per_day == 77

    @pytest.mark.asyncio
    async def test_record_request_async(self):
        backend = InMemoryQuotaBackend()
        mgr = TenantQuotaManager(backend=backend)
        await mgr.record_request_async("t1", tokens=200)
        usage = mgr.get_usage("t1")
        assert usage.requests_today == 1
        assert usage.tokens_today == 200
        persisted = await backend.get_usage("t1")
        assert persisted is not None
        assert persisted.requests_today == 1

    @pytest.mark.asyncio
    async def test_sync_from_backend(self):
        backend = InMemoryQuotaBackend()
        await backend.save_usage(
            TenantUsage(tenant_id="t1", requests_today=10, tokens_today=500)
        )
        mgr = TenantQuotaManager(backend=backend)
        await mgr.sync_from_backend("t1")
        usage = mgr.get_usage("t1")
        assert usage.requests_today == 10
