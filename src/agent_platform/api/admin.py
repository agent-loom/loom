"""Admin API 路由 — agent、会话、运行、工具、配额管理端点。"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from agent_platform.api.admin_deps import AdminDeps
from agent_platform.registry.registry import AgentNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def _deps(request: Request) -> AdminDeps:
    """从 app.state 中获取 Admin 依赖容器。"""
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

    runs = await deps.runtime_manager.list_runs(agent_id=agent_id, limit=20)
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
    try:
        await registry.get(agent_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_id}") from exc

    await registry.unregister(agent_id)
    return {"status": "deleted", "agent_id": agent_id}


# ---------------------------------------------------------------------------
# System Status
# ---------------------------------------------------------------------------


@router.get("/status")
async def system_status(request: Request) -> dict[str, Any]:
    """系统概览：agent 数量、部署、会话、运行数以及平台元信息。"""
    deps = _deps(request)

    agents = await deps.registry.list_agents()
    deployments = await deps.registry.list_deployments()
    sessions = await deps.runtime_manager.list_sessions()
    runs = await deps.runtime_manager.list_runs()

    # 平台版本
    platform_version: str = getattr(
        request.app, "version", "unknown"
    )

    # 运行时间（秒）
    started_at: float | None = getattr(
        request.app.state, "started_at", None
    )
    uptime_seconds: float | None = (
        round(time.time() - started_at, 1)
        if started_at is not None
        else None
    )

    # 中间件数量
    middleware_count = len(request.app.user_middleware)

    # 配额管理器是否已配置
    quota_configured = deps.quota_manager is not None

    return {
        "agents": len(agents),
        "deployments": len(deployments),
        "active_sessions": len(sessions),
        "total_runs": len(runs),
        "platform_version": platform_version,
        "uptime_seconds": uptime_seconds,
        "middleware_count": middleware_count,
        "quota_configured": quota_configured,
    }


# ---------------------------------------------------------------------------
# Prometheus Metrics
# ---------------------------------------------------------------------------


@router.get("/metrics")
async def prometheus_metrics(
    request: Request,
) -> PlainTextResponse:
    """返回 Prometheus text exposition 格式的指标数据。

    使用 text/plain; version=0.0.4 媒体类型以兼容 Prometheus 抓取协议。
    """
    deps = _deps(request)
    body = deps.metrics.to_prometheus()
    return PlainTextResponse(
        content=body,
        media_type="text/plain; version=0.0.4",
    )


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
    runs = await deps.runtime_manager.list_runs(agent_id=agent_id)
    results = [r.model_dump(mode="json") for r in runs]
    if status is not None:
        results = [r for r in results if r.get("status") == status]
    return results


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> dict[str, Any]:
    """Get run details by run_id."""
    deps = _deps(request)
    run = await deps.runtime_manager.get_run(run_id)
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
    sessions = await deps.runtime_manager.list_sessions(agent_id=agent_id)
    return [s.model_dump(mode="json") for s in sessions]


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request) -> dict[str, str]:
    """Delete a session by session_id."""
    deps = _deps(request)
    existing = await deps.runtime_manager.load_session(session_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
    await deps.runtime_manager.delete_session(session_id)
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


# ---------------------------------------------------------------------------
# API Key Management
# ---------------------------------------------------------------------------


class CreateKeyRequest(BaseModel):
    tenant_id: str = "default"
    role: str = "agent_developer"
    scopes: list[str] = Field(default_factory=lambda: ["chat", "eval"])
    expires_in_hours: int | None = None


class CreateKeyResponse(BaseModel):
    key_id: str
    api_key: str
    tenant_id: str
    role: str
    scopes: list[str]
    expires_at: str | None = None


@router.post("/keys")
async def create_api_key(
    body: CreateKeyRequest,
    request: Request,
) -> CreateKeyResponse:
    """Create a new API key and return the plaintext (shown only once)."""
    deps = _deps(request)
    if deps.key_store is None:
        raise HTTPException(status_code=501, detail="key store not configured")

    key_id = f"key_{uuid4().hex[:16]}"
    plaintext = f"ap_{uuid4().hex}"
    expires_at: datetime | None = None
    if body.expires_in_hours is not None:
        from datetime import timedelta
        expires_at = datetime.now(UTC) + timedelta(hours=body.expires_in_hours)

    auth = getattr(request.state, "auth", None)
    created_by = auth.subject if auth else "admin"

    await deps.key_store.add_key(
        plaintext,
        key_id=key_id,
        tenant_id=body.tenant_id,
        role=body.role,
        scopes=body.scopes,
        created_by=created_by,
        expires_at=expires_at,
    )
    return CreateKeyResponse(
        key_id=key_id,
        api_key=plaintext,
        tenant_id=body.tenant_id,
        role=body.role,
        scopes=body.scopes,
        expires_at=expires_at.isoformat() if expires_at else None,
    )


@router.get("/keys")
async def list_api_keys(
    request: Request,
    tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    """List all active API keys (hashes never exposed)."""
    deps = _deps(request)
    if deps.key_store is None:
        raise HTTPException(status_code=501, detail="key store not configured")
    return await deps.key_store.list_keys(tenant_id=tenant_id)


@router.delete("/keys/{key_id}")
async def revoke_api_key(
    key_id: str,
    request: Request,
) -> dict[str, Any]:
    """Revoke an API key by key_id (soft delete)."""
    deps = _deps(request)
    if deps.key_store is None:
        raise HTTPException(status_code=501, detail="key store not configured")
    revoked = await deps.key_store.revoke_key(key_id)
    if not revoked:
        raise HTTPException(status_code=404, detail=f"key not found: {key_id}")
    return {"status": "revoked", "key_id": key_id}


# ---------------------------------------------------------------------------
# Eval Runs
# ---------------------------------------------------------------------------


@router.get("/evals")
async def list_eval_runs(
    request: Request,
    agent_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List eval runs with optional agent_id filter."""
    deps = _deps(request)
    if deps.eval_repo is None:
        raise HTTPException(status_code=501, detail="eval repo not configured")
    return await deps.eval_repo.list_runs(agent_id=agent_id, limit=limit)


