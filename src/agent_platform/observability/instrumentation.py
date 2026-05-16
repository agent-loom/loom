"""Helpers that attach agent-platform-specific attributes to OTel spans.

Every function accepts a span-like object (real OTel span **or**
:class:`~agent_platform.observability.tracing.NoOpSpan`) and never raises.
"""

from __future__ import annotations

from typing import Any


def instrument_agent_run(
    span: Any,
    agent_id: str,
    run_id: str,
    backend_name: str,
) -> None:
    """Set semantic attributes for an agent run span."""
    span.set_attribute("agent.id", agent_id)
    span.set_attribute("agent.run_id", run_id)
    span.set_attribute("agent.backend", backend_name)


def instrument_tool_call(
    span: Any,
    tool_name: str,
    status: str,
) -> None:
    """Set semantic attributes for a tool call span."""
    span.set_attribute("tool.name", tool_name)
    span.set_attribute("tool.status", status)


def instrument_route(
    span: Any,
    agent_id: str,
    reason: str,
) -> None:
    """Set semantic attributes for a routing decision span."""
    span.set_attribute("agent.id", agent_id)
    span.set_attribute("route.reason", reason)
