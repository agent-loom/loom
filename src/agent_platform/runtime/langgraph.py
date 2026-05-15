from __future__ import annotations

import logging
from typing import Any, TypedDict

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

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------

class GraphState(TypedDict, total=False):
    """State that flows through every node in the graph."""

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
    """A simple edge: source -> destination (always taken)."""

    def __init__(self, source: str, destination: str) -> None:
        self.source = source
        self.destination = destination


class _ConditionalEdge:
    """A conditional edge: source -> one-of-many destinations via *router*."""

    def __init__(
        self,
        source: str,
        router: Any,
        mapping: dict[str, str],
    ) -> None:
        self.source = source
        self.router = router
        self.mapping = mapping  # router-return-value -> node name


START = "__start__"
END = "__end__"


class StateGraph:
    """Minimal state-graph executor that mirrors the LangGraph API surface.

    Nodes are async callables ``(state) -> state``.
    Edges connect nodes; conditional edges select the next node at runtime.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, Any] = {}
        self._edges: list[_Edge] = []
        self._conditional_edges: list[_ConditionalEdge] = []
        self._entry_point: str | None = None

    # -- building API -------------------------------------------------------

    def add_node(self, name: str, func: Any) -> None:
        self._nodes[name] = func

    def add_edge(self, source: str, destination: str) -> None:
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
        self._conditional_edges.append(
            _ConditionalEdge(source, router, mapping)
        )

    def compile(self) -> StateGraph:
        """Validate wiring and return self (mirrors LangGraph .compile())."""
        if self._entry_point is None:
            raise ValueError("No entry point: add an edge from START.")
        return self

    # -- execution ----------------------------------------------------------

    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        """Execute the graph asynchronously, mutating *state* in-place."""
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
    """Determine which tool (if any) should handle the query.

    Uses simple keyword matching against the tool registry keywords for the
    MVP.  Sets ``matched_tool`` and ``pending_tool_calls`` in state.
    """
    query = state.get("query", "")
    allowed_tools = state.get("allowed_tools", [])
    tool_executor: ToolExecutor | None = state.get("_tool_executor")  # type: ignore[assignment]

    matched: str | None = None

    if tool_executor is not None:
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


async def call_tool(state: GraphState) -> GraphState:
    """Execute the tool identified by ``classify_intent``."""
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
    """Format a final answer from accumulated tool results."""
    tool_results = state.get("tool_results") or []

    if tool_results:
        parts: list[str] = []
        for tr in tool_results:
            summary = tr.get("output", {}).get("summary")
            if summary:
                parts.append(summary)
            else:
                parts.append(str(tr.get("output", {})))
        state["final_answer"] = " | ".join(parts)
    else:
        query = state.get("query", "")
        state["final_answer"] = f"Received: {query}"

    return state


def should_continue(state: GraphState) -> str:
    """Conditional edge router.

    Returns ``"continue"`` when there are still pending tool calls *and* we
    have not exceeded the maximum iteration budget.  Otherwise ``"end"``.
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
    """LangGraph-based runtime backend.

    Implements a self-contained state-graph executor that follows the LangGraph
    paradigm (nodes as async functions, edges as routing) without importing
    any external graph library.

    Supports the ``graph`` entry mode from the agent manifest.
    """

    name = "langgraph"

    def __init__(
        self, tool_executor: ToolExecutor | None = None,
    ) -> None:
        self.tool_executor = tool_executor or ToolExecutor(
            create_default_tool_registry()
        )
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
