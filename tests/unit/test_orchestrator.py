"""Tests for agent_platform.runtime.orchestrator."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_platform.domain.models import (
    AgentManifest,
    AgentRequest,
    AgentSpec,
    ManifestMetadata,
    ManifestOutput,
    ManifestTools,
    ManifestVersion,
    OutputStatus,
    RuntimeRequest,
)
from agent_platform.runtime.orchestrator import (
    DirectReplyWorker,
    HandoffWorker,
    ToolWorker,
    WorkerOrchestrator,
)
from agent_platform.runtime.worker import AgentTask

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(query: str = "hello", **metadata) -> AgentTask:
    return AgentTask(
        task_id="task-1",
        query=query,
        intent="",
        metadata=metadata,
    )


def _make_runtime_request(
    query: str = "hello",
    tools_allow: list[str] | None = None,
    command_allowlist: list[str] | None = None,
) -> RuntimeRequest:
    return RuntimeRequest(
        request=AgentRequest(
            request_id="req-1",
            session_id="sess-1",
            input={"query": query},
        ),
        agent_spec=AgentSpec(
            manifest=AgentManifest(
                api_version="agent.platform/v1",
                kind="AgentPackage",
                metadata=ManifestMetadata(id="test-agent", name="Test"),
                version=ManifestVersion(package_version="0.1.0"),
                tools=ManifestTools(allow=tools_allow or []),
                output=ManifestOutput(command_allowlist=command_allowlist or []),
            ),
            package_path=Path("/tmp/test-agent"),
        ),
    )


# ---------------------------------------------------------------------------
# DirectReplyWorker tests
# ---------------------------------------------------------------------------


class TestDirectReplyWorker:
    def test_can_handle_returns_low_score(self):
        worker = DirectReplyWorker()
        task = _make_task(query="anything")
        score = worker.can_handle(task)

        assert score.worker_name == "direct_reply"
        assert score.score == pytest.approx(0.1)
        assert score.reason == "fallback"

    @pytest.mark.asyncio
    async def test_run_returns_query_echo(self):
        worker = DirectReplyWorker()
        task = _make_task(query="How do I reset my password?")

        result = await worker.run(task)

        assert result.worker_name == "direct_reply"
        assert "How do I reset my password?" in result.display

    @pytest.mark.asyncio
    async def test_run_uses_query_in_display(self, tmp_path: Path):
        worker = DirectReplyWorker()
        task = _make_task(query="hi")

        result = await worker.run(task)
        assert "hi" in result.display

    @pytest.mark.asyncio
    async def test_run_fallback_when_prompt_file_missing(self):
        worker = DirectReplyWorker()
        task = _make_task(
            query="hi",
            direct_reply_prompt="/nonexistent/path/prompt.md",
        )

        result = await worker.run(task)
        assert "hi" in result.display

    def test_worker_name(self):
        assert DirectReplyWorker.name == "direct_reply"


# ---------------------------------------------------------------------------
# HandoffWorker tests
# ---------------------------------------------------------------------------


class TestHandoffWorker:
    def test_can_handle_matches_default_keyword(self):
        worker = HandoffWorker()
        task = _make_task(query="我要转人工服务")
        score = worker.can_handle(task)

        assert score.worker_name == "handoff"
        assert score.score == 1.0
        assert score.reason == "handoff_keyword"

    def test_can_handle_no_match(self):
        worker = HandoffWorker()
        task = _make_task(query="What is the weather?")
        score = worker.can_handle(task)

        assert score.score == 0.0
        assert score.reason == "no_match"

    def test_can_handle_custom_intents(self):
        worker = HandoffWorker(handoff_intents=["talk to human", "real person"])
        task = _make_task(query="I want to talk to human")
        score = worker.can_handle(task)

        assert score.score == 1.0

    def test_can_handle_custom_intents_no_match(self):
        worker = HandoffWorker(handoff_intents=["talk to human"])
        task = _make_task(query="what products do you have?")
        score = worker.can_handle(task)

        assert score.score == 0.0

    @pytest.mark.asyncio
    async def test_run_returns_handoff_status(self):
        worker = HandoffWorker()
        task = _make_task(query="转人工")

        result = await worker.run(task)

        assert result.worker_name == "handoff"
        assert result.status == "handoff_required"
        assert "人工客服" in result.display
        assert len(result.commands) == 1
        assert result.commands[0]["name"] == "human.handoff"

    def test_worker_name(self):
        assert HandoffWorker().name == "handoff"


# ---------------------------------------------------------------------------
# ToolWorker tests
# ---------------------------------------------------------------------------


class TestToolWorker:
    def test_keyword_matching_full_match(self):
        worker = ToolWorker(
            name="search_worker",
            tool_name="search",
            keywords=["search", "find", "look"],
        )
        task = _make_task(query="search for products and find items, look at catalog")
        score = worker.can_handle(task)

        assert score.worker_name == "search_worker"
        assert score.score == pytest.approx(1.0)
        assert "3/3" in score.reason

    def test_keyword_matching_partial(self):
        worker = ToolWorker(
            name="search_worker",
            tool_name="search",
            keywords=["search", "find", "look"],
        )
        task = _make_task(query="I want to search for something")
        score = worker.can_handle(task)

        assert score.score == pytest.approx(1.0 / 3.0)
        assert "1/3" in score.reason

    def test_keyword_matching_no_match(self):
        worker = ToolWorker(
            name="search_worker",
            tool_name="search",
            keywords=["search", "find"],
        )
        task = _make_task(query="hello world")
        score = worker.can_handle(task)

        assert score.score == 0.0

    def test_keyword_matching_empty_keywords(self):
        worker = ToolWorker(
            name="empty_worker",
            tool_name="noop",
            keywords=[],
        )
        task = _make_task(query="anything")
        score = worker.can_handle(task)

        assert score.score == 0.0
        assert score.reason == "no_keywords"

    @pytest.mark.asyncio
    async def test_run_without_executor(self):
        worker = ToolWorker(
            name="search_worker",
            tool_name="search",
            keywords=["search"],
        )
        task = _make_task(query="search items")

        result = await worker.run(task)

        assert result.worker_name == "search_worker"
        assert "search" in result.display.lower()
        assert result.data == {"tool": "search"}

    def test_worker_name(self):
        w = ToolWorker(name="my_tool", tool_name="t", keywords=[])
        assert w.name == "my_tool"


# ---------------------------------------------------------------------------
# WorkerOrchestrator tests
# ---------------------------------------------------------------------------


class TestWorkerOrchestrator:
    @pytest.mark.asyncio
    async def test_route_selects_best_worker(self):
        orch = WorkerOrchestrator()
        orch.register(DirectReplyWorker())
        orch.register(HandoffWorker())

        req = _make_runtime_request(query="我要转人工")
        result = await orch.route_and_run(req)

        assert result.response.output.status == OutputStatus.HANDOFF_REQUIRED
        assert "人工客服" in result.response.output.text.display
        assert result.response.trace.route_reason is not None
        assert "handoff" in result.response.trace.route_reason

    @pytest.mark.asyncio
    async def test_route_falls_back_to_default_worker(self):
        orch = WorkerOrchestrator(default_worker_name="direct_reply")
        orch.register(DirectReplyWorker())
        orch.register(HandoffWorker())

        req = _make_runtime_request(query="tell me about the weather")
        result = await orch.route_and_run(req)

        # HandoffWorker scores 0.0, DirectReplyWorker scores 0.1
        assert result.response.output.status == OutputStatus.COMPLETED
        assert "weather" in result.response.output.text.display

    @pytest.mark.asyncio
    async def test_route_creates_fallback_when_no_workers_registered(self):
        orch = WorkerOrchestrator()
        # No workers registered at all

        req = _make_runtime_request(query="hello")
        result = await orch.route_and_run(req)

        # Should create a DirectReplyWorker as fallback
        assert result.response.output.status == OutputStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_route_selects_higher_scoring_tool_worker(self):
        orch = WorkerOrchestrator()
        orch.register(DirectReplyWorker())  # score = 0.1

        search_worker = ToolWorker(
            name="search",
            tool_name="myj.goods_search",
            keywords=["search", "find"],
        )
        orch.register(search_worker)

        req = _make_runtime_request(query="search and find products")
        result = await orch.route_and_run(req)

        # ToolWorker should score 1.0 (2/2 keywords matched) > 0.1
        assert "search" in result.response.trace.route_reason

    @pytest.mark.asyncio
    async def test_response_includes_debug_info(self):
        orch = WorkerOrchestrator()
        orch.register(DirectReplyWorker())

        req = _make_runtime_request(query="hi")
        result = await orch.route_and_run(req)

        assert result.response.debug is not None
        assert "worker" in result.response.debug
        assert "score" in result.response.debug

    @pytest.mark.asyncio
    async def test_response_agent_identity(self):
        orch = WorkerOrchestrator()
        orch.register(DirectReplyWorker())

        req = _make_runtime_request(query="hi")
        result = await orch.route_and_run(req)

        assert result.response.agent.agent_id == "test-agent"
        assert result.response.agent.agent_version == "0.1.0"
        assert result.response.request_id == "req-1"
        assert result.response.session_id == "sess-1"

    @pytest.mark.asyncio
    async def test_filters_commands_by_manifest_allowlist(self):
        orch = WorkerOrchestrator()
        orch.register(HandoffWorker())

        req = _make_runtime_request(
            query="我要转人工",
            command_allowlist=["product.locate"],
        )
        result = await orch.route_and_run(req)

        assert result.response.output.status == OutputStatus.HANDOFF_REQUIRED
        assert result.response.output.commands == []
