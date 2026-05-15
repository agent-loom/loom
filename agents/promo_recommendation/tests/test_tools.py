import pytest

from agents.promo_recommendation.tools.product_rank import product_rank
from agents.promo_recommendation.tools.promotion_search import promotion_search


@pytest.mark.asyncio
async def test_promotion_search_with_keywords():
    result = await promotion_search({"query": "有什么饮料优惠"})
    assert "推荐" in result["summary"]
    assert len(result["promotions"]) > 0


@pytest.mark.asyncio
async def test_promotion_search_no_match():
    result = await promotion_search({"query": "你好"})
    assert result["promotions"] == []


@pytest.mark.asyncio
async def test_product_rank_sorts_by_price():
    products = [
        {"name": "B", "price": 10.0},
        {"name": "A", "price": 3.0},
        {"name": "C", "price": 7.0},
    ]
    result = await product_rank({"products": products, "query": "test"})
    assert result["ranked"][0]["name"] == "A"
    assert result["ranked"][-1]["name"] == "B"


@pytest.mark.asyncio
async def test_product_rank_empty():
    result = await product_rank({"products": [], "query": "test"})
    assert result["ranked"] == []
