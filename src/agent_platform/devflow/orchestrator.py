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

# 标识准备进行 AI 开发的状态集合
READY_FOR_AI_DEV_STATES = {"Ready for AI Dev", "ready_for_ai_dev"}
# 标识 AI 正在开发中的状态
AI_DEVELOPING_STATE = "AI Developing"


@dataclass(frozen=True)
class DevFlowResult:
    """
    开发流程执行结果。
    包含任务包、分支信息以及可能的合并请求(MR)和代码生成任务信息。
    """
    task_pack: DevelopmentTask
    branch: str
    mr_url: str | None = None
    mr_iid: int | None = None
    coding_job: CodingJob | None = None


class DevFlowOrchestrator:
    """
    开发流程编排器。
    负责监听外部系统（如 Plane）的 Webhook 事件，并在状态变更为“准备 AI 开发”时，
    触发代码生成流程，包括创建任务包、创建分支、创建 MR 以及派发代码编写任务。
    """

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
        """
        初始化编排器。

        :param plane: Plane 系统适配器，用于获取和更新 Issue 信息。
        :param gitlab: GitLab 适配器，用于操作代码仓库（分支、MR 等）。
        :param gitlab_project_id: 目标 GitLab 项目的 ID。
        :param webhook_repo: Webhook 持久化仓库，用于幂等性校验。
        :param coding_runner: 代码生成任务运行器，用于执行具体的代码编写。
        :param ai_developing_state_id: “AI Developing” 状态的 ID，用于更新任务状态。
        """
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
        """
        处理 Webhook 事件的主入口。
        解析事件类型与状态，对于符合条件的事件启动 AI 开发流程。

        :param event: Webhook 事件类型名称。
        :param payload: Webhook 携带的数据。
        :return: 如果流程成功触发，则返回执行结果，否则返回 None。
        """
        # 仅处理工作项/问题创建或更新事件
        if event not in {"work_item.updated", "work_item", "issue.updated", "issue"}:
            return None

        work_item = payload.get("data") or payload
        new_state = self._extract_state_name(work_item)
        
        # 如果状态不符合触发条件，则忽略
        if new_state not in READY_FOR_AI_DEV_STATES:
            return None

        project_id = work_item.get("project") or work_item.get("project_id", "")
        work_item_id = work_item.get("id", "")
        title = work_item.get("name") or work_item.get("title", "Untitled")

        # 使用工作项 ID 和状态构建幂等键，防止重复处理
        idempotency_key = f"{work_item_id}:{new_state}"
        if not await self._check_idempotency(idempotency_key, event, payload):
            return None

        # 获取工作项详细信息
        work_item_detail = await self._fetch_work_item_detail(project_id, work_item_id)
        agent_id = self._extract_agent_id(work_item_detail)
        task_type = self._extract_task_type(work_item_detail)

        # 基于需求详情生成开发任务包
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
        # 安全地创建目标分支
        await self._create_branch_safe(branch)

        # 在 GitLab 上创建对应的合并请求
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

        # 将创建的 MR 信息作为评论添加到原工作项中
        await self.plane.add_comment(
            project_id,
            work_item_id,
            f"<p>DevFlow: GitLab MR created — <a href='{mr_url}'>{branch}</a></p>",
        )

        # 更新工作项的状态为“AI 开发中”
        if self.ai_developing_state_id:
            try:
                await self.plane.update_work_item_state(
                    project_id, work_item_id, self.ai_developing_state_id
                )
                logger.info("Moved work item %s to AI Developing", work_item_id)
            except Exception:
                logger.warning("Failed to move work item %s to AI Developing", work_item_id)

        # 将 GitLab 分支及 MR 详情更新到自定义属性中
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
        # 如果配置了代码运行器并成功创建了 MR，则派发代码生成任务
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
        """
        检查 Webhook 事件的幂等性。

        :param key: 唯一标识此事件处理逻辑的键。
        :param event: Webhook 事件类型。
        :param payload: Webhook 携带的数据。
        :return: 如果可以继续处理返回 True，重复事件返回 False。
        """
        if self.webhook_repo is not None:
            # 依赖持久化仓库进行幂等检查
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

        # 如果没有持久化仓库，则使用内存集合回退检查
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
        """
        触发 AI 代码运行器以处理生成的任务包。

        :return: 返回触发的 CodingJob，如果触发失败则返回 None。
        """
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
        """
        从外部系统拉取工作项的详细信息。
        """
        try:
            return await self.plane.get_work_item(project_id, work_item_id)
        except Exception:
            logger.warning("Failed to fetch work item detail: %s/%s", project_id, work_item_id)
            return {}

    async def _create_branch_safe(self, branch: str) -> None:
        """
        安全地创建 GitLab 分支，若分支已存在则记录日志并继续。
        """
        try:
            await self.gitlab.create_branch(self.gitlab_project_id, branch)
        except Exception:
            logger.info("Branch %s may already exist, proceeding", branch)

    @staticmethod
    def _extract_state_name(work_item: dict[str, Any]) -> str:
        """
        从工作项字典中提取当前状态名称。
        """
        state = work_item.get("state_detail") or work_item.get("state") or {}
        if isinstance(state, dict):
            return state.get("name", "")
        return str(state)

    @staticmethod
    def _extract_agent_id(work_item: dict[str, Any]) -> str | None:
        """
        从工作项属性中提取绑定的 Agent ID（如果存在）。
        """
        props = work_item.get("properties") or work_item.get("custom_properties") or {}
        return props.get("agent_id")

    @staticmethod
    def _extract_task_type(work_item: dict[str, Any]) -> str:
        """
        从工作项属性中提取任务类型，默认值为 'platform:change'。
        """
        props = work_item.get("properties") or work_item.get("custom_properties") or {}
        return props.get("task_type", "platform:change")
