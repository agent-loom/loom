import json

import httpx
import pytest

from agent_platform.integrations.gitlab.adapter import GitLabAdapter


@pytest.mark.asyncio
async def test_gitlab_create_merge_request_sends_review_metadata():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.raw_path.decode()
        seen["token"] = request.headers["PRIVATE-TOKEN"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"iid": 7, "web_url": "https://gitlab.local/mr/7"})

    adapter = GitLabAdapter(
        "https://gitlab.local",
        "token",
        transport=httpx.MockTransport(handler),
    )

    response = await adapter.create_merge_request(
        "group%2Fproject",
        "feat/task",
        "main",
        "Implement task",
        description="Checklist",
        labels=["agent", "eval"],
        reviewer_ids=[101],
    )

    assert response.mr_id == 7
    assert response.url == "https://gitlab.local/mr/7"
    assert response.source_branch == "feat/task"
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/v4/projects/group%2Fproject/merge_requests"
    assert seen["token"] == "token"
    assert seen["body"] == {
        "source_branch": "feat/task",
        "target_branch": "main",
        "title": "Implement task",
        "description": "Checklist",
        "labels": "agent,eval",
        "reviewer_ids": [101],
    }


@pytest.mark.asyncio
async def test_gitlab_get_pipeline_status_reads_latest_pipeline():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.raw_path.decode().startswith(
            "/api/v4/projects/group%2Fproject/pipelines?"
        )
        assert request.url.params["ref"] == "feat/task"
        assert request.url.params["per_page"] == "1"
        return httpx.Response(200, json=[{"id": 10, "status": "success"}])

    adapter = GitLabAdapter(
        "https://gitlab.local",
        "token",
        transport=httpx.MockTransport(handler),
    )

    status = await adapter.get_pipeline_status("group%2Fproject", "feat/task")

    assert status == "success"


@pytest.mark.asyncio
async def test_gitlab_find_open_merge_request_by_source_branch():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.raw_path.decode()
        seen["source_branch"] = request.url.params["source_branch"]
        seen["state"] = request.url.params["state"]
        return httpx.Response(200, json=[{
            "iid": 17,
            "web_url": "https://gitlab.local/mr/17",
            "source_branch": "feat/task",
            "target_branch": "master",
        }])

    adapter = GitLabAdapter(
        "https://gitlab.local",
        "token",
        transport=httpx.MockTransport(handler),
    )

    response = await adapter.find_open_merge_request("group%2Fproject", "feat/task")

    assert response is not None
    assert response.mr_id == 17
    assert response.url == "https://gitlab.local/mr/17"
    assert response.source_branch == "feat/task"
    assert response.target_branch == "master"
    assert seen["method"] == "GET"
    assert seen["path"].startswith("/api/v4/projects/group%2Fproject/merge_requests?")
    assert seen["source_branch"] == "feat/task"
    assert seen["state"] == "opened"


@pytest.mark.asyncio
async def test_gitlab_find_open_merge_request_returns_none_when_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    adapter = GitLabAdapter(
        "https://gitlab.local",
        "token",
        transport=httpx.MockTransport(handler),
    )

    response = await adapter.find_open_merge_request("group%2Fproject", "feat/missing")

    assert response is None
