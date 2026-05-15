from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_platform.tools.registry import ToolRegistry

from agent_platform.tools.registry import ToolDefinition
from agents.promo_recommendation.tools.product_rank import product_rank
from agents.promo_recommendation.tools.promotion_search import promotion_search


def register_promo_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolDefinition(
            name="promo.promotion_search",
            description="Search promotions and recommend products on sale",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            permissions=["knowledge:read", "promotion:read"],
            handler=promotion_search,
            handler_ref="agents.promo_recommendation.tools.promotion_search:promotion_search",
            owner="retail-ai",
            keywords=["饮料", "推荐", "优惠", "促销", "打折"],
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
            handler=product_rank,
            handler_ref="agents.promo_recommendation.tools.product_rank:product_rank",
            owner="retail-ai",
        )
    )
