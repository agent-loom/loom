from typing import Any


async def promotion_search(payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query") or "")
    if any(kw in query for kw in ["饮料", "推荐", "优惠", "促销"]):
        return {
            "summary": "推荐元气森林白桃味（买一送一）和低糖茶饮（会员九折），冷柜有售。",
            "promotions": [
                {
                    "name": "饮料买一送一",
                    "type": "buy_one_get_one",
                    "products": [
                        {"sku_id": "SKU_10001", "name": "元气森林白桃味", "price": 5.5},
                    ],
                },
                {
                    "name": "会员九折",
                    "type": "member_discount",
                    "products": [
                        {"sku_id": "SKU_10002", "name": "低糖茶饮", "price": 4.0},
                    ],
                },
            ],
        }
    return {"summary": "暂无匹配的促销推荐。", "promotions": []}
