#!/usr/bin/env python
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fastapi.testclient import TestClient

from agent_platform.api.app import app
from agent_platform.config import Settings
from agent_platform.devflow.task_pack import TaskPackGenerator
from agent_platform.domain.models import AgentInput, AgentRequest
from agent_platform.evals.runner import EvalRunner
from agent_platform.registry.loader import ManifestLoader
from agent_platform.registry.registry import AgentRegistry
from agent_platform.router import AgentRouter


async def main() -> int:
    spec = ManifestLoader().load_file(Path("agents/myj/manifest.yaml"))
    assert spec.agent_id == "myj"

    registry = AgentRegistry(Path("agents"))
    route = await AgentRouter(registry, Settings(default_agent_id="myj")).route(
        AgentRequest(agent_id="myj", input=AgentInput(query="hello"))
    )
    assert route.agent_spec.agent_id == "myj"

    client = TestClient(app)
    health = client.get("/health")
    assert health.status_code == 200

    chat = client.post(
        "/api/v1/agent/chat",
        json={
            "agent_id": "myj",
            "context": {"tenant": {"retailer_id": "myj"}},
            "input": {"query": "可乐在哪里"},
            "options": {"debug": True},
        },
    )
    assert chat.status_code == 200
    payload = chat.json()
    assert payload["agent"]["agent_id"] == "myj"
    assert payload["trace"]["route_reason"]
    assert payload["trace"]["tool_calls"][0]["tool_name"] == "myj.goods_location"

    runs = client.get("/api/v1/agent-runs")
    assert runs.status_code == 200
    assert any(run["agent_id"] == "myj" for run in runs.json())

    report = await EvalRunner().run_agent(spec)
    assert report.pass_rate == 1.0

    task = TaskPackGenerator().from_requirement(
        task_id="AGENT-123",
        title="新增促销推荐 Agent",
        task_type="agent:new",
        project_id="agent-platform",
        background="新增一个促销推荐 Agent",
        agent_id="promo_recommendation",
    )
    assert task.repository.work_branch == "feat/agent-123"

    print("smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
