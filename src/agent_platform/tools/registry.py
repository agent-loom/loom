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
    handler_ref: str | None = None
    owner: str | None = None
    max_retries: int = 0
    keywords: list[str] = Field(default_factory=list)

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
            handler_ref="agents.myj.tools.goods_search:goods_search",
            owner="retail-ai",
            keywords=["商品", "饮料", "推荐", "低糖", "搜索", "找"],
        )
    )
    registry.register(
        ToolDefinition(
            name="myj.goods_location",
            description="MYJ demo product location lookup tool",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            permissions=["knowledge:read", "store:read"],
            handler=_myj_goods_location,
            handler_ref="agents.myj.tools.goods_location:goods_location",
            owner="retail-ai",
            keywords=["在哪", "位置", "货架", "可乐", "哪里"],
        )
    )
    registry.register(
        ToolDefinition(
            name="myj.promotion_lookup",
            description="MYJ promotion and discount lookup tool",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            permissions=["knowledge:read", "promotion:read"],
            handler=_myj_promotion_lookup,
            handler_ref="agents.myj.tools.promotion_lookup:promotion_lookup",
            owner="retail-ai",
            keywords=["优惠", "促销", "打折", "活动", "会员"],
        )
    )
    registry.register(
        ToolDefinition(
            name="myj.store_consult",
            description="MYJ store consultation tool for refund, hours, and general inquiries",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            permissions=["knowledge:read", "store:read"],
            handler=_myj_store_consult,
            handler_ref="agents.myj.tools.store_consult:store_consult",
            owner="retail-ai",
            keywords=["退款", "退货", "营业", "时间", "几点", "咨询"],
        )
    )
    registry.register(
        ToolDefinition(
            name="promo.promotion_search",
            description="Search promotions and recommend products on sale",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            permissions=["knowledge:read", "promotion:read"],
            handler=_promo_search,
            handler_ref="agents.promo_recommendation.tools.promotion_search:promotion_search",
            owner="retail-ai",
        )
    )
    registry.register(
        ToolDefinition(
            name="promo.product_rank",
            description="Rank products by price or promotion value",
            input_schema={
                "type": "object",
                "properties": {
                    "products": {"type": "array", "items": {"type": "object"}},
                },
            },
            permissions=["product:read"],
            handler=_promo_rank,
            handler_ref="agents.promo_recommendation.tools.product_rank:product_rank",
            owner="retail-ai",
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


def _myj_promotion_lookup(payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query") or "")
    if any(kw in query for kw in ["优惠", "促销", "打折", "活动"]):
        return {
            "summary": "当前门店有买一送一饮料活动和会员折扣，详情请查看门店公告。",
            "promotions": [
                {"name": "饮料买一送一", "type": "buy_one_get_one"},
                {"name": "会员九折", "type": "member_discount"},
            ],
        }
    return {"summary": "暂无匹配的促销信息。", "promotions": []}


def _myj_store_consult(payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query") or "")
    if any(kw in query for kw in ["退款", "退货"]):
        return {
            "summary": "退款请到收银台，凭小票在7天内可退货。",
            "topic": "refund",
        }
    if any(kw in query for kw in ["营业", "几点", "时间"]):
        return {
            "summary": "门店营业时间一般为 7:00-23:00，具体请以门店公告为准。",
            "topic": "hours",
        }
    return {"summary": "请联系门店工作人员获取更多帮助。", "topic": "general"}


def _promo_search(payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query") or "")
    if any(kw in query for kw in ["饮料", "推荐", "优惠", "促销"]):
        return {
            "summary": "推荐元气森林白桃味（买一送一）和低糖茶饮（会员九折）。",
            "promotions": [
                {"name": "饮料买一送一", "type": "buy_one_get_one"},
                {"name": "会员九折", "type": "member_discount"},
            ],
        }
    return {"summary": "暂无匹配的促销推荐。", "promotions": []}


def _promo_rank(payload: dict[str, Any]) -> dict[str, Any]:
    products = payload.get("products", [])
    ranked = sorted(products, key=lambda p: p.get("price", 0))
    return {"ranked": ranked, "reason": "sorted by price"}
