from typing import Any


async def store_consult(payload: dict[str, Any]) -> dict[str, Any]:
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
