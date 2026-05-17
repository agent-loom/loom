"""Contract tests verifying InMemory and SQL repository implementations
satisfy the same 7 repository Protocol contracts."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.domain.models import (
    AgentDefinition,
    AgentDefinitionStatus,
    AgentDeployment,
    AgentDeploymentStatus,
    AgentManifest,
    AgentRun,
    AgentRunStatus,
    AgentSession,
)
from agent_platform.persistence.memory import (
    InMemoryAgentDefinitionRepository,
    InMemoryAgentDeploymentRepository,
    InMemoryAgentRunRepository,
    InMemoryAgentSessionRepository,
    InMemoryDeploymentAuditRepository,
    InMemoryEvalRunRepository,
    InMemoryWebhookDeliveryRepository,
)
from agent_platform.persistence.sql import (
    SqlAgentDefinitionRepository,
    SqlAgentDeploymentRepository,
    SqlAgentRunRepository,
    SqlAgentSessionRepository,
    SqlDeploymentAuditRepository,
    SqlEvalRunRepository,
    SqlWebhookDeliveryRepository,
)
from agent_platform.registry.deployment import DeploymentEvent
from agent_platform.storage.base import Base

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_MANIFEST = AgentManifest(
    api_version="agent.platform/v1",
    kind="AgentPackage",
    metadata={"id": "test-agent", "name": "Test Agent"},
    version={"package_version": "1.0.0"},
    output={"protocol": "agent-chat/v1", "supports": ["text"]},
)


def _make_definition(
    agent_id: str = "agent-a",
    version: str = "1.0.0",
    status: AgentDefinitionStatus = AgentDefinitionStatus.ACTIVE,
) -> AgentDefinition:
    return AgentDefinition(
        agent_id=agent_id,
        version=version,
        status=status,
        manifest=_MINIMAL_MANIFEST,
    )


def _make_deployment(
    deployment_id: str = "dep-1",
    agent_id: str = "agent-a",
    version: str = "1.0.0",
    channel: str = "dev",
    tenant_id: str | None = None,
) -> AgentDeployment:
    return AgentDeployment(
        deployment_id=deployment_id,
        agent_id=agent_id,
        version=version,
        channel=channel,
        tenant_id=tenant_id,
    )


def _make_event(
    agent_id: str = "agent-a",
    version: str = "1.0.0",
    channel: str = "dev",
    previous_version: str | None = None,
) -> DeploymentEvent:
    return DeploymentEvent(
        event_type="deploy",
        agent_id=agent_id,
        version=version,
        channel=channel,
        status=AgentDeploymentStatus.REGISTERED,
        previous_version=previous_version,
    )


def _make_run(
    run_id: str = "run-1",
    agent_id: str = "agent-a",
    session_id: str | None = None,
    tenant_id: str | None = None,
) -> AgentRun:
    return AgentRun(
        run_id=run_id,
        agent_id=agent_id,
        agent_version="1.0.0",
        runtime_backend="native",
        status=AgentRunStatus.SUCCEEDED,
        latency_ms=42,
        session_id=session_id,
        tenant_id=tenant_id,
    )


def _make_session(
    session_id: str = "sess-1",
    agent_id: str = "agent-a",
) -> AgentSession:
    return AgentSession(session_id=session_id, agent_id=agent_id)


async def _sql_session_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(params=["memory", "sql"])
async def definition_repo(request):
    if request.param == "memory":
        return InMemoryAgentDefinitionRepository()
    sf = await _sql_session_factory()
    return SqlAgentDefinitionRepository(sf)


@pytest_asyncio.fixture(params=["memory", "sql"])
async def deployment_repo(request):
    if request.param == "memory":
        return InMemoryAgentDeploymentRepository()
    sf = await _sql_session_factory()
    return SqlAgentDeploymentRepository(sf)


@pytest_asyncio.fixture(params=["memory", "sql"])
async def audit_repo(request):
    if request.param == "memory":
        return InMemoryDeploymentAuditRepository()
    sf = await _sql_session_factory()
    return SqlDeploymentAuditRepository(sf)


@pytest_asyncio.fixture(params=["memory", "sql"])
async def run_repo(request):
    if request.param == "memory":
        return InMemoryAgentRunRepository()
    sf = await _sql_session_factory()
    return SqlAgentRunRepository(sf)


@pytest_asyncio.fixture(params=["memory", "sql"])
async def session_repo(request):
    if request.param == "memory":
        return InMemoryAgentSessionRepository()
    sf = await _sql_session_factory()
    return SqlAgentSessionRepository(sf)


@pytest_asyncio.fixture(params=["memory", "sql"])
async def webhook_repo(request):
    if request.param == "memory":
        return InMemoryWebhookDeliveryRepository()
    sf = await _sql_session_factory()
    return SqlWebhookDeliveryRepository(sf)


@pytest_asyncio.fixture(params=["memory", "sql"])
async def eval_repo(request):
    if request.param == "memory":
        return InMemoryEvalRunRepository()
    sf = await _sql_session_factory()
    return SqlEvalRunRepository(sf)


# ===================================================================
# 1. AgentDefinitionRepository
# ===================================================================


class TestAgentDefinitionContract:
    @pytest.mark.asyncio
    async def test_save_and_get(self, definition_repo):
        defn = _make_definition()
        await definition_repo.save(defn)
        result = await definition_repo.get("agent-a", "1.0.0")
        assert result is not None
        assert result.agent_id == "agent-a"
        assert result.version == "1.0.0"

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, definition_repo):
        assert await definition_repo.get("no-such", "0.0.0") is None

    @pytest.mark.asyncio
    async def test_get_latest(self, definition_repo):
        await definition_repo.save(_make_definition(version="1.0.0"))
        await definition_repo.save(_make_definition(version="2.0.0"))
        latest = await definition_repo.get_latest("agent-a")
        assert latest is not None
        assert latest.version == "2.0.0"

    @pytest.mark.asyncio
    async def test_list_all_with_status_filter(self, definition_repo):
        await definition_repo.save(
            _make_definition(agent_id="a1", status=AgentDefinitionStatus.ACTIVE)
        )
        await definition_repo.save(
            _make_definition(
                agent_id="a2", version="2.0.0", status=AgentDefinitionStatus.DRAFT
            )
        )
        active = await definition_repo.list_all(status="active")
        assert len(active) == 1
        assert active[0].agent_id == "a1"

    @pytest.mark.asyncio
    async def test_update_status(self, definition_repo):
        await definition_repo.save(_make_definition())
        await definition_repo.update_status("agent-a", "1.0.0", "deprecated")
        result = await definition_repo.get("agent-a", "1.0.0")
        assert result is not None
        assert str(result.status) == "deprecated"


# ===================================================================
# 2. AgentDeploymentRepository
# ===================================================================


class TestAgentDeploymentContract:
    @pytest.mark.asyncio
    async def test_save_and_get(self, deployment_repo):
        dep = _make_deployment()
        await deployment_repo.save(dep)
        result = await deployment_repo.get("dep-1")
        assert result is not None
        assert result.agent_id == "agent-a"

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, deployment_repo):
        assert await deployment_repo.get("no-such") is None

    @pytest.mark.asyncio
    async def test_resolve(self, deployment_repo):
        await deployment_repo.save(_make_deployment())
        result = await deployment_repo.resolve(agent_id="agent-a", channel="dev")
        assert result is not None
        assert result.deployment_id == "dep-1"

    @pytest.mark.asyncio
    async def test_resolve_none_tenant_only_returns_general_deployment(self, deployment_repo):
        await deployment_repo.save(
            _make_deployment(deployment_id="tenant-dep", tenant_id="tenant-a")
        )
        await deployment_repo.save(_make_deployment(deployment_id="general-dep"))
        result = await deployment_repo.resolve(agent_id="agent-a", channel="dev")
        assert result is not None
        assert result.deployment_id == "general-dep"
        assert result.tenant_id is None

    @pytest.mark.asyncio
    async def test_save_upserts_existing_deployment_id(self, deployment_repo):
        await deployment_repo.save(_make_deployment(version="1.0.0"))
        await deployment_repo.save(_make_deployment(version="2.0.0"))
        result = await deployment_repo.get("dep-1")
        assert result is not None
        assert result.version == "2.0.0"

    @pytest.mark.asyncio
    async def test_list_all_with_agent_filter(self, deployment_repo):
        await deployment_repo.save(_make_deployment(deployment_id="d1", agent_id="a1"))
        await deployment_repo.save(_make_deployment(deployment_id="d2", agent_id="a2"))
        results = await deployment_repo.list_all(agent_id="a1")
        assert len(results) == 1
        assert results[0].agent_id == "a1"

    @pytest.mark.asyncio
    async def test_delete(self, deployment_repo):
        await deployment_repo.save(_make_deployment())
        await deployment_repo.delete("dep-1")
        assert await deployment_repo.get("dep-1") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_is_noop(self, deployment_repo):
        await deployment_repo.delete("no-such")  # should not raise

    @pytest.mark.asyncio
    async def test_list_all_with_tenant_filter(self, deployment_repo):
        await deployment_repo.save(
            _make_deployment(deployment_id="d1", agent_id="a1", tenant_id="t1")
        )
        await deployment_repo.save(
            _make_deployment(deployment_id="d2", agent_id="a1", tenant_id="t2")
        )
        results = await deployment_repo.list_all(tenant_id="t1")
        assert len(results) == 1
        assert results[0].tenant_id == "t1"


# ===================================================================
# 3. DeploymentAuditRepository
# ===================================================================


class TestDeploymentAuditContract:
    @pytest.mark.asyncio
    async def test_record_and_list(self, audit_repo):
        event = _make_event()
        await audit_repo.record(event)
        events = await audit_repo.list_events()
        assert len(events) == 1
        assert events[0].agent_id == "agent-a"

    @pytest.mark.asyncio
    async def test_list_events_with_filter(self, audit_repo):
        await audit_repo.record(_make_event(agent_id="a1", channel="dev"))
        await audit_repo.record(_make_event(agent_id="a2", channel="staging"))
        filtered = await audit_repo.list_events(agent_id="a1")
        assert len(filtered) == 1
        assert filtered[0].agent_id == "a1"

    @pytest.mark.asyncio
    async def test_rollback_version(self, audit_repo):
        await audit_repo.record(
            _make_event(agent_id="a1", channel="dev", previous_version="0.9.0")
        )
        ver = await audit_repo.get_rollback_version("a1", "dev")
        assert ver == "0.9.0"

    @pytest.mark.asyncio
    async def test_rollback_version_none_when_no_previous(self, audit_repo):
        assert await audit_repo.get_rollback_version("nope", "dev") is None


# ===================================================================
# 4. AgentRunRepository
# ===================================================================


class TestAgentRunContract:
    @pytest.mark.asyncio
    async def test_record_and_get(self, run_repo):
        run = _make_run()
        await run_repo.record(run)
        result = await run_repo.get("run-1")
        assert result is not None
        assert result.agent_id == "agent-a"
        assert result.latency_ms == 42

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, run_repo):
        assert await run_repo.get("no-such") is None

    @pytest.mark.asyncio
    async def test_list_runs_with_agent_filter(self, run_repo):
        await run_repo.record(_make_run(run_id="r1", agent_id="a1"))
        await run_repo.record(_make_run(run_id="r2", agent_id="a2"))
        results = await run_repo.list_runs(agent_id="a1")
        assert len(results) == 1
        assert results[0].agent_id == "a1"

    @pytest.mark.asyncio
    async def test_list_runs_with_session_filter(self, run_repo):
        await run_repo.record(_make_run(run_id="r1", session_id="s1"))
        await run_repo.record(_make_run(run_id="r2", session_id="s2"))
        results = await run_repo.list_runs(session_id="s1")
        assert len(results) == 1
        assert results[0].session_id == "s1"

    @pytest.mark.asyncio
    async def test_list_runs_with_tenant_filter(self, run_repo):
        await run_repo.record(_make_run(run_id="r1", tenant_id="t1"))
        await run_repo.record(_make_run(run_id="r2", tenant_id="t2"))
        results = await run_repo.list_runs(tenant_id="t1")
        assert len(results) == 1
        assert results[0].tenant_id == "t1"


# ===================================================================
# 5. AgentSessionRepository
# ===================================================================


class TestAgentSessionContract:
    @pytest.mark.asyncio
    async def test_save_and_load(self, session_repo):
        sess = _make_session()
        await session_repo.save(sess)
        result = await session_repo.load("sess-1")
        assert result is not None
        assert result.agent_id == "agent-a"

    @pytest.mark.asyncio
    async def test_load_nonexistent_returns_none(self, session_repo):
        assert await session_repo.load("no-such") is None

    @pytest.mark.asyncio
    async def test_delete(self, session_repo):
        await session_repo.save(_make_session())
        await session_repo.delete("sess-1")
        assert await session_repo.load("sess-1") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_is_noop(self, session_repo):
        await session_repo.delete("no-such")  # should not raise

    @pytest.mark.asyncio
    async def test_list_sessions_with_agent_filter(self, session_repo):
        await session_repo.save(_make_session(session_id="s1", agent_id="a1"))
        await session_repo.save(_make_session(session_id="s2", agent_id="a2"))
        results = await session_repo.list_sessions(agent_id="a1")
        assert len(results) == 1
        assert results[0].agent_id == "a1"

    @pytest.mark.asyncio
    async def test_list_sessions_with_tenant_filter(self, session_repo):
        s1 = _make_session(session_id="s1", agent_id="a1")
        s1.tenant_id = "t1"
        s2 = _make_session(session_id="s2", agent_id="a1")
        s2.tenant_id = "t2"
        await session_repo.save(s1)
        await session_repo.save(s2)
        results = await session_repo.list_sessions(tenant_id="t1")
        assert len(results) == 1
        assert results[0].tenant_id == "t1"


# ===================================================================
# 6. WebhookDeliveryRepository
# ===================================================================


class TestWebhookDeliveryContract:
    @pytest.mark.asyncio
    async def test_record_and_exists(self, webhook_repo):
        await webhook_repo.record(delivery_id="wh-1", source="github")
        assert await webhook_repo.exists("wh-1") is True

    @pytest.mark.asyncio
    async def test_exists_returns_false_for_unknown(self, webhook_repo):
        assert await webhook_repo.exists("no-such") is False

    @pytest.mark.asyncio
    async def test_record_with_all_fields(self, webhook_repo):
        await webhook_repo.record(
            delivery_id="wh-2",
            source="gitlab",
            event_type="push",
            status="rejected",
            payload={"ref": "main"},
            error_message="bad signature",
        )
        assert await webhook_repo.exists("wh-2") is True


# ===================================================================
# 7. EvalRunRepository
# ===================================================================


class TestEvalRunContract:
    @pytest.mark.asyncio
    async def test_record_and_get_latest(self, eval_repo):
        await eval_repo.record(
            agent_id="a1",
            agent_version="1.0.0",
            total=10,
            passed=8,
            pass_rate=0.8,
            required_pass_rate=0.7,
            gate_passed=True,
            results=[{"case": "c1", "passed": True}],
        )
        latest = await eval_repo.get_latest("a1")
        assert latest is not None
        assert latest["agent_id"] == "a1"
        assert latest["passed"] == 8
        assert latest["gate_passed"] is True

    @pytest.mark.asyncio
    async def test_get_latest_returns_none_for_unknown(self, eval_repo):
        assert await eval_repo.get_latest("no-such") is None

    @pytest.mark.asyncio
    async def test_list_runs_with_agent_filter(self, eval_repo):
        await eval_repo.record(
            agent_id="a1",
            agent_version="1.0.0",
            total=5,
            passed=5,
            pass_rate=1.0,
            required_pass_rate=0.9,
            gate_passed=True,
            results=[],
        )
        await eval_repo.record(
            agent_id="a2",
            agent_version="1.0.0",
            total=5,
            passed=3,
            pass_rate=0.6,
            required_pass_rate=0.9,
            gate_passed=False,
            results=[],
        )
        results = await eval_repo.list_runs(agent_id="a1")
        assert len(results) == 1
        assert results[0]["agent_id"] == "a1"

    @pytest.mark.asyncio
    async def test_list_runs_respects_limit(self, eval_repo):
        for i in range(5):
            await eval_repo.record(
                agent_id="a1",
                agent_version=f"{i}.0.0",
                total=1,
                passed=1,
                pass_rate=1.0,
                required_pass_rate=0.5,
                gate_passed=True,
                results=[],
            )
        results = await eval_repo.list_runs(agent_id="a1", limit=3)
        assert len(results) == 3
