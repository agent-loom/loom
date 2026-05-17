"""DevFlow 状态机单元测试。

覆盖所有合法/非法转换路径、历史记录、终态行为以及完整生命周期。
"""

from __future__ import annotations

import pytest

from agent_platform.devflow.state_machine import (
    VALID_TRANSITIONS,
    DevFlowState,
    DevFlowStateMachine,
    DevFlowTransition,
    InvalidTransitionError,
)


class TestDevFlowState:
    """DevFlowState 枚举测试。"""

    def test_state_values(self) -> None:
        """所有状态枚举值正确。"""
        assert DevFlowState.INTAKE == "intake"
        assert DevFlowState.READY_FOR_AI_DEV == "ready_for_ai_dev"
        assert DevFlowState.AI_DEVELOPING == "ai_developing"
        assert DevFlowState.AI_REVIEW == "ai_review"
        assert DevFlowState.HUMAN_REVIEW == "human_review"
        assert DevFlowState.READY_FOR_MERGE == "ready_for_merge"
        assert DevFlowState.DONE == "done"
        assert DevFlowState.REJECTED == "rejected"

    def test_all_states_in_transitions_table(self) -> None:
        """每个状态都必须在转换表中有条目。"""
        for state in DevFlowState:
            assert state in VALID_TRANSITIONS


