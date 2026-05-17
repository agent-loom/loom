from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_platform.domain.models import (
    AgentInput,
    AgentManifest,
    AgentRequest,
    AgentSpec,
    RuntimeRequest,
)
from agent_platform.runtime.langgraph import (
    END,
    GraphState,
    LangGraphRuntimeBackend,
    StateGraph,
    _llm_classify,
    classify_intent,
    generate_response,
    should_continue,
)
from agent_platform.tools import (
    ToolExecutor,
    create_default_tool_registry,
    load_agent_tools,
)

_AGENTS_DIR = Path(__file__).resolve().parents[2] / "agents"


def _registry_with_myj_tools():
    """Return a tool registry pre-loaded with the myj agent tools."""
    registry = create_default_tool_registry()
    load_agent_tools(registry, _AGENTS_DIR / "myj", "myj")
    return registry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_spec(
    agent_id: str = "graph_demo",
    tools_allow: list[str] | None = None,
    max_iterations: int = 4,
    langgraph_ext: dict | None = None,
) -> AgentSpec:
    """Build a minimal AgentSpec without touching the filesystem."""

    manifest_data = {
        "api_version": "agent.platform/v1",
        "kind": "AgentPackage",
        "metadata": {"id": agent_id, "name": "Graph Demo"},
        "version": {"package_version": "0.1.0"},
        "runtime": {
            "backend": "langgraph",
            "max_iterations": max_iterations,
        },
        "tools": {"allow": tools_allow or []},
        "output": {"protocol": "agent-chat/v1"},
        "extensions": {"langgraph": langgraph_ext or {}},
    }
    manifest = AgentManifest.model_validate(manifest_data)
    return AgentSpec(
        manifest=manifest, package_path=Path("/tmp/fake")
    )


