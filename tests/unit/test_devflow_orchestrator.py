import json

import httpx
import pytest

from agent_platform.devflow.orchestrator import DevFlowOrchestrator
from agent_platform.integrations.gitlab.adapter import GitLabAdapter
from agent_platform.integrations.plane.adapter import PlaneAdapter


def _mock_transport(responses: dict[str, dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        for pattern, resp in responses.items():
            if pattern in path:
                return httpx.Response(200, json=resp)
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_devflow_creates_branch_and_mr_on_ready_for_ai_dev():
    created_requests: list[httpx.Request] = []

    def gitlab_handler(request: httpx.Request) -> httpx.Response:
        created_requests.append(request)
        if "merge_requests" in request.url.path and request.method == "POST":
            return httpx.Response(200, json={
                "iid": 42,
                "web_url": "https://gitlab.example.com/mr/42",
            })
        if "branches" in request.url.path:
            return httpx.Response(200, json={"name": "feat/task-001"})
        return httpx.Response(200, json={})

    plane_transport = _mock_transport({
        "/work-items/wi-001/": {
            "id": "wi-001",
            "name": "新增促销推荐 Agent",
            "description_stripped": "需要一个促销推荐 Agent",
            "properties": {"agent_id": "promo", "task_type": "agent:new"},
        },
        "/comments/": {"id": "comment-1"},
    })

    plane = PlaneAdapter(
        base_url="https://plane.test",
        api_key="test-key",
        workspace_slug="ws",
        transport=plane_transport,
    )
    gitlab = GitLabAdapter(
        base_url="https://gitlab.test",
        token="test-token",
        transport=httpx.MockTransport(gitlab_handler),
    )

    orchestrator = DevFlowOrchestrator(
        plane=plane, gitlab=gitlab, gitlab_project_id="proj-1",
        default_branch="master",
    )

    payload = {
        "data": {
            "id": "wi-001",
            "project": "proj-plane-1",
            "name": "新增促销推荐 Agent",
            "state_detail": {"name": "Ready for AI Dev"},
        }
    }

    result = await orchestrator.handle_webhook_event("work_item.updated", payload)

    assert result is not None
    assert result.branch == "feat/wi-001"
    assert result.mr_iid == 42
    assert result.mr_url == "https://gitlab.example.com/mr/42"
    assert result.task_pack.metadata.task_id == "wi-001"
    assert result.task_pack.agent["agent_id"] == "promo"

    mr_requests = [
        r for r in created_requests
        if "merge_requests" in r.url.path and r.method == "POST"
    ]
    assert len(mr_requests) == 1
    mr_body = json.loads(mr_requests[0].content)
    assert "agent:new" in mr_body.get("labels", "")
    assert mr_body["target_branch"] == "master"
    assert result.task_pack.repository.default_branch == "master"


@pytest.mark.asyncio
async def test_devflow_uses_payload_properties_when_detail_lacks_custom_fields():
    def gitlab_handler(request: httpx.Request) -> httpx.Response:
        if "merge_requests" in request.url.path and request.method == "POST":
            return httpx.Response(200, json={
                "iid": 43,
                "web_url": "https://gitlab.example.com/mr/43",
            })
        if "branches" in request.url.path:
            return httpx.Response(200, json={"name": "feat/wi-003"})
        return httpx.Response(200, json={})

    plane_transport = _mock_transport({
        "/work-items/wi-003/": {
            "id": "wi-003",
            "name": "修改 Echo Agent",
            "description_stripped": "Plane 详情里没有 custom properties",
        },
        "/comments/": {"id": "comment-1"},
    })

    plane = PlaneAdapter(
        base_url="https://plane.test",
        api_key="test-key",
        workspace_slug="ws",
        transport=plane_transport,
    )
    gitlab = GitLabAdapter(
        base_url="https://gitlab.test",
        token="test-token",
        transport=httpx.MockTransport(gitlab_handler),
    )
    orchestrator = DevFlowOrchestrator(
        plane=plane, gitlab=gitlab, gitlab_project_id="proj-1",
    )

    payload = {
        "data": {
            "id": "wi-003",
            "project": "proj-plane-1",
            "name": "修改 Echo Agent",
            "state_detail": {"name": "Ready for AI Dev"},
            "properties": {"agent_id": "echo", "task_type": "agent:change"},
        }
    }

    result = await orchestrator.handle_webhook_event("work_item.updated", payload)

    assert result is not None
    assert result.task_pack.agent["agent_id"] == "echo"
    assert result.task_pack.metadata.type == "agent:change"


@pytest.mark.asyncio
async def test_devflow_ignores_non_ready_state():
    plane = PlaneAdapter(
        base_url="https://plane.test",
        api_key="test-key",
        workspace_slug="ws",
    )
    gitlab = GitLabAdapter(
        base_url="https://gitlab.test",
        token="test-token",
    )
    orchestrator = DevFlowOrchestrator(
        plane=plane, gitlab=gitlab, gitlab_project_id="proj-1"
    )

    payload = {
        "data": {
            "id": "wi-002",
            "project": "proj-1",
            "name": "Some task",
            "state_detail": {"name": "Backlog"},
        }
    }

    result = await orchestrator.handle_webhook_event("work_item.updated", payload)
    assert result is None


@pytest.mark.asyncio
async def test_devflow_ignores_irrelevant_event():
    plane = PlaneAdapter(
        base_url="https://plane.test",
        api_key="test-key",
        workspace_slug="ws",
    )
    gitlab = GitLabAdapter(
        base_url="https://gitlab.test",
        token="test-token",
    )
    orchestrator = DevFlowOrchestrator(
        plane=plane, gitlab=gitlab, gitlab_project_id="proj-1"
    )

    result = await orchestrator.handle_webhook_event("project.updated", {"data": {}})
    assert result is None
