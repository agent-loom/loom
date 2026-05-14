from typing import Any


async def promotion_lookup(payload: dict[str, Any]) -> dict[str, Any]:
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
