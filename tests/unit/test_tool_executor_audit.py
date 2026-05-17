"""Tests for ToolExecutor audit recording integration."""

from __future__ import annotations

import pytest

from agent_platform.persistence.memory import InMemoryToolAuditRepository
from agent_platform.tools.executor import ToolExecutor
from agent_platform.tools.registry import ToolDefinition, ToolRegistry


def _make_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(ToolDefinition(
        name="echo",
        description="echo tool",
        handler=lambda payload: {"echo": payload.get("msg", "")},
        input_schema={"properties": {"msg": {"type": "string"}}},
    ))
    return reg


@pytest.mark.asyncio
async def test_execute_records_audit():
    repo = InMemoryToolAuditRepository()
    executor = ToolExecutor(
        registry=_make_registry(),
        audit_repo=repo,
    )
    result = await executor.execute(
        "echo", {"msg": "hi"},
        allowed_tools=["echo"],
        agent_id="test-agent",
        run_id="run-1",
    )
    assert result.trace.status == "success"
    events = await repo.list_events()
    assert len(events) == 1
    assert events[0]["tool_name"] == "echo"
    assert events[0]["status"] == "success"
    assert events[0]["agent_id"] == "test-agent"
    assert events[0]["run_id"] == "run-1"


@pytest.mark.asyncio
async def test_denied_tool_records_audit():
    repo = InMemoryToolAuditRepository()
    executor = ToolExecutor(
        registry=_make_registry(),
        audit_repo=repo,
    )
    result = await executor.execute(
        "echo", {},
        allowed_tools=[],  # not allowed
    )
    assert result.trace.status == "denied"
    events = await repo.list_events()
    assert len(events) == 1
    assert events[0]["status"] == "denied"


@pytest.mark.asyncio
async def test_no_audit_repo_still_works():
    executor = ToolExecutor(registry=_make_registry())
    result = await executor.execute(
        "echo", {"msg": "test"},
        allowed_tools=["echo"],
    )
    assert result.trace.status == "success"
    assert result.output == {"echo": "test"}
