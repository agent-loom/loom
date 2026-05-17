"""审计哈希链完整性测试。"""

from __future__ import annotations

import pytest

from agent_platform.domain.models import AgentDeployment, AgentDeploymentStatus
from agent_platform.persistence.memory import InMemoryDeploymentAuditRepository
from agent_platform.registry.deployment import (
    DeploymentAuditLog,
    DeploymentEvent,
    _compute_event_hash,
)


def _make_deployment(
    agent_id: str = "agent-a",
    version: str = "1.0.0",
    channel: str = "prod",
) -> AgentDeployment:
    return AgentDeployment(
        deployment_id=f"dep_{agent_id}_{channel}",
        agent_id=agent_id,
        version=version,
        channel=channel,
        status=AgentDeploymentStatus.PROD,
    )


class TestAuditHashChain:
    """审计事件哈希链完整性校验测试。"""

    @pytest.mark.asyncio()
    async def test_deploy_event_has_integrity_hash(self):
        """部署事件应包含完整性哈希。"""
        audit = DeploymentAuditLog()
        dep = _make_deployment()
        event = await audit.record_deploy(dep, actor="ci-bot")
        assert event.integrity_hash
        assert len(event.integrity_hash) == 64

    @pytest.mark.asyncio()
    async def test_first_event_links_to_genesis(self):
        """第一条事件的 prev_hash 应为创世哈希。"""
        audit = DeploymentAuditLog()
        dep = _make_deployment()
        event = await audit.record_deploy(dep)
        assert event.prev_hash == DeploymentAuditLog.GENESIS_HASH

    @pytest.mark.asyncio()
    async def test_chain_links_consecutive_events(self):
        """连续事件的哈希应形成链式结构。"""
        audit = DeploymentAuditLog()
        dep1 = _make_deployment(version="1.0.0")
        dep2 = _make_deployment(version="2.0.0")

        ev1 = await audit.record_deploy(dep1)
        ev2 = await audit.record_deploy(dep2, previous_version="1.0.0")

        assert ev2.prev_hash == ev1.integrity_hash
        assert ev1.integrity_hash != ev2.integrity_hash

    @pytest.mark.asyncio()
    async def test_rollback_event_continues_chain(self):
        """回滚事件应继续哈希链。"""
        audit = DeploymentAuditLog()
        dep = _make_deployment(version="2.0.0")

        ev1 = await audit.record_deploy(dep)
        ev2 = await audit.record_rollback(
            "agent-a", "prod", "2.0.0", "1.0.0", actor="oncall",
        )
        assert ev2.prev_hash == ev1.integrity_hash

    @pytest.mark.asyncio()
    async def test_verify_chain_valid(self):
        """完整链应通过校验。"""
        audit = DeploymentAuditLog()
        dep1 = _make_deployment(version="1.0.0")
        dep2 = _make_deployment(version="2.0.0")

        await audit.record_deploy(dep1)
        await audit.record_deploy(dep2, previous_version="1.0.0")

        valid, count = await audit.verify_chain()
        assert valid is True
        assert count == 2

    @pytest.mark.asyncio()
    async def test_verify_chain_detects_tamper(self):
        """篡改事件后链式校验应失败。"""
        repo = InMemoryDeploymentAuditRepository()
        audit = DeploymentAuditLog(repo=repo)
        dep = _make_deployment()

        await audit.record_deploy(dep)
        await audit.record_deploy(
            _make_deployment(version="2.0.0"), previous_version="1.0.0",
        )

        # 篡改第一条事件的哈希
        repo._events[0] = repo._events[0].model_copy(
            update={"integrity_hash": "tampered_hash"},
        )

        valid, count = await audit.verify_chain()
        assert valid is False
        assert count == 1

    def test_compute_event_hash_deterministic(self):
        """同样的输入应产生相同的哈希。"""
        h1 = _compute_event_hash("data1", "prev1")
        h2 = _compute_event_hash("data1", "prev1")
        assert h1 == h2

    def test_compute_event_hash_different_data(self):
        """不同数据应产生不同哈希。"""
        h1 = _compute_event_hash("data1", "prev1")
        h2 = _compute_event_hash("data2", "prev1")
        assert h1 != h2


class TestRoutingDecisionMemory:
    """路由决策内存存储测试。"""

    @pytest.mark.asyncio()
    async def test_record_and_get(self):
        from agent_platform.persistence.memory import InMemoryRoutingDecisionRepository

        repo = InMemoryRoutingDecisionRepository()
        await repo.record(
            run_id="run-1",
            agent_id="agent-a",
            reason="agent_id",
            deployment_id="dep-1",
            traffic_bucket=42,
            latency_ms=15,
        )
        d = await repo.get("run-1")
        assert d is not None
        assert d["agent_id"] == "agent-a"
        assert d["reason"] == "agent_id"
        assert d["traffic_bucket"] == 42

    @pytest.mark.asyncio()
    async def test_get_missing_returns_none(self):
        from agent_platform.persistence.memory import InMemoryRoutingDecisionRepository

        repo = InMemoryRoutingDecisionRepository()
        assert await repo.get("nope") is None

    @pytest.mark.asyncio()
    async def test_list_by_agent_id(self):
        from agent_platform.persistence.memory import InMemoryRoutingDecisionRepository

        repo = InMemoryRoutingDecisionRepository()
        await repo.record(run_id="r1", agent_id="a1", reason="agent_id")
        await repo.record(run_id="r2", agent_id="a2", reason="default")
        await repo.record(run_id="r3", agent_id="a1", reason="tenant")

        results = await repo.list_decisions(agent_id="a1")
        assert len(results) == 2
        assert all(d["agent_id"] == "a1" for d in results)

    @pytest.mark.asyncio()
    async def test_list_by_reason(self):
        from agent_platform.persistence.memory import InMemoryRoutingDecisionRepository

        repo = InMemoryRoutingDecisionRepository()
        await repo.record(run_id="r1", agent_id="a1", reason="agent_id")
        await repo.record(run_id="r2", agent_id="a2", reason="default_agent")

        results = await repo.list_decisions(reason="agent_id")
        assert len(results) == 1

    @pytest.mark.asyncio()
    async def test_list_respects_limit(self):
        from agent_platform.persistence.memory import InMemoryRoutingDecisionRepository

        repo = InMemoryRoutingDecisionRepository()
        for i in range(10):
            await repo.record(run_id=f"r{i}", agent_id="a1", reason="x")

        results = await repo.list_decisions(limit=3)
        assert len(results) == 3
