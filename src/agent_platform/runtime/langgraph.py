"""LangGraph 风格运行时后端，基于状态图实现节点路由和工具调用循环。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, TypedDict

from agent_platform.domain.models import (
    AgentIdentity,
    AgentOutput,
    AgentResponse,
    ResponseText,
    ResponseTrace,
    RuntimeRequest,
    RuntimeResponse,
    ToolCallTrace,
)
from agent_platform.tools import (
    ToolExecutor,
    create_default_tool_registry,
    load_agent_tools,
)

if TYPE_CHECKING:
    from agent_platform.runtime.model_gateway import ModelGateway

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------

class GraphState(TypedDict, total=False):
    """状态图中流转的状态数据。"""

    messages: list[dict[str, Any]]
    query: str
    tool_results: list[dict[str, Any]]
    final_answer: str
    iteration_count: int
    matched_tool: str | None
    pending_tool_calls: bool
    allowed_tools: list[str]


# ---------------------------------------------------------------------------
# Lightweight graph executor (LangGraph-style, no external dependency)
# ---------------------------------------------------------------------------

class _Edge:
    """简单边：从源节点到目标节点的无条件连接。"""

    def __init__(self, source: str, destination: str) -> None:
        """初始化边，指定源节点和目标节点。"""
        self.source = source
        self.destination = destination


class _ConditionalEdge:
    """条件边：通过路由函数从源节点选择目标节点。"""

    def __init__(
        self,
        source: str,
        router: Any,
        mapping: dict[str, str],
    ) -> None:
        """初始化条件边，配置路由函数和目标映射。"""
        self.source = source
        self.router = router
        self.mapping = mapping  # router-return-value -> node name


START = "__start__"
END = "__end__"


class StateGraph:
    """轻量级状态图执行器，兼容 LangGraph API 接口。

    节点为异步函数 ``(state) -> state``，边连接节点，
    条件边在运行时动态选择下一节点。
    """

    def __init__(self) -> None:
        """初始化空的状态图。"""
        self._nodes: dict[str, Any] = {}
        self._edges: list[_Edge] = []
        self._conditional_edges: list[_ConditionalEdge] = []
        self._entry_point: str | None = None

    # -- building API -------------------------------------------------------

    def add_node(self, name: str, func: Any) -> None:
        """注册一个命名节点及其处理函数。"""
        self._nodes[name] = func

    def add_edge(self, source: str, destination: str) -> None:
        """添加从源节点到目标节点的静态边。"""
        if source == START:
            self._entry_point = destination
        else:
            self._edges.append(_Edge(source, destination))

    def add_conditional_edges(
        self,
        source: str,
        router: Any,
        mapping: dict[str, str],
    ) -> None:
        """添加条件边，通过路由函数动态选择目标节点。"""
        self._conditional_edges.append(
            _ConditionalEdge(source, router, mapping)
        )

    def compile(self) -> StateGraph:
        """校验图的连接关系并返回自身（兼容 LangGraph .compile()）。"""
        if self._entry_point is None:
            raise ValueError("No entry point: add an edge from START.")
        return self

    # -- execution ----------------------------------------------------------

    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        """异步执行状态图，沿边遍历直到到达终止节点。"""
        current = self._entry_point
        if current is None:
            raise RuntimeError("Graph has no entry point.")

        while current != END:
            node_fn = self._nodes.get(current)
            if node_fn is None:
                raise RuntimeError(f"Node not found: {current}")

            state = await node_fn(state)
            current = self._next_node(current, state)

        return state

    def _next_node(self, current: str, state: dict[str, Any]) -> str:
        # Check conditional edges first (they take priority).
        for ce in self._conditional_edges:
            if ce.source == current:
                key = ce.router(state)
                destination = ce.mapping.get(key)
                if destination is None:
                    raise RuntimeError(
                        f"Conditional edge from '{current}' returned unknown "
                        f"key '{key}'. Valid keys: {list(ce.mapping)}"
                    )
                return destination

        # Fall back to a static edge.
        for edge in self._edges:
            if edge.source == current:
                return edge.destination

        raise RuntimeError(f"No outgoing edge from node '{current}'.")


# ---------------------------------------------------------------------------
# Node functions
# ---------------------------------------------------------------------------

async def classify_intent(state: GraphState) -> GraphState:
    """意图分类节点：优先使用 LLM，降级到关键词匹配。"""
    query = state.get("query", "")
    allowed_tools = state.get("allowed_tools", [])
    tool_executor: ToolExecutor | None = state.get("_tool_executor")  # type: ignore[assignment]
    gateway: ModelGateway | None = state.get("_model_gateway")  # type: ignore[assignment]

    matched: str | None = None

    if gateway and allowed_tools and tool_executor:
        matched = await _llm_classify(gateway, tool_executor, query, allowed_tools)

    if matched is None and tool_executor is not None:
        for tool_name in allowed_tools:
            try:
                defn = tool_executor.registry.get(tool_name)
                if defn.keywords and any(kw in query for kw in defn.keywords):
                    matched = tool_name
                    break
            except LookupError:
                continue

    state["matched_tool"] = matched
    state["pending_tool_calls"] = matched is not None
    return state


async def _llm_classify(
    gateway: ModelGateway,
    tool_executor: ToolExecutor,
    query: str,
    allowed_tools: list[str],
) -> str | None:
    """通过 LLM 对用户 query 进行意图分类，返回最匹配的工具名称。"""
    from agent_platform.runtime.model_gateway import ModelMessage

    tool_descriptions: list[str] = []
    for name in allowed_tools:
        try:
            defn = tool_executor.registry.get(name)
            desc = defn.description or name
            tool_descriptions.append(f"- {name}: {desc}")
        except LookupError:
            tool_descriptions.append(f"- {name}: (无描述)")

    tools_text = "\n".join(tool_descriptions)
    system_prompt = (
        "你是意图分类器。根据用户输入，从可用工具列表中选择最匹配的工具。\n"
        "仅返回工具名称（不含其他内容）。如果没有匹配的工具，返回 NONE。\n\n"
        f"可用工具:\n{tools_text}"
    )

    try:
        result = await gateway.chat(
            messages=[
                ModelMessage(role="system", content=system_prompt),
                ModelMessage(role="user", content=query),
            ],
            temperature=0.0,
            max_tokens=64,
        )
        answer = result.content.strip()
        if answer and answer != "NONE" and answer in allowed_tools:
            logger.debug("LLM 意图分类: %s -> %s", query[:50], answer)
            return answer
        return None
    except Exception:
        logger.warning("LLM 意图分类失败，降级到关键词匹配", exc_info=True)
        return None


async def call_tool(state: GraphState) -> GraphState:
    """工具调用节点，执行 classify_intent 匹配到的工具。"""
    tool_executor: ToolExecutor | None = state.get("_tool_executor")  # type: ignore[assignment]
    tool_name = state.get("matched_tool")
    query = state.get("query", "")
    allowed_tools = state.get("allowed_tools", [])

    if tool_executor is None or tool_name is None:
        state["pending_tool_calls"] = False
        return state

    result = await tool_executor.execute(
        tool_name,
        {"query": query},
        allowed_tools=allowed_tools,
        timeout_ms=3000,
    )

    tool_results: list[dict[str, Any]] = list(state.get("tool_results") or [])
    tool_results.append({
        "tool_name": tool_name,
        "output": result.output,
        "status": result.trace.status,
        "latency_ms": result.trace.latency_ms,
    })
    state["tool_results"] = tool_results

    # After executing the tool we no longer have a pending call; clear the
    # matched tool so the next classify_intent pass can pick up a new one.
    state["matched_tool"] = None
    state["pending_tool_calls"] = False
    state["iteration_count"] = state.get("iteration_count", 0) + 1

    return state


async def generate_response(state: GraphState) -> GraphState:
    """响应生成节点：优先使用 LLM 生成自然语言回答，降级到模板拼接。"""
    tool_results = state.get("tool_results") or []
    query = state.get("query", "")
    gateway: ModelGateway | None = state.get("_model_gateway")  # type: ignore[assignment]

    if tool_results:
        parts: list[str] = []
        for tr in tool_results:
            summary = tr.get("output", {}).get("summary")
            if summary:
                parts.append(summary)
            else:
                parts.append(str(tr.get("output", {})))
        context = " | ".join(parts)

        if gateway:
            llm_answer = await _llm_generate(gateway, query, context)
            if llm_answer:
                state["final_answer"] = llm_answer
                return state

        state["final_answer"] = context
    else:
        if gateway:
            llm_answer = await _llm_direct_answer(gateway, query)
            if llm_answer:
                state["final_answer"] = llm_answer
                return state
        state["final_answer"] = f"Received: {query}"

    return state


async def _llm_generate(
    gateway: ModelGateway, query: str, context: str,
) -> str | None:
    """使用 LLM 基于工具结果生成自然语言回答。"""
    from agent_platform.runtime.model_gateway import ModelMessage

    try:
        result = await gateway.chat(
            messages=[
                ModelMessage(
                    role="system",
                    content="基于工具执行结果，用简洁自然的语言回答用户问题。",
                ),
                ModelMessage(
                    role="user",
                    content=f"用户问题: {query}\n\n工具结果: {context}",
                ),
            ],
            temperature=0.3,
            max_tokens=512,
        )
        return result.content.strip() or None
    except Exception:
        logger.warning("LLM 响应生成失败，使用原始工具结果", exc_info=True)
        return None


async def _llm_direct_answer(gateway: ModelGateway, query: str) -> str | None:
    """无工具结果时直接使用 LLM 回答。"""
    from agent_platform.runtime.model_gateway import ModelMessage

    try:
        result = await gateway.chat(
            messages=[
                ModelMessage(role="user", content=query),
            ],
            temperature=0.5,
            max_tokens=512,
        )
        return result.content.strip() or None
    except Exception:
        logger.warning("LLM 直接回答失败，使用默认响应", exc_info=True)
        return None


def should_continue(state: GraphState) -> str:
    """条件边路由函数。

    有待处理工具调用且未超出迭代上限时返回 "continue"，
    否则返回 "end"。
    """
    max_iterations = state.get("_max_iterations", 4)  # type: ignore[arg-type]
    iteration_count = state.get("iteration_count", 0)

    if iteration_count >= max_iterations:
        return "end"

    if state.get("pending_tool_calls"):
        return "continue"

    return "end"


# ---------------------------------------------------------------------------
# LangGraph runtime backend
# ---------------------------------------------------------------------------

class LangGraphRuntimeBackend:
    """基于状态图的 LangGraph 运行时后端。

    内置状态图执行器，遵循 LangGraph 范式（节点为异步函数，
    边为路由），无需引入外部图库。
    """

    name = "langgraph"

    def __init__(
        self,
        tool_executor: ToolExecutor | None = None,
        model_gateway: ModelGateway | None = None,
    ) -> None:
        self.tool_executor = tool_executor or ToolExecutor(
            create_default_tool_registry()
        )
        self.model_gateway = model_gateway
        self._loaded_agents: set[str] = set()

    def _ensure_agent_tools(self, agent_spec) -> None:
        """Load tools for the agent if not already loaded."""
        agent_id = agent_spec.agent_id
        if agent_id in self._loaded_agents:
            return
        package_path = agent_spec.package_path
        load_agent_tools(
            self.tool_executor.registry,
            package_path,
            agent_id,
        )
        self._loaded_agents.add(agent_id)

    # -- graph construction -------------------------------------------------

    def _build_graph(self) -> StateGraph:
        graph = StateGraph()

        graph.add_node("classify_intent", classify_intent)
        graph.add_node("call_tool", call_tool)
        graph.add_node("generate_response", generate_response)

        # START -> classify_intent
        graph.add_edge(START, "classify_intent")

        # classify_intent -> call_tool
        graph.add_edge("classify_intent", "call_tool")

        # call_tool -> generate_response
        graph.add_edge("call_tool", "generate_response")

        # generate_response -> should_continue -> END or back to classify_intent
        graph.add_conditional_edges(
            "generate_response",
            should_continue,
            {
                "continue": "classify_intent",
                "end": END,
            },
        )

        return graph.compile()

    # -- execution ----------------------------------------------------------

    async def run(self, request: RuntimeRequest) -> RuntimeResponse:
        """构建状态图并执行请求，返回运行时响应。"""
        self._ensure_agent_tools(request.agent_spec)
        agent = request.agent_spec
        query = request.request.input.query

        graph_config = agent.manifest.extensions.get("langgraph", {})
        max_iterations: int = (
            graph_config.get("max_iterations")
            or agent.manifest.runtime.max_iterations
        )

        logger.info(
            "LangGraph execution for %s (graph_config=%s)",
            agent.agent_id,
            graph_config,
        )

        allowed_tools = list(agent.manifest.tools.allow)

        initial_state: dict[str, Any] = {
            "messages": [],
            "query": query,
            "tool_results": [],
            "final_answer": "",
            "iteration_count": 0,
            "matched_tool": None,
            "pending_tool_calls": False,
            "allowed_tools": allowed_tools,
            # Internal references (not part of the public GraphState schema)
            "_tool_executor": self.tool_executor,
            "_model_gateway": self.model_gateway,
            "_max_iterations": max_iterations,
        }

        graph = self._build_graph()
        final_state = await graph.ainvoke(initial_state)

        display = final_state.get("final_answer") or f"Agent {agent.agent_id} received: {query}"
        tool_results = final_state.get("tool_results") or []

        tool_call_traces = [
            ToolCallTrace(
                tool_name=tr["tool_name"],
                status=tr.get("status", "success"),
                latency_ms=tr.get("latency_ms"),
            )
            for tr in tool_results
        ]

        response = AgentResponse(
            request_id=request.request.request_id,
            session_id=request.request.session_id,
            agent=AgentIdentity(
                agent_id=agent.agent_id,
                agent_version=agent.version,
                deployment_id=request.deployment_id,
            ),
            output=AgentOutput(
                text=ResponseText(display=display, tts=display),
            ),
            trace=ResponseTrace(
                route_reason=request.route_reason,
                tool_calls=tool_call_traces,
            ),
            debug={
                "runtime_backend": "langgraph",
                "graph_config": graph_config,
                "iterations": final_state.get("iteration_count", 0),
            },
        )
        return RuntimeResponse(response=response)
