import json

import httpx
import pytest

from agent_platform.integrations.plane.adapter import PlaneAdapter


@pytest.mark.asyncio
async def test_plane_search_work_items_uses_workspace_project_path():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v1/workspaces/ws/projects/proj/work-items/"
        assert request.url.params["search"] == "Ready for AI Dev"
        assert request.headers["X-API-Key"] == "key"
        return httpx.Response(200, json={"results": [{"id": "issue_1"}]})

    adapter = PlaneAdapter(
        "https://plane.local",
        "key",
        "ws",
        transport=httpx.MockTransport(handler),
    )

    response = await adapter.search_work_items("proj", "Ready for AI Dev")

    assert response["results"][0]["id"] == "issue_1"


@pytest.mark.asyncio
async def test_plane_update_work_item_state_patches_state():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "issue_1", "state": "state_done"})

    adapter = PlaneAdapter(
        "https://plane.local",
        "key",
        "ws",
        transport=httpx.MockTransport(handler),
    )

    response = await adapter.update_work_item_state("proj", "issue_1", "state_done")

    assert response["state"] == "state_done"
    assert seen["method"] == "PATCH"
    assert seen["path"] == "/api/v1/workspaces/ws/projects/proj/work-items/issue_1/"
    assert seen["body"] == {"state": "state_done"}


@pytest.mark.asyncio
async def test_plane_add_comment_posts_comment_html():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == (
            "/api/v1/workspaces/ws/projects/proj/work-items/issue_1/comments/"
        )
        assert json.loads(request.content) == {"comment_html": "<p>MR created</p>"}
        return httpx.Response(201, json={"id": "comment_1"})

    adapter = PlaneAdapter(
        "https://plane.local",
        "key",
        "ws",
        transport=httpx.MockTransport(handler),
    )

    response = await adapter.add_comment("proj", "issue_1", "<p>MR created</p>")

    assert response["id"] == "comment_1"
