from __future__ import annotations

import asyncio
import html
import logging
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from agent_platform.devflow.ownership import AgentOwnershipResolver
from agent_platform.devflow.runner.models import CodingJob
from agent_platform.devflow.state_machine import DevFlowState
from agent_platform.devflow.state_sync import DevFlowStateSync
from agent_platform.devflow.task_pack import DevelopmentTask, TaskPackGenerator
from agent_platform.integrations.plane.adapter import PlaneAdapter
from agent_platform.integrations.scm.protocol import ScmAdapter
from agent_platform.persistence.repositories import WebhookDeliveryRepository

logger = logging.getLogger(__name__)

READY_FOR_AI_DEV_STATES = {"Ready for AI Dev", "ready_for_ai_dev"}
AI_DEVELOPING_STATE = "AI Developing"


@dataclass(frozen=True)
class DevFlowResult:
    """开发流程执行结果。"""
    task_pack: DevelopmentTask
    branch: str
    mr_url: str | None = None
    mr_iid: int | None = None
    coding_job: CodingJob | None = None
    job_submitted: bool = False


class DevFlowOrchestrator:
    """
    开发流程编排器。
    负责监听外部系统（如 Plane）的 Webhook 事件，并在状态变更为"准备 AI 开发"时，
    触发代码生成流程，包括创建任务包、创建分支以及派发代码编写任务。
    MR 的创建推迟到 Runner 完成 commit+push 后执行。
    """

    def __init__(
        self,
        plane: PlaneAdapter,
        gitlab: ScmAdapter,
        gitlab_project_id: str,
        *,
        webhook_repo: WebhookDeliveryRepository | None = None,
        coding_runner: Any | None = None,
        job_queue: Any | None = None,
        ai_developing_state_id: str | None = None,
        default_branch: str = "main",
        state_sync: DevFlowStateSync | None = None,
        ownership_resolver: AgentOwnershipResolver | None = None,
    ):
        self.plane = plane
        self.gitlab = gitlab
        self.gitlab_project_id = gitlab_project_id
        self.webhook_repo = webhook_repo
        self.coding_runner = coding_runner
        self.job_queue = job_queue
        self.ai_developing_state_id = ai_developing_state_id
        self.default_branch = default_branch
        self.state_sync = state_sync
        self.ownership_resolver = ownership_resolver or AgentOwnershipResolver()
        self.task_pack_generator = TaskPackGenerator()
        self._max_processed = 10_000
        self._processed_items: OrderedDict[str, None] = OrderedDict()
        self._idempotency_lock = asyncio.Lock()

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
        delivery_id = self._extract_delivery_id(payload)
        project_id = work_item.get("project") or work_item.get("project_id", "")
        work_item_id = work_item.get("id", "")
        title = work_item.get("name") or work_item.get("title", "Untitled")

        logger.info(
            "DevFlow webhook received: event=%s delivery=%s project=%s item=%s state=%s title=%s",
            event,
            delivery_id,
            project_id,
            work_item_id,
            new_state,
            title,
        )
        
        # 如果状态不符合触发条件，则忽略
        if new_state not in READY_FOR_AI_DEV_STATES:
            return None

        # 使用 project_id + 工作项 ID + 状态构建幂等键：
        # HTTP webhook 层已经按 x-plane-delivery 去重；这里优先把 delivery 放进 key，
        # 避免同一个 item 失败后人工重新切回 Ready for AI Dev 时被永久拦截。
        idempotency_key = self._build_idempotency_key(
            project_id=project_id,
            work_item_id=work_item_id,
            state=new_state,
            delivery_id=delivery_id,
            work_item=work_item,
        )
        # 原子幂等检查：加锁防止并发 webhook 重复触发
        async with self._idempotency_lock:
            if not await self._check_idempotency(idempotency_key, event, payload):
                return None

        # 获取工作项详细信息
        work_item_detail = await self._fetch_work_item_detail(project_id, work_item_id)
        ownership = self.ownership_resolver.resolve(
            work_item=work_item,
            work_item_detail=work_item_detail,
        )
        if ownership is None:
            await self._report_missing_agent_ownership(project_id, work_item_id)
            logger.warning(
                "DevFlow ownership unresolved: work_item=%s project=%s title=%s",
                work_item_id,
                project_id,
                title,
            )
            return None

        agent_id = ownership.agent_id
        task_type = ownership.task_type
        logger.info(
            "DevFlow ownership resolved: item=%s agent_id=%s task_type=%s source=%s",
            work_item_id,
            agent_id,
            task_type,
            ownership.source,
        )
        background = (
            work_item_detail.get("description_stripped")
            or work_item_detail.get("description")
            or work_item.get("description_stripped")
            or work_item.get("description")
            or title
        )

        # 状态同步：需求解析完成 → READY_FOR_AI_DEV
        await self._sync_state(
            work_item_id, project_id, DevFlowState.READY_FOR_AI_DEV,
            reason="需求解析完成，准备 AI 开发",
        )

        # 基于需求详情生成开发任务包
        task_pack = self.task_pack_generator.from_requirement(
            task_id=str(work_item_id),
            title=title,
            task_type=task_type,
            project_id=self.gitlab_project_id,
            background=background,
            agent_id=agent_id,
            source={"system": "plane", "issue_id": str(work_item_id)},
        )
        task_pack.repository.default_branch = self.default_branch

        branch = task_pack.repository.work_branch
        # 安全地创建目标分支
        await self._create_branch_safe(branch)

        try:
            safe_branch = html.escape(branch)
            await self.plane.add_comment(
                project_id,
                work_item_id,
                f"<p>DevFlow: 分支已创建 — <code>{safe_branch}</code>，AI 正在编码...</p>",
            )
        except Exception:
            logger.warning(
                "Failed to add branch comment to Plane work item %s",
                work_item_id, exc_info=True,
            )

        if self.ai_developing_state_id:
            try:
                await self.plane.update_work_item_state(
                    project_id, work_item_id, self.ai_developing_state_id
                )
                logger.info("Moved work item %s to AI Developing", work_item_id)
            except Exception:
                logger.warning(
                    "Failed to move work item %s to AI Developing",
                    work_item_id, exc_info=True,
                )

        try:
            await self.plane.update_custom_properties(
                project_id,
                work_item_id,
                {
                    "agent_id": agent_id,
                    "task_type": task_type,
                    "agent_ownership_source": ownership.source,
                    "agent_ownership_confidence": str(ownership.confidence),
                    "gitlab_branch": branch,
                },
            )
        except Exception:
            logger.warning("Failed to update custom properties for %s", work_item_id, exc_info=True)

        coding_job: CodingJob | None = None
        job_submitted = False
        if self.coding_runner is not None:
            # 状态同步：Runner 开始执行 → AI_DEVELOPING
            await self._sync_state(
                work_item_id, project_id, DevFlowState.AI_DEVELOPING,
                reason="Runner 开始执行编码任务",
            )
            coding_job, job_submitted = await self._dispatch_runner(
                task_pack,
                plane_project_id=project_id,
                plane_work_item_id=work_item_id,
            )

        logger.info("DevFlow: %s -> branch=%s", work_item_id, branch)
        return DevFlowResult(
            task_pack=task_pack,
            branch=branch,
            coding_job=coding_job,
            job_submitted=job_submitted,
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
        self._processed_items[key] = None
        if len(self._processed_items) > self._max_processed:
            self._processed_items.popitem(last=False)
        return True

    @staticmethod
    def _extract_delivery_id(payload: dict[str, Any]) -> str | None:
        meta = payload.get("_devflow") or {}
        if isinstance(meta, dict) and meta.get("delivery_id"):
            return str(meta["delivery_id"])
        delivery_id = payload.get("delivery_id") or payload.get("idempotency_key")
        return str(delivery_id) if delivery_id else None

    @staticmethod
    def _build_idempotency_key(
        *,
        project_id: str,
        work_item_id: str,
        state: str,
        delivery_id: str | None,
        work_item: dict[str, Any],
    ) -> str:
        if delivery_id:
            return f"plane-delivery:{delivery_id}"
        updated_at = (
            work_item.get("updated_at")
            or work_item.get("updated")
            or work_item.get("modified_at")
        )
        if updated_at:
            return f"{project_id}:{work_item_id}:{state}:{updated_at}"
        return f"{project_id}:{work_item_id}:{state}"

    async def _dispatch_runner(
        self,
        task_pack: DevelopmentTask,
        *,
        plane_project_id: str,
        plane_work_item_id: str,
    ) -> tuple[CodingJob | None, bool]:
        """派发编码任务到 Runner — 通过异步队列或直接等待。

        Returns (job, submitted_async) where submitted_async=True means the job
        was submitted to the queue and will complete asynchronously.
        """
        runner_kwargs = dict(
            plane_project_id=plane_project_id,
            plane_work_item_id=plane_work_item_id,
        )

        if self.job_queue is not None:
            try:
                job_id = f"{plane_work_item_id}-runner"
                await self.job_queue.submit(
                    job_id,
                    lambda: self.coding_runner.run(task_pack, **runner_kwargs),
                )
                logger.info("Job %s submitted to async queue", job_id)
                return None, True
            except Exception:
                logger.exception("Failed to submit job to queue for item %s", plane_work_item_id)
                return None, False

        try:
            job: CodingJob = await self.coding_runner.run(task_pack, **runner_kwargs)
            return job, False
        except Exception:
            logger.exception("CodingAgentRunner dispatch failed for item %s", plane_work_item_id)
            return None, False

    async def _fetch_work_item_detail(
        self, project_id: str, work_item_id: str
    ) -> dict[str, Any]:
        """
        从外部系统拉取工作项的详细信息。
        """
        try:
            return await self.plane.get_work_item(project_id, work_item_id)
        except Exception:
            logger.warning(
                "Failed to fetch work item detail: %s/%s",
                project_id, work_item_id, exc_info=True,
            )
        return {}

    async def _report_missing_agent_ownership(
        self,
        project_id: str,
        work_item_id: str,
    ) -> None:
        try:
            await self.plane.add_comment(
                project_id,
                work_item_id,
                (
                    "<p>DevFlow: 无法确定此需求归属的 agent_id。"
                    "请在 Work Item custom property 中补充 "
                    "<code>agent_id</code>，或配置 Plane Project/Label 到 Agent 的映射后"
                    "重新切换到 Ready for AI Dev。</p>"
                ),
            )
        except Exception:
            logger.warning(
                "Failed to add missing ownership comment to work item %s",
                work_item_id,
                exc_info=True,
            )

    async def _create_branch_safe(self, branch: str) -> None:
        try:
            await self.gitlab.create_branch(
                self.gitlab_project_id, branch, ref=self.default_branch,
            )
        except Exception:
            logger.debug("Branch %s may already exist, proceeding", branch, exc_info=True)

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
    def _extract_task_type(
        work_item: dict[str, Any],
        *,
        fallback: str | None = "platform:change",
    ) -> str | None:
        """
        从工作项属性中提取任务类型，默认值为 'platform:change'。
        """
        props = work_item.get("properties") or work_item.get("custom_properties") or {}
        return props.get("task_type") or fallback

    async def _sync_state(
        self,
        work_item_id: str,
        project_id: str,
        new_state: DevFlowState,
        *,
        reason: str = "",
    ) -> None:
        """向后兼容的状态同步辅助方法。

        如果 state_sync 不存在则跳过，失败时仅记录警告不阻塞主流程。
        """
        if self.state_sync is None:
            return
        try:
            await self.state_sync.sync_to_plane(
                work_item_id, project_id, new_state, reason=reason,
            )
        except Exception:
            logger.warning(
                "状态同步失败: work_item=%s, target_state=%s",
                work_item_id,
                new_state.value,
            )
