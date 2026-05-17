"""DevFlow 状态同步服务单元测试。

覆盖状态映射、get_or_create、外部事件处理、sync_to_plane 成功/失败回滚等场景。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agent_platform.devflow.state_machine import (
    DevFlowState,
    InvalidTransitionError,
)
from agent_platform.devflow.state_sync import (
    DEVFLOW_STATE_MAP,
    PLANE_STATE_MAP,
    DevFlowStateSync,
)


class TestStateMaps:
    """Plane ↔ DevFlowState 双向映射测试。"""

    def test_plane_state_map_covers_all_states(self) -> None:
        """PLANE_STATE_MAP 至少覆盖了所有 DevFlowState 的小写形式。"""
        for state in DevFlowState:
            assert state.value in PLANE_STATE_MAP
            assert PLANE_STATE_MAP[state.value] == state

    def test_devflow_state_map_covers_all_states(self) -> None:
        """DEVFLOW_STATE_MAP 覆盖了所有 DevFlowState。"""
        for state in DevFlowState:
            assert state in DEVFLOW_STATE_MAP

    def test_roundtrip_mapping(self) -> None:
        """从 DevFlowState → Plane name → DevFlowState 的往返映射一致。"""
        for state in DevFlowState:
            plane_name = DEVFLOW_STATE_MAP[state]
            assert PLANE_STATE_MAP[plane_name] == state


class TestDevFlowStateSync:
    """DevFlowStateSync 服务测试。"""

    def test_get_or_create_creates_new(self) -> None:
        """get_or_create 为新工作项创建状态机。"""
        sync = DevFlowStateSync()
        sm = sync.get_or_create("wi-1")
        assert sm.current_state == DevFlowState.INTAKE
        assert "wi-1" in sync.tracked_items

    def test_get_or_create_reuses_existing(self) -> None:
        """get_or_create 复用已有状态机。"""
        sync = DevFlowStateSync()
        sm1 = sync.get_or_create("wi-1")
        sm1.transition(DevFlowState.READY_FOR_AI_DEV)

        sm2 = sync.get_or_create("wi-1")
        assert sm2 is sm1
        assert sm2.current_state == DevFlowState.READY_FOR_AI_DEV

    def test_get_or_create_custom_initial_state(self) -> None:
        """get_or_create 支持自定义初始状态。"""
        sync = DevFlowStateSync()
        sm = sync.get_or_create("wi-2", initial_state=DevFlowState.AI_DEVELOPING)
        assert sm.current_state == DevFlowState.AI_DEVELOPING

    def test_from_plane_state_valid(self) -> None:
        """from_plane_state 正确映射合法 Plane 状态名。"""
        result = DevFlowStateSync.from_plane_state("Ready for AI Dev")
        assert result == DevFlowState.READY_FOR_AI_DEV
        assert DevFlowStateSync.from_plane_state("done") == DevFlowState.DONE

    def test_from_plane_state_invalid(self) -> None:
        """from_plane_state 对不存在的状态名抛出 ValueError。"""
        with pytest.raises(ValueError, match="无法映射"):
            DevFlowStateSync.from_plane_state("Unknown State")

    def test_to_plane_state(self) -> None:
        """to_plane_state 正确映射 DevFlowState 到 Plane 名称。"""
        assert DevFlowStateSync.to_plane_state(DevFlowState.AI_REVIEW) == "AI Review"
        assert DevFlowStateSync.to_plane_state(DevFlowState.DONE) == "Done"

    @pytest.mark.asyncio
    async def test_handle_external_transition_valid(self) -> None:
        """handle_external_transition 对合法外部事件成功转换。"""
        sync = DevFlowStateSync()
        sync.get_or_create("wi-10")  # INTAKE

        record = await sync.handle_external_transition(
            "wi-10", "Ready for AI Dev", actor="webhook",
        )
        assert record.to_state == DevFlowState.READY_FOR_AI_DEV
        assert sync.tracked_items["wi-10"]["current_state"] == DevFlowState.READY_FOR_AI_DEV.value

    @pytest.mark.asyncio
    async def test_handle_external_transition_invalid(self) -> None:
        """handle_external_transition 对非法转换抛出 InvalidTransitionError。"""
        sync = DevFlowStateSync()
        sync.get_or_create("wi-11")  # INTAKE

        with pytest.raises(InvalidTransitionError):
            await sync.handle_external_transition("wi-11", "Done")

    @pytest.mark.asyncio
    async def test_sync_to_plane_success(self) -> None:
        """sync_to_plane 成功同步到 Plane。"""
        mock_plane = AsyncMock()
        mock_plane.update_work_item_state = AsyncMock()
        sync = DevFlowStateSync(plane_adapter=mock_plane)
        sync.get_or_create("wi-20")  # INTAKE

        await sync.sync_to_plane("wi-20", "proj-1", DevFlowState.READY_FOR_AI_DEV)

        assert sync.tracked_items["wi-20"]["current_state"] == DevFlowState.READY_FOR_AI_DEV.value
        mock_plane.update_work_item_state.assert_called_once_with(
            "proj-1", "wi-20", "Ready for AI Dev",
        )

    @pytest.mark.asyncio
    async def test_sync_to_plane_rollback_on_failure(self) -> None:
        """sync_to_plane 在 Plane 更新失败时回滚本地状态。"""
        mock_plane = AsyncMock()
        mock_plane.update_work_item_state = AsyncMock(side_effect=RuntimeError("网络错误"))
        sync = DevFlowStateSync(plane_adapter=mock_plane)
        sync.get_or_create("wi-21")  # INTAKE

        with pytest.raises(RuntimeError, match="网络错误"):
            await sync.sync_to_plane("wi-21", "proj-1", DevFlowState.READY_FOR_AI_DEV)

        # 状态应已回滚
        assert sync.tracked_items["wi-21"]["current_state"] == DevFlowState.INTAKE.value
        # 历史中不应有该转换记录
        assert sync.tracked_items["wi-21"]["history_count"] == 0

    @pytest.mark.asyncio
    async def test_sync_to_plane_without_adapter(self) -> None:
        """sync_to_plane 在无 Plane adapter 时仅执行本地转换。"""
        sync = DevFlowStateSync(plane_adapter=None)
        sync.get_or_create("wi-22")

        await sync.sync_to_plane("wi-22", "proj-1", DevFlowState.READY_FOR_AI_DEV)
        assert sync.tracked_items["wi-22"]["current_state"] == DevFlowState.READY_FOR_AI_DEV.value