@router.get("/evals/compare")
async def compare_eval_runs(
    request: Request,
    run_id_a: str = Query(..., description="第一次运行的 ID"),
    run_id_b: str = Query(..., description="第二次运行的 ID"),
) -> dict[str, Any]:
    """对比两次评测运行的结果差异。"""
    deps = _deps(request)
    if deps.eval_repo is None:
        raise HTTPException(
            status_code=501,
            detail="eval repo not configured",
        )

    # 从全部运行记录中按 id 查找，因为 eval_repo 未提供 get_by_id 方法
    all_runs = await deps.eval_repo.list_runs(limit=10000)
    run_a: dict[str, Any] | None = None
    run_b: dict[str, Any] | None = None
    for r in all_runs:
        if r.get("id") == run_id_a:
            run_a = r
        if r.get("id") == run_id_b:
            run_b = r

    if run_a is None:
        raise HTTPException(
            status_code=404,
            detail=f"eval run not found: {run_id_a}",
        )
    if run_b is None:
        raise HTTPException(
            status_code=404,
            detail=f"eval run not found: {run_id_b}",
        )

    # 计算 pass_rate 变化
    rate_a = run_a.get("pass_rate", 0.0)
    rate_b = run_b.get("pass_rate", 0.0)
    delta = round(rate_b - rate_a, 4)

    # 对比各用例的通过/失败状态
    results_a = {
        c["id"]: c["passed"]
        for c in run_a.get("results", [])
    }
    results_b = {
        c["id"]: c["passed"]
        for c in run_b.get("results", [])
    }

    # 新增失败：在 A 中通过但在 B 中失败的用例
    new_failures = [
        cid for cid, passed in results_b.items()
        if not passed and results_a.get(cid, True)
    ]
    # 修复：在 A 中失败但在 B 中通过的用例
    fixed = [
        cid for cid, passed in results_b.items()
        if passed and not results_a.get(cid, False)
    ]

    return {
        "run_id_a": run_id_a,
        "run_id_b": run_id_b,
        "pass_rate_a": rate_a,
        "pass_rate_b": rate_b,
        "pass_rate_delta": delta,
        "new_failures": new_failures,
        "fixed": fixed,
    }


@router.get("/evals/{agent_id}/latest")
async def get_latest_eval(
    agent_id: str,
    request: Request,
) -> dict[str, Any]:
    """Get the most recent eval run for an agent."""
    deps = _deps(request)
    if deps.eval_repo is None:
        raise HTTPException(status_code=501, detail="eval repo not configured")
    result = await deps.eval_repo.get_latest(agent_id)
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"no eval runs found for {agent_id}",
        )
    return result


@router.post("/evals/{agent_id}/run")
async def trigger_eval_run(
    agent_id: str,
    request: Request,
) -> dict[str, Any]:
    """按需触发指定 agent 的评测运行并返回报告。"""
    deps = _deps(request)

    # 检查 eval_runner 是否可用
    if deps.eval_runner is None:
        raise HTTPException(
            status_code=501,
            detail="eval runner not configured",
        )

    # 从注册中心获取 agent spec
    try:
        spec = await deps.registry.get(agent_id)
    except AgentNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail=str(exc),
        ) from exc

    # 运行评测
    report = await deps.eval_runner.run_agent(spec)
    return report.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Deployment Audit
# ---------------------------------------------------------------------------


