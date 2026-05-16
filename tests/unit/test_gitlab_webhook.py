from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agent_platform.integrations.gitlab.webhook import (
    GitLabEventHandler,
    GitLabWebhookError,
    GitLabWebhookVerifier,
)


class TestGitLabWebhookVerifier:
    def test_valid_token(self):
        verifier = GitLabWebhookVerifier("my-secret")
        verifier.verify("my-secret")

    def test_invalid_token(self):
        verifier = GitLabWebhookVerifier("my-secret")
        with pytest.raises(GitLabWebhookError, match="Invalid"):
            verifier.verify("wrong-token")

    def test_none_token(self):
        verifier = GitLabWebhookVerifier("my-secret")
        with pytest.raises(GitLabWebhookError):
            verifier.verify(None)

    def test_empty_token(self):
        verifier = GitLabWebhookVerifier("my-secret")
        with pytest.raises(GitLabWebhookError):
            verifier.verify("")


@pytest.fixture
def plane_mock():
    plane = AsyncMock()
    plane.update_work_item_state = AsyncMock()
    plane.add_comment = AsyncMock()
    return plane


@pytest.fixture
def webhook_repo_mock():
    repo = AsyncMock()
    repo.exists = AsyncMock(return_value=False)
    repo.record = AsyncMock()
    return repo


@pytest.fixture
def handler(plane_mock, webhook_repo_mock):
    return GitLabEventHandler(
        plane=plane_mock,
        webhook_repo=webhook_repo_mock,
        testing_state_id="state-testing",
        human_review_state_id="state-review",
        staging_state_id="state-staging",
        done_state_id="state-done",
        ai_developing_state_id="state-ai-dev",
    )