def _make_request(
    query: str,
    spec: AgentSpec | None = None,
    tools_allow: list[str] | None = None,
    max_iterations: int = 4,
) -> RuntimeRequest:
    if spec is None:
        spec = _make_spec(
            tools_allow=tools_allow,
            max_iterations=max_iterations,
        )
    return RuntimeRequest(
        request=AgentRequest(
            agent_id=spec.agent_id,
            input=AgentInput(query=query),
        ),
        agent_spec=spec,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLangGraphBackendCreatesValidResponse:
    """The backend should always return a well-formed RuntimeResponse."""

    @pytest.mark.asyncio
    async def test_simple_query_no_tools(self):
        backend = LangGraphRuntimeBackend()
        request = _make_request("hello")
        result = await backend.run(request)

        assert result.response.output.status == "completed"
        assert result.response.output.text.display
        assert (
            result.response.debug["runtime_backend"]
            == "langgraph"
        )
        assert result.response.agent.agent_id == "graph_demo"

    @pytest.mark.asyncio
    async def test_response_contains_query_when_no_tool_matches(
        self,
    ):
        backend = LangGraphRuntimeBackend()
        request = _make_request(
            "something random with no matching keywords"
        )
        result = await backend.run(request)

        assert (
            "something random"
            in result.response.output.text.display
        )


class TestToolsCalledDuringGraphExecution:
    """When the query matches a tool's keywords the tool must be invoked."""

    @pytest.mark.asyncio
    async def test_goods_search_tool_called(self):
        registry = _registry_with_myj_tools()
        executor = ToolExecutor(registry)
        backend = LangGraphRuntimeBackend(
            tool_executor=executor
        )

        request = _make_request(
            query="推荐低糖饮料",
            tools_allow=["myj.goods_search"],
        )
        result = await backend.run(request)

        assert len(result.response.trace.tool_calls) >= 1
        assert (
            result.response.trace.tool_calls[0].tool_name
            == "myj.goods_search"
        )
        assert (
            result.response.trace.tool_calls[0].status
            == "success"
        )
        assert "低糖" in result.response.output.text.display

    @pytest.mark.asyncio
    async def test_promotion_lookup_tool_called(self):
        registry = _registry_with_myj_tools()
        executor = ToolExecutor(registry)
        backend = LangGraphRuntimeBackend(
            tool_executor=executor
        )

        request = _make_request(
            query="有什么优惠活动",
            tools_allow=["myj.promotion_lookup"],
        )
        result = await backend.run(request)

        assert len(result.response.trace.tool_calls) >= 1
        assert (
            result.response.trace.tool_calls[0].tool_name
            == "myj.promotion_lookup"
        )

    @pytest.mark.asyncio
    async def test_no_tool_called_when_no_keyword_match(self):
        registry = _registry_with_myj_tools()
        executor = ToolExecutor(registry)
        backend = LangGraphRuntimeBackend(
            tool_executor=executor
        )

        request = _make_request(
            query="unrelated english sentence",
            tools_allow=[
                "myj.goods_search",
                "myj.promotion_lookup",
            ],
        )
        result = await backend.run(request)

        assert result.response.trace.tool_calls == []
        assert result.response.output.text.display.startswith(
            "Received:"
        )


class TestMaxIterationsRespected:
    """The graph must stop even if the conditional edge keeps
    returning 'continue'."""

    @pytest.mark.asyncio
    async def test_iteration_count_in_debug(self):
        registry = _registry_with_myj_tools()
        executor = ToolExecutor(registry)
        backend = LangGraphRuntimeBackend(
            tool_executor=executor
        )

        request = _make_request(
            query="推荐低糖饮料",
            tools_allow=["myj.goods_search"],
            max_iterations=2,
        )
        result = await backend.run(request)

        iterations = result.response.debug.get("iterations", 0)
        assert iterations <= 2

    @pytest.mark.asyncio
    async def test_single_iteration_max(self):
        registry = _registry_with_myj_tools()
        executor = ToolExecutor(registry)
        backend = LangGraphRuntimeBackend(
            tool_executor=executor
        )

        spec = _make_spec(
            tools_allow=["myj.goods_search"],
            max_iterations=1,
        )
        request = _make_request(query="推荐低糖饮料", spec=spec)
        result = await backend.run(request)

        iterations = result.response.debug.get("iterations", 0)
        assert iterations <= 1
        assert result.response.output.status == "completed"

    @pytest.mark.asyncio
    async def test_should_continue_returns_end_at_limit(self):
        state: GraphState = {
            "iteration_count": 4,
            "pending_tool_calls": True,
            "_max_iterations": 4,  # type: ignore[typeddict-unknown-key]
        }
        assert should_continue(state) == "end"

    @pytest.mark.asyncio
    async def test_should_continue_returns_continue_below_limit(
        self,
    ):
        state: GraphState = {
            "iteration_count": 1,
            "pending_tool_calls": True,
            "_max_iterations": 4,  # type: ignore[typeddict-unknown-key]
        }
        assert should_continue(state) == "continue"


class TestClassifyIntentNode:
    """Unit tests for the classify_intent node function in
    isolation."""

    @pytest.mark.asyncio
    async def test_matches_goods_search_keywords(self):
        registry = _registry_with_myj_tools()
        executor = ToolExecutor(registry)

        state: dict = {
            "query": "帮我推荐低糖饮料",
            "allowed_tools": ["myj.goods_search"],
            "_tool_executor": executor,
            "matched_tool": None,
            "pending_tool_calls": False,
        }
        result = await classify_intent(state)
        assert result["matched_tool"] == "myj.goods_search"
        assert result["pending_tool_calls"] is True

    @pytest.mark.asyncio
    async def test_matches_location_keywords(self):
        registry = _registry_with_myj_tools()
        executor = ToolExecutor(registry)

        state: dict = {
            "query": "可乐在哪里",
            "allowed_tools": ["myj.goods_location"],
            "_tool_executor": executor,
            "matched_tool": None,
            "pending_tool_calls": False,
        }
        result = await classify_intent(state)
        assert result["matched_tool"] == "myj.goods_location"
        assert result["pending_tool_calls"] is True

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self):
        registry = _registry_with_myj_tools()
        executor = ToolExecutor(registry)

        state: dict = {
            "query": "random unrelated query in english",
            "allowed_tools": [
                "myj.goods_search",
                "myj.goods_location",
            ],
            "_tool_executor": executor,
            "matched_tool": None,
            "pending_tool_calls": False,
        }
        result = await classify_intent(state)
        assert result["matched_tool"] is None
        assert result["pending_tool_calls"] is False

    @pytest.mark.asyncio
    async def test_no_match_when_tool_not_in_allowed(self):
        registry = _registry_with_myj_tools()
        executor = ToolExecutor(registry)

        state: dict = {
            "query": "推荐低糖饮料",
            "allowed_tools": [],
            "_tool_executor": executor,
            "matched_tool": None,
            "pending_tool_calls": False,
        }
        result = await classify_intent(state)
        assert result["matched_tool"] is None
        assert result["pending_tool_calls"] is False

    @pytest.mark.asyncio
    async def test_no_crash_without_executor(self):
        state: dict = {
            "query": "推荐低糖饮料",
            "allowed_tools": ["myj.goods_search"],
            "_tool_executor": None,
            "matched_tool": None,
            "pending_tool_calls": False,
        }
        result = await classify_intent(state)
        assert result["matched_tool"] is None


