"""DevFlow 状态同步服务 — 在本地状态机与 Plane 之间同步工作项状态。

提供双向状态映射、外部事件处理、以及 Plane 更新失败时的回滚机制。
"""

from __future__ import annotations

import logging
from typing import Any

from agent_platform.devflow.state_machine import (
    DevFlowState,
    DevFlowStateMachine,
    DevFlowTransition,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 状态映射表：Plane 状态名 <-> DevFlowState
# ---------------------------------------------------------------------------

PLANE_STATE_MAP: dict[str, DevFlowState] = {
    "Intake": DevFlowState.INTAKE,
    "intake": DevFlowState.INTAKE,
    "Ready for AI Dev": DevFlowState.READY_FOR_AI_DEV,
    "ready_for_ai_dev": DevFlowState.READY_FOR_AI_DEV,
    "AI Developing": DevFlowState.AI_DEVELOPING,
    "ai_developing": DevFlowState.AI_DEVELOPING,
    "AI Review": DevFlowState.AI_REVIEW,
    "ai_review": DevFlowState.AI_REVIEW,
    "Human Review": DevFlowState.HUMAN_REVIEW,
    "human_review": DevFlowState.HUMAN_REVIEW,
    "Ready for Merge": DevFlowState.READY_FOR_MERGE,
    "ready_for_merge": DevFlowState.READY_FOR_MERGE,
    "Done": DevFlowState.DONE,
    "done": DevFlowState.DONE,
    "Rejected": DevFlowState.REJECTED,
    "rejected": DevFlowState.REJECTED,
}

DEVFLOW_STATE_MAP: dict[DevFlowState, str] = {
    DevFlowState.INTAKE: "Intake",
    DevFlowState.READY_FOR_AI_DEV: "Ready for AI Dev",
    DevFlowState.AI_DEVELOPING: "AI Developing",
    DevFlowState.AI_REVIEW: "AI Review",
    DevFlowState.HUMAN_REVIEW: "Human Review",
    DevFlowState.READY_FOR_MERGE: "Ready for Merge",
    DevFlowState.DONE: "Done",
    DevFlowState.REJECTED: "Rejected",
}


# ---------------------------------------------------------------------------
# 状态同步服务
# ---------------------------------------------------------------------------


class DevFlowStateSync:
    """DevFlow 状态同步服务。

    负责管理工作项的本地状态机实例，并与 Plane 系统保持状态同步。
    支持本地 → Plane 的推送同步，以及 Plane → 本地的 Webhook 事件处理。
    """

    def __init__(
        self,
        plane_adapter: Any | None = None,
        state_machines: dict[str, DevFlowStateMachine] | None = None,
    ) -> None:
        self.plane_adapter = plane_adapter
        self._state_machines: dict[str, DevFlowStateMachine] = state_machines or {}

    def get_or_create(
        self,
        work_item_id: str,
        initial_state: DevFlowState = DevFlowState.INTAKE,
    ) -> DevFlowStateMachine:
        """获取已有的状态机实例，或为新工作项创建一个。"""
        if work_item_id not in self._state_machines:
            self._state_machines[work_item_id] = DevFlowStateMachine(initial_state)
        return self._state_machines[work_item_id]

    @staticmethod
    def from_plane_state(plane_state: str) -> DevFlowState:
        """将 Plane 状态名映射为 DevFlowState。

        :param plane_state: Plane 系统中的状态名称。
        :returns: 对应的 DevFlowState。
        :raises ValueError: 无法映射的状态名。
        """
        state = PLANE_STATE_MAP.get(plane_state)
        if state is None:
            raise ValueError(f"无法映射的 Plane 状态: {plane_state!r}")
        return state

    @staticmethod
    def to_plane_state(state: DevFlowState) -> str:
        """将 DevFlowState 映射为 Plane 状态名。

        :param state: DevFlowState 枚举值。
        :returns: Plane 系统中对应的状态名称。
        :raises ValueError: 无法映射的 DevFlowState。
        """
        plane_name = DEVFLOW_STATE_MAP.get(state)
        if plane_name is None:
            raise ValueError(f"无法映射的 DevFlowState: {state!r}")
        return plane_name

    async def sync_to_plane(
        self,
        work_item_id: str,
        project_id: str,
        new_state: DevFlowState,
        *,
        actor: str = "system",
        reason: str = "",
    ) -> None:
        """同步状态到 Plane。

        先在本地状态机执行转换以校验合法性，然后更新 Plane 工作项状态。
        如果 Plane 更新失败，回滚本地状态。

        :param work_item_id: 工作项 ID。
        :param project_id: Plane 项目 ID。
        :param new_state: 目标状态。
        :param actor: 操作者。
        :param reason: 转换原因。
        """
        sm = self.get_or_create(work_item_id)
        old_state = sm.current_state

        # 本地转换（校验合法性）
        sm.transition(new_state, actor=actor, reason=reason)

        # 更新 Plane
        if self.plane_adapter is not None:
            plane_state_name = self.to_plane_state(new_state)
            try:
                await self.plane_adapter.update_work_item_state(
                    project_id, work_item_id, plane_state_name,
                )
                logger.info(
                    "已同步工作项 %s 状态到 Plane: %s -> %s",
                    work_item_id,
                    old_state.value,
                    new_state.value,
                )
            except Exception:
                # Plane 更新失败，回滚本地状态
                logger.warning(
                    "Plane 更新失败，回滚工作项 %s 状态: %s -> %s",
                    work_item_id,
                    new_state.value,
                    old_state.value,
                )
                sm._current_state = old_state  # noqa: SLF001
                # 移除最后一条转换记录
                if sm._history:  # noqa: SLF001
                    sm._history.pop()  # noqa: SLF001
                raise

    async def handle_external_transition(
        self,
        work_item_id: str,
        new_plane_state: str,
        actor: str = "plane_webhook",
    ) -> DevFlowTransition:
        """处理来自 Plane Webhook 的外部状态变更。

        将 Plane 状态名映射为 DevFlowState，然后在本地状态机执行转换。

        :param work_item_id: 工作项 ID。
        :param new_plane_state: Plane 中新的状态名称。
        :param actor: 操作者标识，默认为 "plane_webhook"。
        :returns: 转换记录。
        :raises ValueError: 无法映射的 Plane 状态。
        :raises InvalidTransitionError: 非法转换。
        """
        devflow_state = self.from_plane_state(new_plane_state)
        sm = self.get_or_create(work_item_id)
        return sm.transition(
            devflow_state,
            actor=actor,
            reason=f"Plane 外部事件: {new_plane_state}",
        )

    @property
    def tracked_items(self) -> dict[str, DevFlowStateMachine]:
        """当前正在跟踪的所有工作项状态机（只读）。"""
        return dict(self._state_machines)
