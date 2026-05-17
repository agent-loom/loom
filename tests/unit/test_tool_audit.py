"""Tests for tool audit trail persistence."""

from __future__ import annotations

import pytest

from agent_platform.persistence.memory import InMemoryToolAuditRepository


@pytest.fixture
def repo():
    return InMemoryToolAuditRepository()


@pytest.mark.asyncio
async def test_record_and_list(repo):
    await repo.record(
        tool_name="search",
        status="success",
        latency_ms=42,
        payload={"q": "test"},
        output={"results": []},
        run_id="run-1",
        agent_id="echo",
    )
    events = await repo.list_events()
    assert len(events) == 1
    assert events[0]["tool_name"] == "search"
    assert events[0]["status"] == "success"
    assert events[0]["latency_ms"] == 42
    assert events[0]["run_id"] == "run-1"


@pytest.mark.asyncio
async def test_filter_by_tool_name(repo):
    await repo.record(tool_name="search", status="success", latency_ms=10)
    await repo.record(tool_name="calc", status="success", latency_ms=20)
    events = await repo.list_events(tool_name="search")
    assert len(events) == 1
    assert events[0]["tool_name"] == "search"


@pytest.mark.asyncio
async def test_filter_by_status(repo):
    await repo.record(tool_name="t1", status="success", latency_ms=10)
    await repo.record(tool_name="t2", status="failed", latency_ms=20)
    events = await repo.list_events(status="failed")
    assert len(events) == 1
    assert events[0]["tool_name"] == "t2"


@pytest.mark.asyncio
async def test_filter_by_run_id(repo):
    await repo.record(tool_name="t", status="success", latency_ms=1, run_id="r1")
    await repo.record(tool_name="t", status="success", latency_ms=1, run_id="r2")
    events = await repo.list_events(run_id="r1")
    assert len(events) == 1


@pytest.mark.asyncio
async def test_filter_by_agent_id(repo):
    await repo.record(tool_name="t", status="success", latency_ms=1, agent_id="a1")
    await repo.record(tool_name="t", status="success", latency_ms=1, agent_id="a2")
    events = await repo.list_events(agent_id="a2")
    assert len(events) == 1
    assert events[0]["agent_id"] == "a2"


@pytest.mark.asyncio
async def test_limit(repo):
    for i in range(10):
        await repo.record(tool_name=f"t{i}", status="success", latency_ms=1)
    events = await repo.list_events(limit=3)
    assert len(events) == 3


@pytest.mark.asyncio
async def test_record_with_error(repo):
    await repo.record(
        tool_name="dangerous",
        status="denied",
        latency_ms=0,
        error="APPROVAL_DENIED",
    )
    events = await repo.list_events()
    assert events[0]["error"] == "APPROVAL_DENIED"
