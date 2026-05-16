"""Admin API router — agent, session, run, and tool management endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from agent_platform.api.admin_deps import AdminDeps
from agent_platform.registry.registry import AgentNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def _deps(request: Request) -> AdminDeps:
    """Retrieve AdminDeps from app state."""
    return request.app.state.admin_deps


# ---------------------------------------------------------------------------
# Agent Management
# ---------------------------------------------------------------------------


@router.get("/agents")
async def list_agents(request: Request) -> list[dict[str, Any]]:
    """List all agents with full manifest details."""
    deps = _deps(request)
    specs = await deps.registry.list_agents()
    return [
        {
            "agent_id": spec.agent_id,
            "version": spec.version,
            "name": spec.manifest.metadata.name,
            "manifest": spec.manifest.model_dump(mode="json"),
        }
        for spec in specs
    ]


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, request: Request) -> dict[str, Any]:
    """Get full agent details including manifest, deployments, and recent runs."""
    deps = _deps(request)
    try:
        spec = await deps.registry.get(agent_id)
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    deployments = await deps.registry.list_deployments()
    agent_deployments = [
        d.model_dump(mode="json") for d in deployments if d.agent_id == agent_id
    ]

    runs = await deps.runtime_manager.run_store.list_runs(agent_id=agent_id, limit=20)
    recent_runs = [r.model_dump(mode="json") for r in runs]

    return {
        "agent_id": spec.agent_id,
        "version": spec.version,
        "name": spec.manifest.metadata.name,
        "manifest": spec.manifest.model_dump(mode="json"),
        "deployments": agent_deployments,
        "recent_runs": recent_runs,
    }


@router.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str, request: Request) -> dict[str, str]:
    """Soft-delete / unregister an agent (remove from local specs)."""
    deps = _deps(request)
    registry = deps.registry
    if agent_id not in registry._local_specs:
        # Try discovering first
        if not registry._local_specs:
            await registry.discover()
        if agent_id not in registry._local_specs:
            raise HTTPException(status_code=404, detail=f"agent not found: {agent_id}")

    registry._local_specs.pop(agent_id, None)
    return {"status": "deleted", "agent_id": agent_id}


# ---------------------------------------------------------------------------
# System Status
# ---------------------------------------------------------------------------


@router.get("/status")
async def system_status(request: Request) -> dict[str, Any]:
    """System overview: counts of agents, deployments, active sessions, total runs."""
    deps = _deps(request)

    agents = await deps.registry.list_agents()
    deployments = await deps.registry.list_deployments()
    sessions = await deps.runtime_manager.session_store.list_sessions()
    runs = await deps.runtime_manager.run_store.list_runs()

    return {
        "agents": len(agents),
        "deployments": len(deployments),
        "active_sessions": len(sessions),
        "total_runs": len(runs),
    }


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


@router.get("/runs")
async def list_runs(
    request: Request,
    agent_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List recent runs with optional agent_id and status filters."""
    deps = _deps(request)
    runs = await deps.runtime_manager.run_store.list_runs(agent_id=agent_id)
    results = [r.model_dump(mode="json") for r in runs]
    if status is not None:
        results = [r for r in results if r.get("status") == status]
    return results


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> dict[str, Any]:
    """Get run details by run_id."""
    deps = _deps(request)
    run = await deps.runtime_manager.run_store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    return run.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Session Management
# ---------------------------------------------------------------------------


@router.get("/sessions")
async def list_sessions(
    request: Request,
    agent_id: str | None = None,
) -> list[dict[str, Any]]:
    """List sessions with optional agent_id filter."""
    deps = _deps(request)
    sessions = await deps.runtime_manager.session_store.list_sessions(agent_id=agent_id)
    return [s.model_dump(mode="json") for s in sessions]


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request) -> dict[str, str]:
    """Delete a session by session_id."""
    deps = _deps(request)
    existing = await deps.runtime_manager.session_store.load(session_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
    await deps.runtime_manager.session_store.delete(session_id)
    return {"status": "deleted", "session_id": session_id}


# ---------------------------------------------------------------------------
# Tool Management
# ---------------------------------------------------------------------------


@router.get("/tools")
async def list_tools(request: Request) -> list[dict[str, Any]]:
    """List all registered tools with risk levels and owners."""
    deps = _deps(request)
    tools = deps.tool_registry.list_tools()
    return [
        {
            "name": t.name,
            "description": t.description,
            "risk_level": t.risk_level,
            "owner": t.owner,
            "permissions": t.permissions,
            "timeout_ms": t.timeout_ms,
        }
        for t in tools
    ]
