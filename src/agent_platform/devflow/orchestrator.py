from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from agent_platform.devflow.task_pack import DevelopmentTask, TaskPackGenerator
from agent_platform.integrations.gitlab.adapter import GitLabAdapter
from agent_platform.integrations.plane.adapter import PlaneAdapter

logger = logging.getLogger(__name__)

READY_FOR_AI_DEV_STATES = {"Ready for AI Dev", "ready_for_ai_dev"}


@dataclass(frozen=True)
class DevFlowResult:
    task_pack: DevelopmentTask
    branch: str
    mr_url: str | None = None
    mr_iid: int | None = None


class DevFlowOrchestrator:
    def __init__(
        self,
        plane: PlaneAdapter,
        gitlab: GitLabAdapter,
        gitlab_project_id: str,
    ):
        self.plane = plane
        self.gitlab = gitlab
        self.gitlab_project_id = gitlab_project_id
        self.task_pack_generator = TaskPackGenerator()

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

        logger.info("DevFlow: %s -> branch=%s mr=%s", work_item_id, branch, mr_url)
        return DevFlowResult(
            task_pack=task_pack,
            branch=branch,
            mr_url=mr_url,
            mr_iid=mr_iid,
        )

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
