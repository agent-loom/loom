from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from agent_platform.devflow.runner.models import CodingJob
from agent_platform.devflow.task_pack import DevelopmentTask, TaskPackGenerator
from agent_platform.integrations.gitlab.adapter import GitLabAdapter
from agent_platform.integrations.plane.adapter import PlaneAdapter
from agent_platform.persistence.repositories import WebhookDeliveryRepository

logger = logging.getLogger(__name__)

READY_FOR_AI_DEV_STATES = {"Ready for AI Dev", "ready_for_ai_dev"}
AI_DEVELOPING_STATE = "AI Developing"


@dataclass(frozen=True)
class DevFlowResult:
    task_pack: DevelopmentTask
    branch: str
    mr_url: str | None = None
    mr_iid: int | None = None
    coding_job: CodingJob | None = None


class DevFlowOrchestrator:
    def __init__(
        self,
        plane: PlaneAdapter,
        gitlab: GitLabAdapter,
        gitlab_project_id: str,
        *,
        webhook_repo: WebhookDeliveryRepository | None = None,
        coding_runner: Any | None = None,
        ai_developing_state_id: str | None = None,
    ):
        self.plane = plane
        self.gitlab = gitlab
        self.gitlab_project_id = gitlab_project_id
        self.webhook_repo = webhook_repo
        self.coding_runner = coding_runner
        self.ai_developing_state_id = ai_developing_state_id
        self.task_pack_generator = TaskPackGenerator()
        self._processed_items: set[str] = set()

    async def handle_webhook_event(
        self,
        event: str,
        payload: dict[str, Any],
    ) -> DevFlowResult | None:
        if event not in {"work_item.updated", "work_item", "issue.updated", "issue"}:
            return None

        work_item = payload.get("data") or payload
        new_state = self._extract_state_name(work_item)
        if new_state not in READY_FOR_AI_DEV_STATES:
            return None

        project_id = work_item.get("project") or work_item.get("project_id", "")
        work_item_id = work_item.get("id", "")
        title = work_item.get("name") or work_item.get("title", "Untitled")

        idempotency_key = f"{work_item_id}:{new_state}"
        if not await self._check_idempotency(idempotency_key, event, payload):
            return None

        work_item_detail = await self._fetch_work_item_detail(project_id, work_item_id)
        agent_id = self._extract_agent_id(work_item_detail)
        task_type = self._extract_task_type(work_item_detail)

        task_pack = self.task_pack_generator.from_requirement(
            task_id=str(work_item_id),
            title=title,
            task_type=task_type,
            project_id=self.gitlab_project_id,
            background=work_item_detail.get("description_stripped") or title,
            agent_id=agent_id,
            source={"system": "plane", "issue_id": str(work_item_id)},
        )

        branch = task_pack.repository.work_branch
        await self._create_branch_safe(branch)

        mr_result = await self.gitlab.create_merge_request(
            project_id=self.gitlab_project_id,
            source_branch=branch,
            target_branch=task_pack.repository.default_branch,
            title=task_pack.repository.merge_request.title,
            description=task_pack.repository.merge_request.description,
            labels=task_pack.repository.merge_request.labels,
        )

        mr_url = mr_result.get("web_url")
        mr_iid = mr_result.get("iid")

        await self.plane.add_comment(
            project_id,
            work_item_id,
            f"<p>DevFlow: GitLab MR created — <a href=\"{mr_url}\">{branch}</a></p>",
        )

        if self.ai_developing_state_id:
            try:
                await self.plane.update_work_item_state(
                    project_id, work_item_id, self.ai_developing_state_id
                )
                logger.info("Moved work item %s to AI Developing", work_item_id)
            except Exception:
                logger.warning("Failed to move work item %s to AI Developing", work_item_id)

        try:
            await self.plane.update_custom_properties(
                project_id,
                work_item_id,
                {
                    "gitlab_branch": branch,
                    "gitlab_mr_url": mr_url,
                    "gitlab_mr_iid": str(mr_iid) if mr_iid else None,
                },
            )
        except Exception:
            logger.warning("Failed to update custom properties for %s", work_item_id)

        coding_job: CodingJob | None = None
        if self.coding_runner is not None and mr_iid is not None:
            coding_job = await self._dispatch_runner(
                task_pack, mr_iid=mr_iid,
                plane_project_id=project_id,
                plane_work_item_id=work_item_id,
            )

        logger.info("DevFlow: %s -> branch=%s mr=%s", work_item_id, branch, mr_url)
        return DevFlowResult(
            task_pack=task_pack,
            branch=branch,
            mr_url=mr_url,
            mr_iid=mr_iid,
            coding_job=coding_job,
        )

    async def _check_idempotency(
        self,
        key: str,
        event: str,
        payload: dict[str, Any],
    ) -> bool:
        """Return True if event should be processed, False if duplicate."""
        if self.webhook_repo is not None:
            if await self.webhook_repo.exists(key):
                logger.info("Skipping duplicate event (persistent): %s", key)
                return False
            await self.webhook_repo.record(
                delivery_id=key,
                source="plane",
                event_type=event,
                status="processing",
                payload=payload,
            )
            return True

        if key in self._processed_items:
            logger.info("Skipping duplicate event: %s", key)
            return False
        self._processed_items.add(key)
        return True

    async def _dispatch_runner(
        self,
        task_pack: DevelopmentTask,
        *,
        mr_iid: int,
        plane_project_id: str,
        plane_work_item_id: str,
    ) -> CodingJob | None:
        try:
            job: CodingJob = await self.coding_runner.run(
                task_pack,
                mr_iid=mr_iid,
                plane_project_id=plane_project_id,
                plane_work_item_id=plane_work_item_id,
            )
            return job
        except Exception:
            logger.exception(
                "CodingAgentRunner dispatch failed for MR !%s", mr_iid,
            )
            return None

    async def _fetch_work_item_detail(
        self, project_id: str, work_item_id: str
    ) -> dict[str, Any]:
        try:
            return await self.plane.get_work_item(project_id, work_item_id)
        except Exception:
            logger.warning("Failed to fetch work item detail: %s/%s", project_id, work_item_id)
            return {}

    async def _create_branch_safe(self, branch: str) -> None:
        try:
            await self.gitlab.create_branch(self.gitlab_project_id, branch)
        except Exception:
            logger.info("Branch %s may already exist, proceeding", branch)

    @staticmethod
    def _extract_state_name(work_item: dict[str, Any]) -> str:
        state = work_item.get("state_detail") or work_item.get("state") or {}
        if isinstance(state, dict):
            return state.get("name", "")
        return str(state)

    @staticmethod
    def _extract_agent_id(work_item: dict[str, Any]) -> str | None:
        props = work_item.get("properties") or work_item.get("custom_properties") or {}
        return props.get("agent_id")

    @staticmethod
    def _extract_task_type(work_item: dict[str, Any]) -> str:
        props = work_item.get("properties") or work_item.get("custom_properties") or {}
        return props.get("task_type", "platform:change")