class TestStateGraphExecutor:
    """Sanity tests for the lightweight StateGraph executor."""

    @pytest.mark.asyncio
    async def test_simple_linear_graph(self):
        async def node_a(state):
            state["visited"] = state.get("visited", []) + [
                "a"
            ]
            return state

        async def node_b(state):
            state["visited"] = state.get("visited", []) + [
                "b"
            ]
            return state

        g = StateGraph()
        g.add_node("a", node_a)
        g.add_node("b", node_b)
        g.add_edge("__start__", "a")
        g.add_edge("a", "b")
        g.add_edge("b", END)
        g.compile()

        result = await g.ainvoke({})
        assert result["visited"] == ["a", "b"]

    @pytest.mark.asyncio
    async def test_conditional_edge_loop(self):
        async def increment(state):
            state["count"] = state.get("count", 0) + 1
            return state

        def router(state):
            return (
                "end" if state["count"] >= 3 else "loop"
            )

        g = StateGraph()
        g.add_node("inc", increment)
        g.add_edge("__start__", "inc")
        g.add_conditional_edges(
            "inc", router, {"loop": "inc", "end": END}
        )
        g.compile()

        result = await g.ainvoke({"count": 0})
        assert result["count"] == 3


class TestLLMClassifyIntent:
    """测试 LLM 意图分类集成。"""

    def _mock_gateway(self, content: str):
        gw = MagicMock()
        chat_result = MagicMock()
        chat_result.content = content
        gw.chat = AsyncMock(return_value=chat_result)
        return gw

    @pytest.mark.asyncio
    async def test_llm_classify_returns_matched_tool(self):
        registry = _registry_with_myj_tools()
        executor = ToolExecutor(registry)
        gw = self._mock_gateway("myj.goods_search")
        result = await _llm_classify(gw, executor, "推荐低糖饮料", ["myj.goods_search"])
        assert result == "myj.goods_search"

    @pytest.mark.asyncio
    async def test_llm_classify_returns_none_for_unknown(self):
        registry = _registry_with_myj_tools()
        executor = ToolExecutor(registry)
        gw = self._mock_gateway("NONE")
        result = await _llm_classify(gw, executor, "random query", ["myj.goods_search"])
        assert result is None

    @pytest.mark.asyncio
    async def test_llm_classify_falls_back_on_error(self):
        registry = _registry_with_myj_tools()
        executor = ToolExecutor(registry)
        gw = MagicMock()
        gw.chat = AsyncMock(side_effect=RuntimeError("LLM 不可用"))
        result = await _llm_classify(gw, executor, "推荐饮料", ["myj.goods_search"])
        assert result is None

    @pytest.mark.asyncio
    async def test_classify_intent_uses_llm_then_keyword(self):
        """LLM 分类优先于关键词匹配。"""
        registry = _registry_with_myj_tools()
        executor = ToolExecutor(registry)
        gw = self._mock_gateway("myj.goods_search")
        state: dict = {
            "query": "any query",
            "allowed_tools": ["myj.goods_search"],
            "_tool_executor": executor,
            "_model_gateway": gw,
            "matched_tool": None,
            "pending_tool_calls": False,
        }
        result = await classify_intent(state)
        assert result["matched_tool"] == "myj.goods_search"
        gw.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_classify_intent_llm_fail_falls_to_keyword(self):
        """LLM 失败时降级到关键词匹配。"""
        registry = _registry_with_myj_tools()
        executor = ToolExecutor(registry)
        gw = MagicMock()
        gw.chat = AsyncMock(side_effect=RuntimeError("timeout"))
        state: dict = {
            "query": "推荐低糖饮料",
            "allowed_tools": ["myj.goods_search"],
            "_tool_executor": executor,
            "_model_gateway": gw,
            "matched_tool": None,
            "pending_tool_calls": False,
        }
        result = await classify_intent(state)
        assert result["matched_tool"] == "myj.goods_search"

    @pytest.mark.asyncio
    async def test_classify_intent_no_gateway_uses_keyword(self):
        """无 ModelGateway 时直接使用关键词匹配。"""
        registry = _registry_with_myj_tools()
        executor = ToolExecutor(registry)
        state: dict = {
            "query": "推荐低糖饮料",
            "allowed_tools": ["myj.goods_search"],
            "_tool_executor": executor,
            "_model_gateway": None,
            "matched_tool": None,
            "pending_tool_calls": False,
        }
        result = await classify_intent(state)
        assert result["matched_tool"] == "myj.goods_search"


