from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]]


class ToolDefinition(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    timeout_ms: int = 3000
    permissions: list[str] = Field(default_factory=list)
    handler: ToolHandler

    model_config = {"arbitrary_types_allowed": True}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        self._tools[definition.name] = definition

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise LookupError(f"tool not found: {name}") from exc

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())


def create_default_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="myj.goods_search",
            description="MYJ demo product search and recommendation tool",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            permissions=["knowledge:read", "product:read"],
            handler=_myj_goods_search,
        )
    )
    registry.register(
        ToolDefinition(
            name="myj.goods_location",
            description="MYJ demo product location lookup tool",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            permissions=["knowledge:read", "store:read"],
            handler=_myj_goods_location,
        )
    )
    return registry


def _myj_goods_search(payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query") or "")
    if any(keyword in query for keyword in ["低糖", "饮料", "推荐"]):
        return {
            "summary": "推荐低糖茶饮、无糖气泡水和低糖咖啡，可按门店库存继续过滤。",
            "items": [
                {"name": "低糖茶饮", "category": "drink"},
                {"name": "无糖气泡水", "category": "drink"},
            ],
        }
    return {"summary": "已收到商品查询，可继续补充口味、价格或规格。", "items": []}


def _myj_goods_location(payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query") or "")
    if any(keyword in query for keyword in ["可乐", "饮料", "水"]):
        return {
            "summary": "饮料通常在冷柜或常温饮料货架，可引导顾客查看收银台附近冷柜。",
            "aisle": "冷柜 / 饮料货架",
        }
    return {"summary": "暂未匹配到具体货架，可转门店人员确认。", "aisle": None}
