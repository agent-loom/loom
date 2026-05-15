import pytest

from agent_platform.domain.models import AgentInput, AgentRequest, AgentSession, RuntimeRequest
from agent_platform.registry.loader import ManifestLoader
from agent_platform.runtime.manager import RuntimeManager
from agent_platform.session.store import InMemorySessionStore


class FailingRuntimeBackend:
    name = "failing"

    async def run(self, request):
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_session_store_save_and_load():
    store = InMemorySessionStore()
    session = AgentSession(session_id="sess_001", agent_id="myj")
    session.add_message("user", "hello")
    store.save(session)

    loaded = store.load("sess_001")
    assert loaded is not None
    assert len(loaded.history) == 1
    assert loaded.history[0].content == "hello"


@pytest.mark.asyncio
async def test_session_store_delete():
    store = InMemorySessionStore()
    session = AgentSession(session_id="sess_002", agent_id="myj")
    store.save(session)
    store.delete("sess_002")
    assert store.load("sess_002") is None


@pytest.mark.asyncio
async def test_session_store_list_by_agent():
    store = InMemorySessionStore()
    store.save(AgentSession(session_id="s1", agent_id="myj"))
    store.save(AgentSession(session_id="s2", agent_id="echo"))
    store.save(AgentSession(session_id="s3", agent_id="myj"))

    myj_sessions = store.list_sessions(agent_id="myj")
    assert len(myj_sessions) == 2

    all_sessions = store.list_sessions()
    assert len(all_sessions) == 3


@pytest.mark.asyncio
async def test_runtime_manager_creates_session():
    from pathlib import Path
    spec = ManifestLoader().load_file(Path("agents/myj/manifest.yaml"))
    session_store = InMemorySessionStore()
    manager = RuntimeManager(session_store=session_store)

    request = AgentRequest(
        agent_id="myj",
        session_id="test_sess",
        context={"tenant": {"retailer_id": "myj"}},
        input=AgentInput(query="hello"),
    )
    await manager.run(RuntimeRequest(request=request, agent_spec=spec))

    session = session_store.load("test_sess")
    assert session is not None
    assert session.agent_id == "myj"
    assert len(session.history) == 2


@pytest.mark.asyncio
async def test_runtime_manager_preserves_traffic_bucket_on_error():
    from pathlib import Path

    spec = ManifestLoader().load_file(Path("agents/myj/manifest.yaml"))
    spec.manifest.runtime.backend = "failing"
    manager = RuntimeManager()
    manager.register(FailingRuntimeBackend())

    response = await manager.run(
        RuntimeRequest(
            request=AgentRequest(
                agent_id="myj",
                context={"tenant": {"retailer_id": "myj"}},
                input=AgentInput(query="hello"),
            ),
            agent_spec=spec,
            route_reason="agent_id",
            traffic_bucket=7,
        )
    )

    assert response.response.output.status == "failed"
    assert response.response.error is not None
    assert response.response.error.code == "RUNTIME_ERROR"
    assert response.response.trace is not None
    assert response.response.trace.traffic_bucket == 7


@pytest.mark.asyncio
async def test_session_recent_messages():
    session = AgentSession(session_id="s1", agent_id="myj")
    for i in range(10):
        session.add_message("user", f"msg_{i}")

    recent = session.recent_messages(3)
    assert len(recent) == 3
    assert recent[0].content == "msg_7"
