"""Plane 项目引导脚本 — 自动发现或创建标准 DevFlow 状态。

用法:
  PlaneBootstrap(plane_adapter).bootstrap(project_id)

幂等设计：重复执行不会创建重复状态。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from agent_platform.devflow.state_machine import DevFlowState
from agent_platform.devflow.state_sync import DEVFLOW_STATE_MAP
from agent_platform.integrations.plane.adapter import PlaneAdapter

logger = logging.getLogger(__name__)

DEVFLOW_STATE_DISPLAY_ORDER = [
    DevFlowState.INTAKE,
    DevFlowState.READY_FOR_AI_DEV,
    DevFlowState.AI_DEVELOPING,
    DevFlowState.AI_REVIEW,
    DevFlowState.HUMAN_REVIEW,
    DevFlowState.READY_FOR_MERGE,
    DevFlowState.DONE,
    DevFlowState.REJECTED,
]


@dataclass
class BootstrapResult:
    """Bootstrap 执行结果。"""

    project_id: str
    state_map: dict[str, str]
    created: list[str]
    existing: list[str]


class PlaneBootstrap:
    """自动为 Plane 项目配置 DevFlow 所需的 8 个标准状态。"""

    def __init__(self, plane: PlaneAdapter) -> None:
        self.plane = plane

    async def discover_state_map(
        self, project_id: str,
    ) -> dict[str, str]:
        """查询 Plane 项目现有状态，返回 {state_name: state_id} 映射。"""
        states = await self.plane.list_states(project_id)
        return {s["name"]: s["id"] for s in states if "name" in s and "id" in s}

    async def bootstrap(self, project_id: str) -> BootstrapResult:
        """确保项目包含所有 DevFlow 标准状态，缺失的自动报告（需手动创建）。

        Plane REST API 不一定支持 POST 创建 state（取决于版本），
        因此本方法仅做发现和报告，不强制创建。
        """
        existing_map = await self.discover_state_map(project_id)
        result_map: dict[str, str] = {}
        created: list[str] = []
        existing: list[str] = []

        for state in DEVFLOW_STATE_DISPLAY_ORDER:
            display_name = DEVFLOW_STATE_MAP[state]
            if display_name in existing_map:
                result_map[display_name] = existing_map[display_name]
                existing.append(display_name)
                logger.info("状态已存在: %s → %s", display_name, existing_map[display_name])
            else:
                logger.warning(
                    "状态缺失: %s — 请在 Plane 项目 %s 中手动创建",
                    display_name, project_id,
                )

        return BootstrapResult(
            project_id=project_id,
            state_map=result_map,
            created=created,
            existing=existing,
        )

    async def resolve_state_ids(
        self, project_id: str,
    ) -> dict[str, str | None]:
        """返回 DevFlow 各环节对应的 Plane state_id 映射。

        返回格式: {"ai_developing": "uuid-...", "testing": "uuid-...", ...}
        未找到的键值为 None。
        """
        existing_map = await self.discover_state_map(project_id)

        key_mapping = {
            "ai_developing": "AI Developing",
            "testing": "AI Review",
            "human_review": "Human Review",
            "staging": "Ready for Merge",
            "done": "Done",
        }

        return {
            key: existing_map.get(display_name)
            for key, display_name in key_mapping.items()
        }
