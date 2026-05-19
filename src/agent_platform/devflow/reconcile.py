"""DevFlow 状态对账（Reconciliation）服务。

负责定期或按需对账 DevFlow 的本地状态、GitLab 流水线/MR 状态与 Plane 工单状态。
防止因为 webhook 漏发、网络故障或 GitLab pipeline 崩溃导致 Plane 工单永远卡在中间状态（如 Testing）。
"""

import asyncio
import logging
from typing import Any

from agent_platform.devflow.state_machine import DevFlowState
from agent_platform.devflow.state_sync import DevFlowStateSync
from agent_platform.integrations.plane.adapter import PlaneAdapter
from agent_platform.integrations.scm.protocol import ScmAdapter

logger = logging.getLogger(__name__)


class DevFlowReconciler:
    """状态机重对账服务。"""

    def __init__(
        self,
        state_sync: DevFlowStateSync,
        plane: PlaneAdapter,
        gitlab: ScmAdapter,
        gitlab_project_id: str,
    ) -> None:
        self.state_sync = state_sync
        self.plane = plane
        self.gitlab = gitlab
        self.gitlab_project_id = gitlab_project_id

    async def reconcile_item(
        self,
        project_id: str,
        work_item_id: str,
        current_state: str,
        custom_properties: dict[str, Any],
    ) -> None:
        """对账单个工作项的状态。

        如果 Plane 状态与 GitLab MR 或 Pipeline 的真实状态不符，进行同步修复。
        """
        try:
            expected_state = self.state_sync.from_plane_state(current_state)
        except ValueError:
            # 状态不在映射表中（非 DevFlow 关心的状态），无需对账
            return

        # 获取 MR 信息
        mr_iid_str = custom_properties.get("gitlab_mr_iid")
        if not mr_iid_str:
            return

        try:
            mr_iid = int(mr_iid_str)
        except ValueError:
            return

        # 从 GitLab 拉取真实 MR 状态
        try:
            mr_info = await self.gitlab.get_merge_request(self.gitlab_project_id, mr_iid)
        except Exception:
            logger.warning("对账失败：无法获取 MR %s 信息", mr_iid, exc_info=True)
            return

        # 检查 MR 是否已合并/关闭
        mr_state = mr_info.get("state")
        if mr_state == "merged":
            if expected_state not in (DevFlowState.DONE, DevFlowState.READY_FOR_MERGE):
                logger.info("对账纠正：工作项 %s 的 MR %s 已合并，状态应为 Done", work_item_id, mr_iid)
                await self.state_sync.sync_to_plane(
                    work_item_id, project_id, DevFlowState.DONE,
                    actor="reconciler", reason="MR 已合并（对账纠正）"
                )
            return

        if mr_state == "closed":
            if expected_state != DevFlowState.REJECTED:
                logger.info("对账纠正：工作项 %s 的 MR %s 已关闭，状态应为 Rejected", work_item_id, mr_iid)
                await self.state_sync.sync_to_plane(
                    work_item_id, project_id, DevFlowState.REJECTED,
                    actor="reconciler", reason="MR 已关闭（对账纠正）"
                )
            return

        # 如果 MR 仍然开放且状态是 Testing，检查最新的 Pipeline 状态
        if expected_state == DevFlowState.AI_REVIEW:  # "Testing" 对应的逻辑状态
            source_branch = mr_info.get("source_branch")
            if source_branch:
                try:
                    pipeline_status = await self.gitlab.get_pipeline_status(
                        self.gitlab_project_id, source_branch
                    )
                    if pipeline_status == "success":
                        logger.info("对账纠正：工作项 %s 的 Pipeline %s 成功，推进至 Human Review", work_item_id, pipeline_status)
                        await self.state_sync.sync_to_plane(
                            work_item_id, project_id, DevFlowState.HUMAN_REVIEW,
                            actor="reconciler", reason="Pipeline 成功（对账纠正）"
                        )
                    elif pipeline_status in ("failed", "canceled"):
                        logger.info("对账纠正：工作项 %s 的 Pipeline %s 失败，回退至 AI Developing", work_item_id, pipeline_status)
                        await self.state_sync.sync_to_plane(
                            work_item_id, project_id, DevFlowState.AI_DEVELOPING,
                            actor="reconciler", reason="Pipeline 失败（对账纠正）"
                        )
                except Exception:
                    logger.debug("获取 MR %s 的 pipeline 失败", mr_iid, exc_info=True)

    async def run_reconciliation(self, project_id: str) -> dict[str, Any]:
        """全量对账指定项目下的 DevFlow 工作项。

        从 Plane 拉取所有工作项，过滤出 DevFlow 关心的中间状态项，
        每批最多 5 个并发调用 reconcile_item()，并汇总日志。

        Returns:
            对账结果摘要字典。
        """
        # 1. 从 Plane 拉取所有工作项
        try:
            response = await self.plane.list_work_items(project_id)
        except Exception:
            logger.error("对账失败：无法从 Plane 拉取项目 %s 的工作项", project_id, exc_info=True)
            return {"status": "error", "total_candidates": 0, "processed": 0}

        results = response.get("results", [])
        logger.info("对账开始：项目 %s 共获取到 %d 个工作项", project_id, len(results))

        # 2. 过滤出 DevFlow 关心的中间状态（from_plane_state 不抛 ValueError 的状态）
        candidates: list[dict[str, Any]] = []
        for item in results:
            state_name = item.get("state", "")
            try:
                self.state_sync.from_plane_state(state_name)
            except ValueError:
                # 不在映射表中，无需对账
                continue
            candidates.append(item)

        logger.info(
            "对账过滤：项目 %s 共 %d 个工作项需要对账",
            project_id,
            len(candidates),
        )

        # 3. 批量并发执行，每批最多 5 个
        batch_size = 5
        total = len(candidates)
        corrected = 0

        for batch_start in range(0, total, batch_size):
            batch = candidates[batch_start : batch_start + batch_size]

            async def _reconcile_one(item: dict[str, Any]) -> bool:
                """对账单个工作项，返回是否执行了纠正（未抛异常即视为成功）。"""
                try:
                    await self.reconcile_item(
                        project_id=project_id,
                        work_item_id=item["id"],
                        current_state=item.get("state", ""),
                        custom_properties=item.get("custom_properties", {}),
                    )
                    return True
                except Exception:
                    logger.warning(
                        "对账单项失败：工作项 %s，跳过继续",
                        item.get("id"),
                        exc_info=True,
                    )
                    return False

            outcomes = await asyncio.gather(*[_reconcile_one(item) for item in batch])
            corrected += sum(outcomes)

        # 4. 记录汇总日志
        logger.info(
            "对账完成：项目 %s，候选 %d 项，成功处理 %d 项",
            project_id,
            total,
            corrected,
        )
        return {
            "status": "completed",
            "total_candidates": total,
            "processed": corrected,
        }
