from typing import Any


async def product_rank(payload: dict[str, Any]) -> dict[str, Any]:
    products = payload.get("products", [])
    query = str(payload.get("query") or "")
    if not products:
        return {"ranked": [], "reason": "no candidate products"}

    ranked = sorted(products, key=lambda p: p.get("price", 0))
    return {
        "ranked": ranked,
        "reason": f"ranked {len(ranked)} products by price for query: {query[:50]}",
    }
