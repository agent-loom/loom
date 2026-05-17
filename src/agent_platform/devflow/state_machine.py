"""DevFlow 强状态机 — 管理开发流程的状态转换与校验。

提供 DevFlowState 枚举、合法转换表、状态机类以及相关数据模型，
确保开发流水线中的状态变更严格遵循预定义规则。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class DevFlowState(StrEnum):
    """开发流水线状态枚举。"""

    INTAKE = "intake"  # 需求接收
    READY_FOR_AI_DEV = "ready_for_ai_dev"  # 等待 AI 开发
    AI_DEVELOPING = "ai_developing"  # AI 开发中
    AI_REVIEW = "ai_review"  # AI 自审中
    HUMAN_REVIEW = "human_review"  # 人工审核
    READY_FOR_MERGE = "ready_for_merge"  # 等待合并
    DONE = "done"  # 完成
    REJECTED = "rejected"  # 被拒绝


# ---------------------------------------------------------------------------
# 合法状态转换表
# ---------------------------------------------------------------------------

VALID_TRANSITIONS: dict[DevFlowState, set[DevFlowState]] = {
    DevFlowState.INTAKE: {DevFlowState.READY_FOR_AI_DEV, DevFlowState.REJECTED},
    DevFlowState.READY_FOR_AI_DEV: {DevFlowState.AI_DEVELOPING, DevFlowState.REJECTED},
    DevFlowState.AI_DEVELOPING: {DevFlowState.AI_REVIEW, DevFlowState.REJECTED},
    DevFlowState.AI_REVIEW: {
        DevFlowState.HUMAN_REVIEW,
        DevFlowState.AI_DEVELOPING,
        DevFlowState.REJECTED,
    },
    DevFlowState.HUMAN_REVIEW: {
        DevFlowState.READY_FOR_MERGE,
        DevFlowState.AI_DEVELOPING,
        DevFlowState.REJECTED,
    },
    DevFlowState.READY_FOR_MERGE: {DevFlowState.DONE, DevFlowState.REJECTED},
    DevFlowState.DONE: set(),  # 终态
    DevFlowState.REJECTED: {DevFlowState.INTAKE},  # 可重新评估
}


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


class DevFlowTransition(BaseModel):
    """一次状态转换的记录。"""

    from_state: DevFlowState
    to_state: DevFlowState
    actor: str = "system"
    reason: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# 自定义异常
# ---------------------------------------------------------------------------


class InvalidTransitionError(Exception):
    """非法状态转换异常。"""

    def __init__(
        self,
        from_state: DevFlowState,
        to_state: DevFlowState,
        message: str = "",
    ) -> None:
        self.from_state = from_state
        self.to_state = to_state
        if not message:
            message = (
                f"非法状态转换: {from_state.value} -> {to_state.value}。"
                f"允许的目标状态: "
                f"{sorted(s.value for s in VALID_TRANSITIONS.get(from_state, set()))}"
            )
        self.message = message
        super().__init__(self.message)


# ---------------------------------------------------------------------------
# 状态机
# ---------------------------------------------------------------------------


class DevFlowStateMachine:
    """DevFlow 开发流程状态机。

    管理单个工作项从 INTAKE 到 DONE/REJECTED 的完整生命周期，
    严格校验每一步状态转换是否合法，并记录完整的转换历史。
    """

    def __init__(
        self,
        initial_state: DevFlowState = DevFlowState.INTAKE,
    ) -> None:
        self._current_state = initial_state
        self._history: list[DevFlowTransition] = []

    @property
    def current_state(self) -> DevFlowState:
        """当前状态（只读）。"""
        return self._current_state

    @property
    def history(self) -> list[DevFlowTransition]:
        """状态转换历史（只读副本）。"""
        return list(self._history)

    def can_transition(self, to_state: DevFlowState) -> bool:
        """判断是否可以从当前状态转换到目标状态。"""
        return to_state in VALID_TRANSITIONS.get(self._current_state, set())

    def available_transitions(self) -> set[DevFlowState]:
        """返回当前状态允许转换到的所有目标状态。"""
        return set(VALID_TRANSITIONS.get(self._current_state, set()))

    def transition(
        self,
        to_state: DevFlowState,
        *,
        actor: str = "system",
        reason: str = "",
    ) -> DevFlowTransition:
        """执行状态转换。

        :param to_state: 目标状态。
        :param actor: 执行转换的操作者。
        :param reason: 转换原因。
        :returns: 转换记录。
        :raises InvalidTransitionError: 如果转换不合法。
        """
        if not self.can_transition(to_state):
            raise InvalidTransitionError(self._current_state, to_state)

        record = DevFlowTransition(
            from_state=self._current_state,
            to_state=to_state,
            actor=actor,
            reason=reason,
        )
        self._current_state = to_state
        self._history.append(record)
        return record
