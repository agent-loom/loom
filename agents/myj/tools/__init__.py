from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_platform.tools.registry import ToolRegistry

from agent_platform.tools.registry import ToolDefinition
from agents.myj.tools.goods_location import goods_location
from agents.myj.tools.goods_search import goods_search
from agents.myj.tools.promotion_lookup import promotion_lookup
from agents.myj.tools.store_consult import store_consult


def register_myj_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolDefinition(
            name="myj.goods_search",
            description="MYJ demo product search and recommendation tool",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            permissions=["knowledge:read", "product:read"],
            handler=goods_search,
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
            handler=goods_location,
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
            handler=promotion_lookup,
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
            handler=store_consult,
            handler_ref="agents.myj.tools.store_consult:store_consult",
            owner="retail-ai",
            keywords=["退款", "退货", "营业", "时间", "几点", "咨询"],
        )
    )
