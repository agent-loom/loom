"""GitLab webhook 事件处理 — 将 Pipeline/MR 状态反向同步到 Plane。"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from agent_platform.integrations.plane.adapter import PlaneAdapter
from agent_platform.persistence.repositories import WebhookDeliveryRepository

logger = logging.getLogger(__name__)


class GitLabWebhookError(ValueError):
    """GitLab webhook verification failure."""


class GitLabWebhookVerifier:
    """Verify GitLab webhook requests using the shared secret token."""

    def __init__(self, secret: str):
        self.secret = secret

    def verify(self, token: str | None) -> None:
        if not token or not hmac.compare_digest(token, self.secret):
            raise GitLabWebhookError("Invalid or missing X-Gitlab-Token")


@dataclass(frozen=True)
class StateMapping:
    """Maps a GitLab event condition to a Plane state transition."""

    gitlab_event: str
    condition: str
    plane_state_id: str
    description: str


class GitLabEventHandler:
    """Processes GitLab webhook events and syncs state back to Plane.

    Supports pipeline and merge_request events with configurable
    state mappings driven by Plane state IDs from environment config.
    """

    def __init__(
        self,
        plane: PlaneAdapter,
        *,
        webhook_repo: WebhookDeliveryRepository | None = None,
        testing_state_id: str | None = None,
        human_review_state_id: str | None = None,
        staging_state_id: str | None = None,
        done_state_id: str | None = None,
        ai_developing_state_id: str | None = None,
        on_pipeline_failed: Callable[..., Awaitable[Any]] | None = None,
    ):
        self.plane = plane
        self.webhook_repo = webhook_repo
        self._state_ids = {
            "testing": testing_state_id,
            "human_review": human_review_state_id,
            "staging": staging_state_id,
            "done": done_state_id,
            "ai_developing": ai_developing_state_id,
        }
        self._on_pipeline_failed = on_pipeline_failed

    async def handle_event(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Route a GitLab webhook event to the appropriate handler.

        Returns a summary dict with the action taken.
        """
        delivery_id = self._build_delivery_id(event_type, payload)
        if self.webhook_repo and await self.webhook_repo.exists(delivery_id):
            return {"status": "duplicate", "delivery_id": delivery_id}

        if self.webhook_repo:
            await self.webhook_repo.record(
                delivery_id=delivery_id,
                source="gitlab",
                event_type=event_type,
                status="processing",
                payload=payload,
            )

        result: dict[str, Any]
        if event_type == "pipeline":
            result = await self._handle_pipeline(payload)
        elif event_type == "merge_request":
            result = await self._handle_merge_request(payload)
        else:
            result = {"status": "ignored", "reason": f"unhandled event: {event_type}"}

        return result

    async def _handle_pipeline(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Map pipeline status to Plane state transitions."""
        attrs = payload.get("object_attributes", {})
        status = attrs.get("status", "")
        ref = attrs.get("ref", "")

        plane_info = self._extract_plane_info_from_variables(payload)
        if not plane_info:
            return {"status": "skipped", "reason": "no plane metadata in pipeline variables"}

        project_id, work_item_id = plane_info

        target_state: str | None = None
        action = ""

        if status == "running":
            target_state = self._state_ids.get("testing")
            action = "pipeline_running → Testing"
        elif status == "failed":
            target_state = self._state_ids.get("ai_developing")
            action = "pipeline_failed → AI Developing"
        elif status == "success":
            target_state = self._state_ids.get("human_review")
            action = "pipeline_success → Human Review"

        if target_state:
            await self._update_plane_state(project_id, work_item_id, target_state, action)
            comment = f"<p>GitLab Pipeline <strong>{status}</strong> on <code>{ref}</code></p>"
            await self._add_plane_comment(project_id, work_item_id, comment)

            if status == "failed" and self._on_pipeline_failed:
                try:
                    await self._on_pipeline_failed(
                        project_id=project_id,
                        work_item_id=work_item_id,
                        ref=ref,
                    )
                except Exception:
                    logger.warning(
                        "Pipeline 失败回调执行异常: project=%s item=%s",
                        project_id, work_item_id, exc_info=True,
                    )

            return {"status": "synced", "action": action, "ref": ref}

        return {"status": "ignored", "reason": f"no mapping for pipeline status: {status}"}

    async def _handle_merge_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Map MR events (merge, close) to Plane state transitions."""
        attrs = payload.get("object_attributes", {})
        action = attrs.get("action", "")
        state = attrs.get("state", "")
        source_branch = attrs.get("source_branch", "")

        plane_info = self._extract_plane_info_from_description(attrs)
        if not plane_info:
            plane_info = self._extract_plane_info_from_variables(payload)
        if not plane_info:
            return {"status": "skipped", "reason": "no plane metadata in MR"}

        project_id, work_item_id = plane_info

        target_state: str | None = None
        desc = ""

        if action == "merge" or state == "merged":
            target_state = self._state_ids.get("staging")
            desc = "MR merged → Staging"
        elif action == "close" or state == "closed":
            target_state = self._state_ids.get("ai_developing")
            desc = "MR closed → AI Developing"

        if target_state:
            await self._update_plane_state(project_id, work_item_id, target_state, desc)
            comment = (
                f"<p>GitLab MR <strong>{action or state}</strong> "
                f"on <code>{source_branch}</code></p>"
            )
            await self._add_plane_comment(project_id, work_item_id, comment)
            return {"status": "synced", "action": desc, "branch": source_branch}

        return {"status": "ignored", "reason": f"no mapping for MR action: {action}"}

    async def _update_plane_state(
        self,
        project_id: str,
        work_item_id: str,
        state_id: str,
        action: str,
    ) -> None:
        try:
            await self.plane.update_work_item_state(project_id, work_item_id, state_id)
            logger.info("Plane state sync: %s for %s", action, work_item_id)
        except Exception:
            logger.warning("Failed to sync Plane state: %s for %s", action, work_item_id)

    async def _add_plane_comment(
        self,
        project_id: str,
        work_item_id: str,
        comment: str,
    ) -> None:
        try:
            await self.plane.add_comment(project_id, work_item_id, comment)
        except Exception:
            logger.warning("Failed to add Plane comment for %s", work_item_id)

    @staticmethod
    def _extract_plane_info_from_variables(
        payload: dict[str, Any],
    ) -> tuple[str, str] | None:
        """Extract Plane project_id and work_item_id from CI variables."""
        variables = payload.get("variables", [])
        if isinstance(variables, list):
            var_map = {v.get("key"): v.get("value") for v in variables if isinstance(v, dict)}
        elif isinstance(variables, dict):
            var_map = variables
        else:
            return None

        project_id = var_map.get("PLANE_PROJECT_ID")
        work_item_id = var_map.get("PLANE_WORK_ITEM_ID")
        if project_id and work_item_id:
            return project_id, work_item_id
        return None

    @staticmethod
    def _extract_plane_info_from_description(
        attrs: dict[str, Any],
    ) -> tuple[str, str] | None:
        """Extract Plane IDs from MR description metadata comments.

        支持两种格式:
        1. HTML 注释: <!-- devflow:plane_project_id=X plane_work_item_id=Y -->
        2. 纯文本: PLANE_PROJECT_ID: X ... PLANE_WORK_ITEM_ID: Y
        """
        description = attrs.get("description", "") or ""
        import re

        # 格式 1: HTML 注释（orchestrator 自动嵌入）
        match = re.search(
            r"devflow:plane_project_id=(\S+)\s+plane_work_item_id=(\S+)",
            description,
        )
        if match:
            return match.group(1), match.group(2)

        # 格式 2: 纯文本标签（向后兼容）
        match = re.search(
            r"PLANE_PROJECT_ID:\s*(\S+).*?PLANE_WORK_ITEM_ID:\s*(\S+)",
            description,
            re.DOTALL,
        )
        if match:
            return match.group(1), match.group(2)
        return None

    @staticmethod
    def _build_delivery_id(event_type: str, payload: dict[str, Any]) -> str:
        """Build a unique delivery ID for idempotency."""
        attrs = payload.get("object_attributes", {})
        obj_id = attrs.get("id", "")
        status = attrs.get("status", attrs.get("action", ""))
        return f"gitlab:{event_type}:{obj_id}:{status}"