class TestDevFlowStateMachine:
    """DevFlowStateMachine 核心行为测试。"""

    def test_initial_state_is_intake(self) -> None:
        """默认初始状态为 INTAKE。"""
        sm = DevFlowStateMachine()
        assert sm.current_state == DevFlowState.INTAKE

    def test_custom_initial_state(self) -> None:
        """支持自定义初始状态。"""
        sm = DevFlowStateMachine(initial_state=DevFlowState.AI_DEVELOPING)
        assert sm.current_state == DevFlowState.AI_DEVELOPING

    def test_intake_to_ready_for_ai_dev(self) -> None:
        """INTAKE → READY_FOR_AI_DEV 是合法转换。"""
        sm = DevFlowStateMachine()
        record = sm.transition(DevFlowState.READY_FOR_AI_DEV)
        assert sm.current_state == DevFlowState.READY_FOR_AI_DEV
        assert record.from_state == DevFlowState.INTAKE
        assert record.to_state == DevFlowState.READY_FOR_AI_DEV

    def test_intake_to_rejected(self) -> None:
        """INTAKE → REJECTED 是合法转换。"""
        sm = DevFlowStateMachine()
        sm.transition(DevFlowState.REJECTED, actor="reviewer", reason="需求不清晰")
        assert sm.current_state == DevFlowState.REJECTED

    def test_ready_for_ai_dev_to_ai_developing(self) -> None:
        """READY_FOR_AI_DEV → AI_DEVELOPING 合法。"""
        sm = DevFlowStateMachine(initial_state=DevFlowState.READY_FOR_AI_DEV)
        sm.transition(DevFlowState.AI_DEVELOPING)
        assert sm.current_state == DevFlowState.AI_DEVELOPING

    def test_ai_developing_to_ai_review(self) -> None:
        """AI_DEVELOPING → AI_REVIEW 合法（开发完成后自审）。"""
        sm = DevFlowStateMachine(initial_state=DevFlowState.AI_DEVELOPING)
        sm.transition(DevFlowState.AI_REVIEW)
        assert sm.current_state == DevFlowState.AI_REVIEW

    def test_ai_review_to_human_review(self) -> None:
        """AI_REVIEW → HUMAN_REVIEW 合法（自审通过进入人审）。"""
        sm = DevFlowStateMachine(initial_state=DevFlowState.AI_REVIEW)
        sm.transition(DevFlowState.HUMAN_REVIEW)
        assert sm.current_state == DevFlowState.HUMAN_REVIEW

    def test_ai_review_to_ai_developing_rework(self) -> None:
        """AI_REVIEW → AI_DEVELOPING 合法（自审不通过，重新开发）。"""
        sm = DevFlowStateMachine(initial_state=DevFlowState.AI_REVIEW)
        sm.transition(DevFlowState.AI_DEVELOPING, reason="自审发现问题，需返工")
        assert sm.current_state == DevFlowState.AI_DEVELOPING

    def test_human_review_to_ready_for_merge(self) -> None:
        """HUMAN_REVIEW → READY_FOR_MERGE 合法（人审通过）。"""
        sm = DevFlowStateMachine(initial_state=DevFlowState.HUMAN_REVIEW)
        sm.transition(DevFlowState.READY_FOR_MERGE)
        assert sm.current_state == DevFlowState.READY_FOR_MERGE

    def test_human_review_to_ai_developing_rework(self) -> None:
        """HUMAN_REVIEW → AI_DEVELOPING 合法（人审不通过，需返工）。"""
        sm = DevFlowStateMachine(initial_state=DevFlowState.HUMAN_REVIEW)
        sm.transition(DevFlowState.AI_DEVELOPING, actor="reviewer", reason="需修复测试")
        assert sm.current_state == DevFlowState.AI_DEVELOPING

    def test_ready_for_merge_to_done(self) -> None:
        """READY_FOR_MERGE → DONE 合法。"""
        sm = DevFlowStateMachine(initial_state=DevFlowState.READY_FOR_MERGE)
        sm.transition(DevFlowState.DONE)
        assert sm.current_state == DevFlowState.DONE

    def test_rejected_to_intake(self) -> None:
        """REJECTED → INTAKE 合法（可重新评估）。"""
        sm = DevFlowStateMachine(initial_state=DevFlowState.REJECTED)
        sm.transition(DevFlowState.INTAKE, reason="重新评估需求")
        assert sm.current_state == DevFlowState.INTAKE

    def test_done_is_terminal(self) -> None:
        """DONE 是终态，无法转换到任何其他状态。"""
        sm = DevFlowStateMachine(initial_state=DevFlowState.DONE)
        assert sm.available_transitions() == set()
        with pytest.raises(InvalidTransitionError):
            sm.transition(DevFlowState.INTAKE)

    def test_invalid_transition_raises_error(self) -> None:
        """非法转换抛出 InvalidTransitionError。"""
        sm = DevFlowStateMachine()
        with pytest.raises(InvalidTransitionError) as exc_info:
            sm.transition(DevFlowState.DONE)

        err = exc_info.value
        assert err.from_state == DevFlowState.INTAKE
        assert err.to_state == DevFlowState.DONE
        assert "非法状态转换" in err.message

    def test_invalid_transition_preserves_state(self) -> None:
        """非法转换不改变当前状态。"""
        sm = DevFlowStateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.transition(DevFlowState.HUMAN_REVIEW)
        assert sm.current_state == DevFlowState.INTAKE

    def test_can_transition_true(self) -> None:
        """can_transition 返回 True 对合法转换。"""
        sm = DevFlowStateMachine()
        assert sm.can_transition(DevFlowState.READY_FOR_AI_DEV) is True
        assert sm.can_transition(DevFlowState.REJECTED) is True

    def test_can_transition_false(self) -> None:
        """can_transition 返回 False 对非法转换。"""
        sm = DevFlowStateMachine()
        assert sm.can_transition(DevFlowState.DONE) is False
        assert sm.can_transition(DevFlowState.AI_REVIEW) is False

    def test_available_transitions(self) -> None:
        """available_transitions 返回当前可用目标状态集合。"""
        sm = DevFlowStateMachine()
        expected = {DevFlowState.READY_FOR_AI_DEV, DevFlowState.REJECTED}
        assert sm.available_transitions() == expected

    def test_transition_history_is_recorded(self) -> None:
        """转换历史记录完整。"""
        sm = DevFlowStateMachine()
        sm.transition(DevFlowState.READY_FOR_AI_DEV, actor="pm", reason="已审核需求")
        sm.transition(DevFlowState.AI_DEVELOPING, actor="system")

        assert len(sm.history) == 2

        h0 = sm.history[0]
        assert h0.from_state == DevFlowState.INTAKE
        assert h0.to_state == DevFlowState.READY_FOR_AI_DEV
        assert h0.actor == "pm"
        assert h0.reason == "已审核需求"
        assert h0.timestamp is not None

        h1 = sm.history[1]
        assert h1.from_state == DevFlowState.READY_FOR_AI_DEV
        assert h1.to_state == DevFlowState.AI_DEVELOPING

    def test_history_is_readonly_copy(self) -> None:
        """history 属性返回的是副本，修改不影响内部状态。"""
        sm = DevFlowStateMachine()
        sm.transition(DevFlowState.READY_FOR_AI_DEV)
        history = sm.history
        history.clear()
        assert len(sm.history) == 1  # 内部历史不受影响

    def test_transition_returns_pydantic_model(self) -> None:
        """transition 返回 DevFlowTransition Pydantic 模型。"""
        sm = DevFlowStateMachine()
        record = sm.transition(DevFlowState.READY_FOR_AI_DEV, actor="bot")
        assert isinstance(record, DevFlowTransition)
        # 可序列化
        data = record.model_dump(mode="json")
        assert data["from_state"] == "intake"
        assert data["to_state"] == "ready_for_ai_dev"
        assert data["actor"] == "bot"

    def test_full_lifecycle_intake_to_done(self) -> None:
        """完整生命周期测试: INTAKE → ... → DONE。"""
        sm = DevFlowStateMachine()

        # INTAKE → READY_FOR_AI_DEV
        sm.transition(DevFlowState.READY_FOR_AI_DEV, actor="pm")
        # READY_FOR_AI_DEV → AI_DEVELOPING
        sm.transition(DevFlowState.AI_DEVELOPING, actor="system")
        # AI_DEVELOPING → AI_REVIEW
        sm.transition(DevFlowState.AI_REVIEW, actor="ai_runner")
        # AI_REVIEW → HUMAN_REVIEW
        sm.transition(DevFlowState.HUMAN_REVIEW, actor="ai_reviewer")
        # HUMAN_REVIEW → READY_FOR_MERGE
        sm.transition(DevFlowState.READY_FOR_MERGE, actor="reviewer")
        # READY_FOR_MERGE → DONE
        sm.transition(DevFlowState.DONE, actor="ci")

        assert sm.current_state == DevFlowState.DONE
        assert len(sm.history) == 6

    def test_lifecycle_with_rework(self) -> None:
        """含返工的生命周期: AI_REVIEW → AI_DEVELOPING → AI_REVIEW → HUMAN_REVIEW。"""
        sm = DevFlowStateMachine()
        sm.transition(DevFlowState.READY_FOR_AI_DEV)
        sm.transition(DevFlowState.AI_DEVELOPING)
        sm.transition(DevFlowState.AI_REVIEW)

        # 自审不通过，返工
        sm.transition(DevFlowState.AI_DEVELOPING, reason="测试失败")
        sm.transition(DevFlowState.AI_REVIEW)
        # 自审通过
        sm.transition(DevFlowState.HUMAN_REVIEW)
        sm.transition(DevFlowState.READY_FOR_MERGE)
        sm.transition(DevFlowState.DONE)

        assert sm.current_state == DevFlowState.DONE
        assert len(sm.history) == 8

    def test_rejected_and_retry(self) -> None:
        """REJECTED 后可重回 INTAKE 并重新走完整流程。"""
        sm = DevFlowStateMachine()
        sm.transition(DevFlowState.REJECTED, reason="方案有风险")
        assert sm.current_state == DevFlowState.REJECTED

        sm.transition(DevFlowState.INTAKE, reason="重新评估")
        assert sm.current_state == DevFlowState.INTAKE

        sm.transition(DevFlowState.READY_FOR_AI_DEV)
        assert sm.current_state == DevFlowState.READY_FOR_AI_DEV

    def test_all_reject_transitions(self) -> None:
        """除 DONE 外所有状态都可以转换到 REJECTED。"""
        rejectable_states = [
            DevFlowState.INTAKE,
            DevFlowState.READY_FOR_AI_DEV,
            DevFlowState.AI_DEVELOPING,
            DevFlowState.AI_REVIEW,
            DevFlowState.HUMAN_REVIEW,
            DevFlowState.READY_FOR_MERGE,
        ]
        for state in rejectable_states:
            sm = DevFlowStateMachine(initial_state=state)
            sm.transition(DevFlowState.REJECTED)
            assert sm.current_state == DevFlowState.REJECTED
