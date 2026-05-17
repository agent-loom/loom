"""ModelGateway 自动注册、配额计数、审计链初始化等生产修复的测试。"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from agent_platform.runtime.model_gateway import ModelGateway


class TestModelGatewayAutoRegister:
    def test_default_stub_only(self):
        with patch.dict(os.environ, {}, clear=True):
            gw = ModelGateway.create_default()
        assert gw._default_provider == "stub"
        assert "stub" in gw._providers

    def test_registers_openai_when_key_set(self):
        env = {"OPENAI_API_KEY": "sk-test-123"}
        with patch.dict(os.environ, env, clear=True):
            gw = ModelGateway.create_default()
        assert "openai" in gw._providers
        assert gw._default_provider == "openai"

    def test_registers_anthropic_when_key_set(self):
        env = {"ANTHROPIC_API_KEY": "sk-ant-test"}
        with patch.dict(os.environ, env, clear=True):
            gw = ModelGateway.create_default()
        assert "anthropic" in gw._providers
        assert gw._default_provider == "anthropic"

    def test_openai_takes_priority_over_anthropic(self):
        env = {
            "OPENAI_API_KEY": "sk-test",
            "ANTHROPIC_API_KEY": "sk-ant-test",
        }
        with patch.dict(os.environ, env, clear=True):
            gw = ModelGateway.create_default()
        assert gw._default_provider == "openai"
        assert "anthropic" in gw._providers

    def test_openai_custom_base_url(self):
        env = {
            "OPENAI_API_KEY": "sk-test",
            "OPENAI_API_BASE": "https://my-proxy.example.com/v1",
        }
        with patch.dict(os.environ, env, clear=True):
            gw = ModelGateway.create_default()
        provider = gw._providers["openai"]
        base = str(provider._client.base_url)
        assert "my-proxy" in base


class TestAuditChainInitialization:
    @pytest.mark.asyncio
    async def test_initializes_from_existing_events(self):
        from agent_platform.domain.models import AgentDeployment, AgentDeploymentStatus
        from agent_platform.persistence.memory import InMemoryDeploymentAuditRepository
        from agent_platform.registry.deployment import DeploymentAuditLog

        repo = InMemoryDeploymentAuditRepository()
        log1 = DeploymentAuditLog(repo=repo)

        deployment = AgentDeployment(
            deployment_id="dep-1",
            agent_id="agent-a", version="1.0", channel="prod",
            status=AgentDeploymentStatus.PROD, traffic_percent=100,
        )
        event1 = await log1.record_deploy(deployment)
        saved_hash = event1.integrity_hash
        assert saved_hash != DeploymentAuditLog.GENESIS_HASH

        log2 = DeploymentAuditLog(repo=repo)
        assert log2._last_hash == DeploymentAuditLog.GENESIS_HASH

        deployment2 = AgentDeployment(
            deployment_id="dep-2",
            agent_id="agent-a", version="2.0", channel="prod",
            status=AgentDeploymentStatus.PROD, traffic_percent=100,
        )
        event2 = await log2.record_deploy(deployment2)
        assert event2.prev_hash == saved_hash

    @pytest.mark.asyncio
    async def test_genesis_when_no_events(self):
        from agent_platform.persistence.memory import InMemoryDeploymentAuditRepository
        from agent_platform.registry.deployment import DeploymentAuditLog

        repo = InMemoryDeploymentAuditRepository()
        log = DeploymentAuditLog(repo=repo)
        await log._ensure_chain_initialized()
        assert log._last_hash == DeploymentAuditLog.GENESIS_HASH


class TestTenantQuotaRecord:
    def test_record_increments_counter(self):
        from agent_platform.api.tenant_quota import TenantQuotaManager

        qm = TenantQuotaManager()
        qm.record_request("t-1")
        qm.record_request("t-1")
        qm.record_request("t-1")
        usage = qm.get_usage("t-1")
        assert usage.requests_today == 3

    def test_check_passes_under_limit(self):
        from agent_platform.api.tenant_quota import TenantQuota, TenantQuotaManager

        qm = TenantQuotaManager()
        qm.set_quota(TenantQuota(tenant_id="t-1", max_requests_per_day=1000))
        qm.record_request("t-1")
        qm.check_request_quota("t-1")

    def test_check_fails_over_limit(self):
        from agent_platform.api.tenant_quota import (
            QuotaExceededError,
            TenantQuota,
            TenantQuotaManager,
        )

        qm = TenantQuotaManager()
        qm.set_quota(TenantQuota(tenant_id="t-1", max_requests_per_day=2))
        qm.record_request("t-1")
        qm.record_request("t-1")
        with pytest.raises(QuotaExceededError):
            qm.check_request_quota("t-1")