@router.get("/audit")
async def list_audit_events(
    request: Request,
    agent_id: str | None = None,
    channel: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List deployment audit events."""
    deps = _deps(request)
    events = await deps.audit_log.list_events(
        agent_id=agent_id, channel=channel, limit=limit,
    )
    return [e.model_dump(mode="json") for e in events]


# ---------------------------------------------------------------------------
# Tool Audit
# ---------------------------------------------------------------------------


@router.get("/tool-audit")
async def list_tool_audit_events(
    request: Request,
    tool_name: str | None = None,
    agent_id: str | None = None,
    run_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List tool call audit events."""
    deps = _deps(request)
    if deps.tool_audit_repo is None:
        raise HTTPException(status_code=501, detail="tool audit repo not configured")
    return await deps.tool_audit_repo.list_events(
        tool_name=tool_name,
        agent_id=agent_id,
        run_id=run_id,
        status=status,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# 租户配额管理
# ---------------------------------------------------------------------------


class SetQuotaRequest(BaseModel):
    """设置租户配额的请求体。"""
    tenant_id: str
    max_requests_per_day: int = 10000
    max_tokens_per_day: int = 5_000_000
    max_storage_mb: int = 1024
    max_agents: int = 50


@router.get("/quotas")
async def list_quotas(request: Request) -> list[dict[str, Any]]:
    """列出所有已设置的租户配额。"""
    deps = _deps(request)
    if deps.quota_manager is None:
        raise HTTPException(status_code=501, detail="quota manager not configured")
    return [q.model_dump() for q in deps.quota_manager.list_quotas()]


@router.post("/quotas")
async def set_quota(body: SetQuotaRequest, request: Request) -> dict[str, Any]:
    """设置或更新租户配额。"""
    deps = _deps(request)
    if deps.quota_manager is None:
        raise HTTPException(status_code=501, detail="quota manager not configured")
    from agent_platform.api.tenant_quota import TenantQuota
    quota = TenantQuota(
        tenant_id=body.tenant_id,
        max_requests_per_day=body.max_requests_per_day,
        max_tokens_per_day=body.max_tokens_per_day,
        max_storage_mb=body.max_storage_mb,
        max_agents=body.max_agents,
    )
    deps.quota_manager.set_quota(quota)
    return quota.model_dump()


@router.get("/quotas/{tenant_id}")
async def get_tenant_quota_report(
    tenant_id: str, request: Request,
) -> dict[str, Any]:
    """获取租户的配额使用报告，含利用率百分比。"""
    deps = _deps(request)
    if deps.quota_manager is None:
        raise HTTPException(status_code=501, detail="quota manager not configured")
    return deps.quota_manager.get_tenant_report(tenant_id)


@router.get("/quotas/{tenant_id}/check")
async def check_tenant_quota(
    tenant_id: str, request: Request,
) -> dict[str, Any]:
    """检查租户配额是否存在违规。"""
    deps = _deps(request)
    if deps.quota_manager is None:
        raise HTTPException(status_code=501, detail="quota manager not configured")
    violations = deps.quota_manager.check_all(tenant_id)
    return {
        "tenant_id": tenant_id,
        "ok": len(violations) == 0,
        "violations": violations,
    }


# ---------------------------------------------------------------------------
# 制品管理
# ---------------------------------------------------------------------------


@router.get("/artifacts")
async def list_artifacts(
    request: Request,
    agent_id: str | None = None,
) -> list[dict[str, Any]]:
    """列出所有制品，可按 agent_id 过滤。"""
    _deps(request)  # 确认依赖可用
    artifact_store = getattr(request.app.state, "artifact_store", None)
    if artifact_store is None:
        raise HTTPException(status_code=501, detail="artifact store not configured")
    artifacts = artifact_store.list_artifacts(agent_id=agent_id)
    return [a.model_dump(mode="json") for a in artifacts]


@router.get("/artifacts/{artifact_id}")
async def get_artifact_metadata(
    artifact_id: str, request: Request,
) -> dict[str, Any]:
    """获取制品元数据。"""
    artifact_store = getattr(request.app.state, "artifact_store", None)
    if artifact_store is None:
        raise HTTPException(status_code=501, detail="artifact store not configured")
    metadata = artifact_store.get_metadata(artifact_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail=f"artifact not found: {artifact_id}")
    return metadata.model_dump(mode="json")


@router.get("/artifacts/{artifact_id}/verify")
async def verify_artifact(
    artifact_id: str, request: Request,
) -> dict[str, Any]:
    """校验制品的 SHA-256 完整性。"""
    artifact_store = getattr(request.app.state, "artifact_store", None)
    if artifact_store is None:
        raise HTTPException(status_code=501, detail="artifact store not configured")
    metadata = artifact_store.get_metadata(artifact_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail=f"artifact not found: {artifact_id}")
    valid = artifact_store.verify_checksum(artifact_id)
    return {
        "artifact_id": artifact_id,
        "checksum_sha256": metadata.checksum_sha256,
        "manifest_sha256": metadata.manifest_sha256,
        "valid": valid,
    }
