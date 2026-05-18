"""S8 Phase 3 — Plane+GitLab 端到端联调测试。

覆盖 Plane bootstrap 状态发现、GitLab MR 元数据解析、DLQ get_entry Protocol 一致性。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agent_platform.integrations.gitlab.webhook import GitLabEventHandler
from agent_platform.integrations.plane.bootstrap import PlaneBootstrap
from agent_platform.webhooks.dead_letter import (
    DeadLetterEntry,
    DeadLetterQueue,
    InMemoryDeadLetterQueue,
)


class TestPlaneBootstrap:
    """PlaneBootstrap 状态发现测试。"""

    @pytest.mark.asyncio
    async def test_discover_state_map(self):
        mock_plane = AsyncMock()
        mock_plane.list_states = AsyncMock(return_value=[
            {"id": "s1", "name": "Intake"},
            {"id": "s2", "name": "AI Developing"},
            {"id": "s3", "name": "Done"},
        ])

        bootstrap = PlaneBootstrap(mock_plane)
        state_map = await bootstrap.discover_state_map("proj-1")

        assert state_map["Intake"] == "s1"
        assert state_map["AI Developing"] == "s2"
        assert state_map["Done"] == "s3"
        assert len(state_map) == 3

    @pytest.mark.asyncio
    async def test_bootstrap_reports_existing_and_missing(self):
        mock_plane = AsyncMock()
        mock_plane.list_states = AsyncMock(return_value=[
            {"id": "s1", "name": "Intake"},
            {"id": "s2", "name": "Ready for AI Dev"},
            {"id": "s3", "name": "AI Developing"},
            {"id": "s4", "name": "AI Review"},
            {"id": "s5", "name": "Human Review"},
            {"id": "s6", "name": "Ready for Merge"},
            {"id": "s7", "name": "Done"},
            {"id": "s8", "name": "Rejected"},
        ])

        bootstrap = PlaneBootstrap(mock_plane)
        result = await bootstrap.bootstrap("proj-1")

        assert len(result.existing) == 8
        assert result.state_map["Done"] == "s7"

    @pytest.mark.asyncio
    async def test_resolve_state_ids(self):
        mock_plane = AsyncMock()
        mock_plane.list_states = AsyncMock(return_value=[
            {"id": "s1", "name": "AI Developing"},
            {"id": "s2", "name": "Human Review"},
            {"id": "s3", "name": "Done"},
        ])

        bootstrap = PlaneBootstrap(mock_plane)
        ids = await bootstrap.resolve_state_ids("proj-1")

        assert ids["ai_developing"] == "s1"
        assert ids["human_review"] == "s2"
        assert ids["done"] == "s3"
        assert ids["testing"] is None
        assert ids["staging"] is None


class TestGitLabMRMetadataExtraction:
    """GitLab MR 描述中 Plane 元数据提取测试。"""

    def test_extract_html_comment_format(self):
        attrs = {
            "description": (
                "## Source Task\nT-100\n\n"
                "<!-- devflow:plane_project_id=pp-1 plane_work_item_id=wi-99 -->\n"
            ),
        }
        result = GitLabEventHandler._extract_plane_info_from_description(attrs)
        assert result == ("pp-1", "wi-99")

    def test_extract_legacy_text_format(self):
        attrs = {
            "description": "PLANE_PROJECT_ID: proj-abc\nPLANE_WORK_ITEM_ID: wi-123",
        }
        result = GitLabEventHandler._extract_plane_info_from_description(attrs)
        assert result == ("proj-abc", "wi-123")

    def test_extract_returns_none_without_metadata(self):
        attrs = {"description": "普通 MR 描述，没有元数据"}
        result = GitLabEventHandler._extract_plane_info_from_description(attrs)
        assert result is None

    def test_extract_none_description(self):
        attrs = {"description": None}
        result = GitLabEventHandler._extract_plane_info_from_description(attrs)
        assert result is None

    @pytest.mark.asyncio
    async def test_mr_merge_syncs_to_plane(self):
        mock_plane = AsyncMock()
        mock_plane.update_work_item_state = AsyncMock()
        mock_plane.add_comment = AsyncMock()

        handler = GitLabEventHandler(
            plane=mock_plane,
            staging_state_id="state-staging",
        )

        result = await handler.handle_event("merge_request", {
            "object_attributes": {
                "id": 42,
                "action": "merge",
                "state": "merged",
                "source_branch": "feat/test",
                "description": "<!-- devflow:plane_project_id=pp-1 plane_work_item_id=wi-50 -->",
            },
        })

        assert result["status"] == "synced"
        mock_plane.update_work_item_state.assert_called_once_with(
            "pp-1", "wi-50", "state-staging",
        )

    @pytest.mark.asyncio
    async def test_pipeline_success_syncs_to_human_review(self):
        mock_plane = AsyncMock()
        mock_plane.update_work_item_state = AsyncMock()
        mock_plane.add_comment = AsyncMock()

        handler = GitLabEventHandler(
            plane=mock_plane,
            human_review_state_id="state-hr",
        )

        result = await handler.handle_event("pipeline", {
            "object_attributes": {
                "id": 100,
                "status": "success",
                "ref": "feat/fix-xss",
            },
            "variables": [
                {"key": "PLANE_PROJECT_ID", "value": "pp-2"},
                {"key": "PLANE_WORK_ITEM_ID", "value": "wi-60"},
            ],
        })

        assert result["status"] == "synced"
        mock_plane.update_work_item_state.assert_called_once_with(
            "pp-2", "wi-60", "state-hr",
        )


class TestDLQGetEntryProtocol:
    """验证 DLQ get_entry 方法的 Protocol 一致性。"""

    @pytest.mark.asyncio
    async def test_get_entry_found(self):
        dlq = InMemoryDeadLetterQueue()
        entry = DeadLetterEntry(
            source="plane", event_type="test", error_message="err",
        )
        await dlq.enqueue(entry)

        found = await dlq.get_entry(entry.id)
        assert found is not None
        assert found.id == entry.id

    @pytest.mark.asyncio
    async def test_get_entry_not_found(self):
        dlq = InMemoryDeadLetterQueue()
        found = await dlq.get_entry("nonexistent")
        assert found is None

    def test_implements_protocol(self):
        assert isinstance(InMemoryDeadLetterQueue(), DeadLetterQueue)
