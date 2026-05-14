from typing import Any


async def goods_location(payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query") or "")
    if any(keyword in query for keyword in ["可乐", "饮料", "水"]):
        return {
            "summary": "饮料通常在冷柜或常温饮料货架，可引导顾客查看收银台附近冷柜。",
            "aisle": "冷柜 / 饮料货架",
            "location": {"area": "冷柜", "shelf": "第三层"},
        }
    return {"summary": "暂未匹配到具体货架，可转门店人员确认。", "aisle": None}
