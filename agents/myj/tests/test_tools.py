import pytest

from agents.myj.tools.goods_location import goods_location
from agents.myj.tools.goods_search import goods_search
from agents.myj.tools.promotion_lookup import promotion_lookup
from agents.myj.tools.store_consult import store_consult


@pytest.mark.asyncio
async def test_goods_search_drink():
    result = await goods_search({"query": "推荐低糖饮料"})
    assert len(result["items"]) > 0
    assert "低糖" in result["summary"]


@pytest.mark.asyncio
async def test_goods_search_no_match():
    result = await goods_search({"query": "随便问问"})
    assert result["items"] == []


@pytest.mark.asyncio
async def test_goods_location_cola():
    result = await goods_location({"query": "可乐在哪里"})
    assert result["aisle"] is not None
    assert "冷柜" in result["aisle"] or "饮料" in result["aisle"]


@pytest.mark.asyncio
async def test_goods_location_no_match():
    result = await goods_location({"query": "电脑在哪里"})
    assert result["aisle"] is None


@pytest.mark.asyncio
async def test_promotion_lookup_keywords():
    result = await promotion_lookup({"query": "有什么优惠活动"})
    assert len(result["promotions"]) > 0
    assert "买一送一" in result["summary"]


@pytest.mark.asyncio
async def test_promotion_lookup_no_match():
    result = await promotion_lookup({"query": "你好"})
    assert result["promotions"] == []


@pytest.mark.asyncio
async def test_store_consult_refund():
    result = await store_consult({"query": "怎么退款"})
    assert result["topic"] == "refund"
    assert "收银台" in result["summary"]


@pytest.mark.asyncio
async def test_store_consult_hours():
    result = await store_consult({"query": "营业时间"})
    assert result["topic"] == "hours"


@pytest.mark.asyncio
async def test_store_consult_general():
    result = await store_consult({"query": "其他问题"})
    assert result["topic"] == "general"
