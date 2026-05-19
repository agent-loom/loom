"""DevFlow 后台调度器：定期运行状态对账和反馈智能闭环。"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_platform.devflow.reconcile import DevFlowReconciler
    from agent_platform.feedback.service import FeedbackIntelligenceService

logger = logging.getLogger(__name__)


class DevFlowScheduler:
    """后台定时调度 Reconciler 和 FeedbackIntelligenceService。

    使用示例::

        scheduler = DevFlowScheduler(
            reconciler=reconciler,
            feedback_service=feedback_service,
            project_id="proj-xxx",
            reconcile_interval=300,
            feedback_interval=3600,
        )
        await scheduler.start()
        # ... 应用运行期间 ...
        await scheduler.stop()
    """

    def __init__(
        self,
        reconciler: DevFlowReconciler | None = None,
        feedback_service: FeedbackIntelligenceService | None = None,
        project_id: str | None = None,
        reconcile_interval: int = 300,
        feedback_interval: int = 3600,
    ) -> None:
        self._reconciler = reconciler
        self._feedback_service = feedback_service
        self._project_id = project_id
        self._reconcile_interval = reconcile_interval
        self._feedback_interval = feedback_interval
        self._tasks: list[asyncio.Task] = []
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True

        if self._reconciler and self._project_id:
            task = asyncio.create_task(self._reconcile_loop())
            task.set_name("devflow-reconciler")
            self._tasks.append(task)
            logger.info(
                "Reconciler 调度已启动: interval=%ds, project=%s",
                self._reconcile_interval,
                self._project_id,
            )

        if self._feedback_service:
            task = asyncio.create_task(self._feedback_loop())
            task.set_name("feedback-intelligence")
            self._tasks.append(task)
            logger.info(
                "Feedback Intelligence 调度已启动: interval=%ds",
                self._feedback_interval,
            )

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("DevFlow 调度器已停止")

    async def _reconcile_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._reconcile_interval)
                if not self._running:
                    break
                summary = await self._reconciler.run_reconciliation(
                    self._project_id,
                )
                logger.info("定时对账完成: %s", summary)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("定时对账异常，将在下一周期重试")

    async def _feedback_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._feedback_interval)
                if not self._running:
                    break
                result = await self._feedback_service.run(hours=24)
                logger.info(
                    "定时反馈闭环完成: signals=%d proposals=%d approved=%d items=%d",
                    result.signals_collected,
                    result.proposals_generated,
                    result.proposals_approved,
                    result.work_items_created,
                )
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("定时反馈闭环异常，将在下一周期重试")
