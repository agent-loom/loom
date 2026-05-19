import httpx
import pytest

from agent_platform.devflow.orchestrator import DevFlowOrchestrator
from agent_platform.devflow.ownership import AgentOwnershipResolver
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
async def test_devflow_creates_branch_on_ready_for_ai_dev():
    created_requests: list[httpx.Request] = []

    def gitlab_handler(request: httpx.Request) -> httpx.Response:
        created_requests.append(request)
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
    assert result.mr_iid is None
    assert result.mr_url is None
    assert result.task_pack.metadata.task_id == "wi-001"
    assert result.task_pack.agent["agent_id"] == "promo"
    assert result.task_pack.repository.default_branch == "master"

    # Orchestrator 只创建分支，不创建 MR
    mr_requests = [
        r for r in created_requests
        if "merge_requests" in r.url.path and r.method == "POST"
    ]
    assert len(mr_requests) == 0


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
async def test_devflow_uses_project_mapping_when_agent_property_missing():
    created_requests: list[httpx.Request] = []

    def gitlab_handler(request: httpx.Request) -> httpx.Response:
        created_requests.append(request)
        if "merge_requests" in request.url.path and request.method == "POST":
            return httpx.Response(200, json={
                "iid": 44,
                "web_url": "https://gitlab.example.com/mr/44",
            })
        if "branches" in request.url.path:
            return httpx.Response(200, json={"name": "feat/wi-004"})
        return httpx.Response(200, json={})

    plane_transport = _mock_transport({
        "/work-items/wi-004/": {
            "id": "wi-004",
            "name": "修改 Echo Agent",
            "project": {"id": "proj-plane-1", "name": "Agent Platform"},
            "description_stripped": "没有填写 agent_id，但项目映射到 echo",
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
    resolver = AgentOwnershipResolver(
        project_mappings=[
            {
                "plane_project_id": "proj-plane-1",
                "agent_id": "echo",
                "task_type": "agent:change",
            }
        ]
    )
    orchestrator = DevFlowOrchestrator(
        plane=plane,
        gitlab=gitlab,
        gitlab_project_id="proj-1",
        ownership_resolver=resolver,
    )

    payload = {
        "data": {
            "id": "wi-004",
            "project": "proj-plane-1",
            "name": "修改 Echo Agent",
            "state_detail": {"name": "Ready for AI Dev"},
        }
    }

    result = await orchestrator.handle_webhook_event("work_item.updated", payload)

    assert result is not None
    assert result.task_pack.agent["agent_id"] == "echo"
    commands = result.task_pack.validation["commands"]
    assert "python scripts/validate_manifest.py agents/echo/manifest.yaml" in commands
    assert "python scripts/run_agent_eval.py --agent echo --report eval-report.json" in commands
    assert not any("<agent_id>" in command for command in commands)


@pytest.mark.asyncio
async def test_devflow_blocks_when_agent_ownership_unresolved():
    plane_requests: list[httpx.Request] = []
    gitlab_requests: list[httpx.Request] = []

    def plane_handler(request: httpx.Request) -> httpx.Response:
        plane_requests.append(request)
        if "/work-items/wi-005/" in request.url.path and request.method == "GET":
            return httpx.Response(200, json={
                "id": "wi-005",
                "name": "缺少 Agent 归属",
                "description_stripped": "没有 agent_id，也没有项目映射",
            })
        if "/comments/" in request.url.path and request.method == "POST":
            return httpx.Response(200, json={"id": "comment-1"})
        return httpx.Response(200, json={})

    def gitlab_handler(request: httpx.Request) -> httpx.Response:
        gitlab_requests.append(request)
        return httpx.Response(200, json={})

    plane = PlaneAdapter(
        base_url="https://plane.test",
        api_key="test-key",
        workspace_slug="ws",
        transport=httpx.MockTransport(plane_handler),
    )
    gitlab = GitLabAdapter(
        base_url="https://gitlab.test",
        token="test-token",
        transport=httpx.MockTransport(gitlab_handler),
    )
    orchestrator = DevFlowOrchestrator(
        plane=plane,
        gitlab=gitlab,
        gitlab_project_id="proj-1",
    )

    payload = {
        "data": {
            "id": "wi-005",
            "project": "proj-plane-unknown",
            "name": "缺少 Agent 归属",
            "state_detail": {"name": "Ready for AI Dev"},
        }
    }

    result = await orchestrator.handle_webhook_event("work_item.updated", payload)

    assert result is None
    assert not gitlab_requests
    comment_requests = [request for request in plane_requests if "/comments/" in request.url.path]
    assert len(comment_requests) == 1
    assert "agent_id" in comment_requests[0].content.decode()


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
