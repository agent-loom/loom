from typing import Any


async def goods_search(payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query") or "")
    if any(keyword in query for keyword in ["低糖", "饮料", "推荐"]):
        return {
            "summary": "推荐低糖茶饮、无糖气泡水和低糖咖啡，可按门店库存继续过滤。",
            "items": [
                {"name": "低糖茶饮", "category": "drink", "sku_id": "SKU_001"},
                {"name": "无糖气泡水", "category": "drink", "sku_id": "SKU_002"},
            ],
        }
    return {"summary": "已收到商品查询，可继续补充口味、价格或规格。", "items": []}
