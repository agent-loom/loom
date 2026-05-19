"""DevFlowReconciler.run_reconciliation() 单元测试。

覆盖：
1. 无工作项时正常完成
2. 有多个工作项时都被对账
3. 单项失败不影响其他项
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_platform.devflow.reconcile import DevFlowReconciler
from agent_platform.devflow.state_sync import DevFlowStateSync


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------


def _make_reconciler(
    list_work_items_return: dict | None = None,
    reconcile_item_side_effect=None,
) -> DevFlowReconciler:
    """创建带有 Mock 依赖的 DevFlowReconciler 实例。"""
    state_sync = DevFlowStateSync()

    plane = MagicMock()
    plane.list_work_items = AsyncMock(
        return_value=list_work_items_return or {"results": []}
    )

    gitlab = MagicMock()

    reconciler = DevFlowReconciler(
        state_sync=state_sync,
        plane=plane,
        gitlab=gitlab,
        gitlab_project_id="gl-proj-1",
    )

    if reconcile_item_side_effect is not None:
        reconciler.reconcile_item = AsyncMock(side_effect=reconcile_item_side_effect)
    else:
        reconciler.reconcile_item = AsyncMock(return_value=None)

    return reconciler


# ---------------------------------------------------------------------------
# 测试用数据
# ---------------------------------------------------------------------------

# DevFlow 关心的合法状态（from_plane_state 不会抛 ValueError）
VALID_WORK_ITEMS = [
    {
        "id": "wi-1",
        "state": "AI Developing",
        "custom_properties": {"gitlab_mr_iid": "10"},
    },
    {
        "id": "wi-2",
        "state": "Human Review",
        "custom_properties": {"gitlab_mr_iid": "20"},
    },
    {
        "id": "wi-3",
        "state": "AI Review",
        "custom_properties": {"gitlab_mr_iid": "30"},
    },
]

# 不在映射表中的状态（应被过滤掉）
UNKNOWN_STATE_ITEM = {
    "id": "wi-unknown",
    "state": "SomeRandomState",
    "custom_properties": {},
}


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------


class TestRunReconciliationNoItems:
    """无工作项时 run_reconciliation 应正常完成。"""

    @pytest.mark.asyncio
    async def test_empty_results_completes_without_error(self) -> None:
        """返回空 results 时不应抛异常，reconcile_item 不被调用。"""
        reconciler = _make_reconciler(list_work_items_return={"results": []})

        await reconciler.run_reconciliation("project-123")

        reconciler.reconcile_item.assert_not_called()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_missing_results_key_completes_without_error(self) -> None:
        """返回字典中没有 results 键时，应视为空列表，正常完成。"""
        reconciler = _make_reconciler(list_work_items_return={})

        await reconciler.run_reconciliation("project-xyz")

        reconciler.reconcile_item.assert_not_called()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_only_unknown_states_are_filtered(self) -> None:
        """只有不在映射表中的状态时，全部被过滤，reconcile_item 不被调用。"""
        reconciler = _make_reconciler(
            list_work_items_return={"results": [UNKNOWN_STATE_ITEM]}
        )

        await reconciler.run_reconciliation("project-456")

        reconciler.reconcile_item.assert_not_called()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_plane_api_error_does_not_raise(self) -> None:
        """Plane API 调用失败时，run_reconciliation 应捕获异常并正常返回。"""
        state_sync = DevFlowStateSync()
        plane = MagicMock()
        plane.list_work_items = AsyncMock(side_effect=RuntimeError("网络超时"))
        gitlab = MagicMock()

        reconciler = DevFlowReconciler(
            state_sync=state_sync,
            plane=plane,
            gitlab=gitlab,
            gitlab_project_id="gl-proj-1",
        )
        reconciler.reconcile_item = AsyncMock(return_value=None)

        # 不应抛出异常
        await reconciler.run_reconciliation("project-err")


class TestRunReconciliationMultipleItems:
    """有多个工作项时，所有符合条件的项都被对账。"""

    @pytest.mark.asyncio
    async def test_all_valid_items_reconciled(self) -> None:
        """三个合法状态的工作项，reconcile_item 均被调用一次。"""
        reconciler = _make_reconciler(
            list_work_items_return={"results": VALID_WORK_ITEMS}
        )

        await reconciler.run_reconciliation("project-multi")

        assert reconciler.reconcile_item.call_count == 3  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_unknown_state_items_excluded(self) -> None:
        """混合列表中，不合法状态的工作项被过滤，只对账合法的。"""
        mixed = VALID_WORK_ITEMS + [UNKNOWN_STATE_ITEM]
        reconciler = _make_reconciler(list_work_items_return={"results": mixed})

        await reconciler.run_reconciliation("project-mixed")

        # 只有 3 个合法工作项被对账
        assert reconciler.reconcile_item.call_count == 3  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_reconcile_item_called_with_correct_args(self) -> None:
        """reconcile_item 以正确参数被调用。"""
        single_item = [VALID_WORK_ITEMS[0]]
        reconciler = _make_reconciler(
            list_work_items_return={"results": single_item}
        )

        await reconciler.run_reconciliation("proj-arg-check")

        reconciler.reconcile_item.assert_called_once_with(  # type: ignore[attr-defined]
            project_id="proj-arg-check",
            work_item_id="wi-1",
            current_state="AI Developing",
            custom_properties={"gitlab_mr_iid": "10"},
        )

    @pytest.mark.asyncio
    async def test_batching_more_than_five_items(self) -> None:
        """超过 5 个工作项时，仍然全部被处理。"""
        items = [
            {
                "id": f"wi-{i}",
                "state": "AI Developing",
                "custom_properties": {},
            }
            for i in range(8)
        ]
        reconciler = _make_reconciler(list_work_items_return={"results": items})

        await reconciler.run_reconciliation("project-batch")

        assert reconciler.reconcile_item.call_count == 8  # type: ignore[attr-defined]


class TestRunReconciliationSingleItemFailure:
    """单项失败不影响其他项的对账。"""

    @pytest.mark.asyncio
    async def test_one_failure_does_not_stop_others(self) -> None:
        """第一个工作项抛异常后，其余两个仍然被对账。"""
        call_count = 0

        async def side_effect(**kwargs):  # noqa: ANN001, ANN201
            nonlocal call_count
            call_count += 1
            if kwargs.get("work_item_id") == "wi-1":
                raise RuntimeError("模拟 GitLab 超时")
            return None

        reconciler = _make_reconciler(
            list_work_items_return={"results": VALID_WORK_ITEMS},
            reconcile_item_side_effect=side_effect,
        )

        # 不应向外抛出异常
        await reconciler.run_reconciliation("project-fail")

        # 三个工作项都被尝试调用
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_all_failures_still_no_exception(self) -> None:
        """所有工作项都失败时，run_reconciliation 应仍然正常返回。"""

        async def always_fail(**kwargs):  # noqa: ANN001, ANN201
            raise RuntimeError("全部失败")

        reconciler = _make_reconciler(
            list_work_items_return={"results": VALID_WORK_ITEMS},
            reconcile_item_side_effect=always_fail,
        )

        # 不应抛出异常
        await reconciler.run_reconciliation("project-all-fail")

    @pytest.mark.asyncio
    async def test_partial_failures_others_succeed(self) -> None:
        """部分失败时，成功的项正常完成，失败的项不影响整体流程。"""
        succeeded: list[str] = []

        async def side_effect(**kwargs):  # noqa: ANN001, ANN201
            wid = kwargs.get("work_item_id", "")
            if wid == "wi-2":
                raise ValueError("状态机非法转换")
            succeeded.append(wid)
            return None

        reconciler = _make_reconciler(
            list_work_items_return={"results": VALID_WORK_ITEMS},
            reconcile_item_side_effect=side_effect,
        )

        await reconciler.run_reconciliation("project-partial")

        # wi-1 和 wi-3 成功，wi-2 失败被跳过
        assert "wi-1" in succeeded
        assert "wi-3" in succeeded
        assert "wi-2" not in succeeded
