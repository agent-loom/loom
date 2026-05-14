from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_platform.api.app import app
from agent_platform.evals.runner import EvalRunner
from agent_platform.registry.loader import ManifestLoader
from agent_platform.runtime.manager import RuntimeManager


def test_echo_manifest_loads_without_core_changes():
    spec = ManifestLoader().load_file(Path("agents/echo/manifest.yaml"))
    assert spec.agent_id == "echo"
    assert spec.version == "0.1.0"
    assert spec.manifest.runtime.backend == "native"


def test_echo_agent_chat_via_api():
    client = TestClient(app)
    response = client.post(
        "/api/v1/agent/chat",
        json={
            "request_id": "req_echo_1",
            "agent_id": "echo",
            "session_id": "sess_echo",
            "input": {"query": "hello world"},
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["agent"]["agent_id"] == "echo"
    assert "hello world" in data["output"]["text"]["display"]


@pytest.mark.asyncio
async def test_echo_eval_passes():
    spec = ManifestLoader().load_file(Path("agents/echo/manifest.yaml"))
    runner = EvalRunner(RuntimeManager())
    report = await runner.run_agent(spec)
    assert report.total == 1
    assert report.passed == 1
    assert report.gate_passed is True


def test_agents_list_includes_echo():
    client = TestClient(app)
    response = client.get("/api/v1/agents")
    assert response.status_code == 200
    agents = response.json()
    agent_ids = [a["agent_id"] for a in agents]
    assert "echo" in agent_ids
    assert "myj" in agent_ids
