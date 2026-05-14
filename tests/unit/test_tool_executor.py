import pytest

from agent_platform.tools import ToolExecutor, create_default_tool_registry


@pytest.mark.asyncio
async def test_tool_executor_runs_allowed_tool():
    executor = ToolExecutor(create_default_tool_registry())

    result = await executor.execute(
        "myj.goods_search",
        {"query": "推荐低糖饮料"},
        allowed_tools=["myj.goods_search"],
    )

    assert result.trace.status == "success"
    assert "summary" in result.output


@pytest.mark.asyncio
async def test_tool_executor_denies_unallowed_tool():
    executor = ToolExecutor(create_default_tool_registry())

    result = await executor.execute(
        "myj.goods_search",
        {"query": "推荐低糖饮料"},
        allowed_tools=[],
    )

    assert result.trace.status == "denied"
    assert result.trace.error == "TOOL_NOT_ALLOWED"