class TestPipelineEvents:
    @pytest.mark.asyncio
    async def test_pipeline_running_syncs_testing(self, handler, plane_mock):
        payload = {
            "object_attributes": {"id": 1, "status": "running", "ref": "feat/x"},
            "variables": [
                {"key": "PLANE_PROJECT_ID", "value": "proj-1"},
                {"key": "PLANE_WORK_ITEM_ID", "value": "wi-1"},
            ],
        }
        result = await handler.handle_event("pipeline", payload)
        assert result["status"] == "synced"
        assert "Testing" in result["action"]
        plane_mock.update_work_item_state.assert_awaited_once_with(
            "proj-1", "wi-1", "state-testing"
        )

    @pytest.mark.asyncio
    async def test_pipeline_failed_syncs_ai_developing(self, handler, plane_mock):
        payload = {
            "object_attributes": {"id": 2, "status": "failed", "ref": "feat/y"},
            "variables": [
                {"key": "PLANE_PROJECT_ID", "value": "proj-1"},
                {"key": "PLANE_WORK_ITEM_ID", "value": "wi-2"},
            ],
        }
        result = await handler.handle_event("pipeline", payload)
        assert result["status"] == "synced"
        assert "AI Developing" in result["action"]
        plane_mock.update_work_item_state.assert_awaited_once_with(
            "proj-1", "wi-2", "state-ai-dev"
        )

    @pytest.mark.asyncio
    async def test_pipeline_success_syncs_human_review(self, handler, plane_mock):
        payload = {
            "object_attributes": {"id": 3, "status": "success", "ref": "feat/z"},
            "variables": [
                {"key": "PLANE_PROJECT_ID", "value": "proj-1"},
                {"key": "PLANE_WORK_ITEM_ID", "value": "wi-3"},
            ],
        }
        result = await handler.handle_event("pipeline", payload)
        assert result["status"] == "synced"
        assert "Human Review" in result["action"]

    @pytest.mark.asyncio
    async def test_pipeline_no_plane_vars_skipped(self, handler):
        payload = {
            "object_attributes": {"id": 4, "status": "success", "ref": "main"},
            "variables": [],
        }
        result = await handler.handle_event("pipeline", payload)
        assert result["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_pipeline_unknown_status_ignored(self, handler):
        payload = {
            "object_attributes": {"id": 5, "status": "pending", "ref": "feat/a"},
            "variables": [
                {"key": "PLANE_PROJECT_ID", "value": "proj-1"},
                {"key": "PLANE_WORK_ITEM_ID", "value": "wi-5"},
            ],
        }
        result = await handler.handle_event("pipeline", payload)
        assert result["status"] == "ignored"


class TestMergeRequestEvents:
    @pytest.mark.asyncio
    async def test_mr_merged_syncs_staging(self, handler, plane_mock):
        payload = {
            "object_attributes": {
                "id": 10,
                "action": "merge",
                "state": "merged",
                "source_branch": "feat/task-1",
                "description": "PLANE_PROJECT_ID: proj-1\nPLANE_WORK_ITEM_ID: wi-10",
            },
        }
        result = await handler.handle_event("merge_request", payload)
        assert result["status"] == "synced"
        assert "Staging" in result["action"]
        plane_mock.update_work_item_state.assert_awaited_once_with(
            "proj-1", "wi-10", "state-staging"
        )

    @pytest.mark.asyncio
    async def test_mr_closed_syncs_ai_developing(self, handler, plane_mock):
        payload = {
            "object_attributes": {
                "id": 11,
                "action": "close",
                "state": "closed",
                "source_branch": "feat/task-2",
                "description": "PLANE_PROJECT_ID: proj-1\nPLANE_WORK_ITEM_ID: wi-11",
            },
        }
        result = await handler.handle_event("merge_request", payload)
        assert result["status"] == "synced"
        assert "AI Developing" in result["action"]

    @pytest.mark.asyncio
    async def test_mr_no_plane_info_skipped(self, handler):
        payload = {
            "object_attributes": {
                "id": 12,
                "action": "merge",
                "state": "merged",
                "source_branch": "fix/bug",
                "description": "Just a regular fix",
            },
        }
        result = await handler.handle_event("merge_request", payload)
        assert result["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_mr_open_action_ignored(self, handler):
        payload = {
            "object_attributes": {
                "id": 13,
                "action": "open",
                "state": "opened",
                "source_branch": "feat/task-3",
                "description": "PLANE_PROJECT_ID: proj-1\nPLANE_WORK_ITEM_ID: wi-13",
            },
        }
        result = await handler.handle_event("merge_request", payload)
        assert result["status"] == "ignored"


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_duplicate_event_skipped(self, handler, webhook_repo_mock):
        webhook_repo_mock.exists.return_value = True
        payload = {
            "object_attributes": {"id": 99, "status": "success", "ref": "main"},
            "variables": [
                {"key": "PLANE_PROJECT_ID", "value": "proj-1"},
                {"key": "PLANE_WORK_ITEM_ID", "value": "wi-99"},
            ],
        }
        result = await handler.handle_event("pipeline", payload)
        assert result["status"] == "duplicate"


class TestUnhandledEvent:
    @pytest.mark.asyncio
    async def test_unknown_event_ignored(self, handler):
        result = await handler.handle_event("push", {"object_attributes": {"id": 1}})
        assert result["status"] == "ignored"


class TestPlaneInfoExtraction:
    def test_extract_from_ci_variables_list(self):
        payload = {
            "variables": [
                {"key": "PLANE_PROJECT_ID", "value": "proj-a"},
                {"key": "PLANE_WORK_ITEM_ID", "value": "wi-a"},
            ],
        }
        result = GitLabEventHandler._extract_plane_info_from_variables(payload)
        assert result == ("proj-a", "wi-a")

    def test_extract_from_ci_variables_dict(self):
        payload = {
            "variables": {
                "PLANE_PROJECT_ID": "proj-b",
                "PLANE_WORK_ITEM_ID": "wi-b",
            },
        }
        result = GitLabEventHandler._extract_plane_info_from_variables(payload)
        assert result == ("proj-b", "wi-b")

    def test_missing_variables_returns_none(self):
        result = GitLabEventHandler._extract_plane_info_from_variables({"variables": []})
        assert result is None

    def test_extract_from_description(self):
        attrs = {
            "description": "Some text\nPLANE_PROJECT_ID: proj-c\nPLANE_WORK_ITEM_ID: wi-c\nMore text",
        }
        result = GitLabEventHandler._extract_plane_info_from_description(attrs)
        assert result == ("proj-c", "wi-c")

    def test_description_without_plane_info_returns_none(self):
        attrs = {"description": "Just a plain MR description"}
        result = GitLabEventHandler._extract_plane_info_from_description(attrs)
        assert result is None

    def test_description_none_returns_none(self):
        attrs = {"description": None}
        result = GitLabEventHandler._extract_plane_info_from_description(attrs)
        assert result is None


class TestDeliveryId:
    def test_build_delivery_id_pipeline(self):
        payload = {"object_attributes": {"id": 42, "status": "success"}}
        did = GitLabEventHandler._build_delivery_id("pipeline", payload)
        assert did == "gitlab:pipeline:42:success"

    def test_build_delivery_id_mr(self):
        payload = {"object_attributes": {"id": 7, "action": "merge"}}
        did = GitLabEventHandler._build_delivery_id("merge_request", payload)
        assert did == "gitlab:merge_request:7:merge"


class TestPlaneErrorHandling:
    @pytest.mark.asyncio
    async def test_plane_state_update_failure_logged_not_raised(self, handler, plane_mock):
        plane_mock.update_work_item_state.side_effect = RuntimeError("Plane down")
        payload = {
            "object_attributes": {"id": 50, "status": "success", "ref": "main"},
            "variables": [
                {"key": "PLANE_PROJECT_ID", "value": "proj-1"},
                {"key": "PLANE_WORK_ITEM_ID", "value": "wi-50"},
            ],
        }
        result = await handler.handle_event("pipeline", payload)
        assert result["status"] == "synced"

    @pytest.mark.asyncio
    async def test_plane_comment_failure_logged_not_raised(self, handler, plane_mock):
        plane_mock.add_comment.side_effect = RuntimeError("Comment failed")
        payload = {
            "object_attributes": {"id": 51, "status": "running", "ref": "feat/x"},
            "variables": [
                {"key": "PLANE_PROJECT_ID", "value": "proj-1"},
                {"key": "PLANE_WORK_ITEM_ID", "value": "wi-51"},
            ],
        }
        result = await handler.handle_event("pipeline", payload)
        assert result["status"] == "synced"