class TestLLMGenerateResponse:
    """测试 LLM 响应生成集成。"""

    @pytest.mark.asyncio
    async def test_generate_with_gateway_and_tool_results(self):
        gw = MagicMock()
        chat_result = MagicMock()
        chat_result.content = "为您推荐了3款低糖饮料"
        gw.chat = AsyncMock(return_value=chat_result)
        state: dict = {
            "query": "推荐低糖饮料",
            "tool_results": [{"output": {"summary": "找到3款低糖饮料"}}],
            "_model_gateway": gw,
        }
        result = await generate_response(state)
        assert result["final_answer"] == "为您推荐了3款低糖饮料"

    @pytest.mark.asyncio
    async def test_generate_without_gateway_uses_template(self):
        state: dict = {
            "query": "hello",
            "tool_results": [],
            "_model_gateway": None,
        }
        result = await generate_response(state)
        assert result["final_answer"] == "Received: hello"

    @pytest.mark.asyncio
    async def test_generate_gateway_error_falls_back(self):
        gw = MagicMock()
        gw.chat = AsyncMock(side_effect=RuntimeError("error"))
        state: dict = {
            "query": "hello",
            "tool_results": [{"output": {"summary": "tool output"}}],
            "_model_gateway": gw,
        }
        result = await generate_response(state)
        assert result["final_answer"] == "tool output"


class TestBackendWithModelGateway:
    """测试 LangGraphRuntimeBackend 接受 ModelGateway。"""

    @pytest.mark.asyncio
    async def test_backend_accepts_model_gateway(self):
        gw = MagicMock()
        chat_result = MagicMock()
        chat_result.content = "NONE"
        gw.chat = AsyncMock(return_value=chat_result)
        backend = LangGraphRuntimeBackend(model_gateway=gw)
        request = _make_request("hello")
        result = await backend.run(request)
        assert result.response.output.status == "completed"
