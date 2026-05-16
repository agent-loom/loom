"""Tests for the HITL approval gate mechanism."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from agent_platform.tools.approval import (
    ApprovalRequest,
    ApprovalStatus,
    AutoApproveGate,
    InMemoryApprovalGate,
)
from agent_platform.tools.executor import ToolExecutor
from agent_platform.tools.registry import ToolDefinition, ToolRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_approval_request(
    request_id: str = "req_001",
    tool_name: str = "dangerous_tool",
    risk_level: str = "high",
    ttl_seconds: int = 300,
) -> ApprovalRequest:
    return ApprovalRequest(
        request_id=request_id,
        tool_name=tool_name,
        risk_level=risk_level,
        payload={"key": "value"},
        agent_id="agent_1",
        run_id="run_1",
        reason="test approval",
        ttl_seconds=ttl_seconds,
    )


def _make_registry_and_executor(
    *,
    risk_level: str = "low",
    approval_gate=None,
) -> tuple[ToolRegistry, ToolExecutor]:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="test_tool",
            description="A test tool",
            handler=lambda p: {"result": "ok"},
            risk_level=risk_level,
        )
    )
    executor = ToolExecutor(registry=registry, approval_gate=approval_gate)
    return registry, executor


# ===========================================================================
# InMemoryApprovalGate tests
# ===========================================================================


class TestInMemoryApprovalGate:
    @pytest.mark.asyncio
    async def test_request_approval_returns_pending(self):
        gate = InMemoryApprovalGate()
        req = _make_approval_request()
        status = await gate.request_approval(req)
        assert status == ApprovalStatus.PENDING

    @pytest.mark.asyncio
    async def test_check_status_returns_pending(self):
        gate = InMemoryApprovalGate()
        req = _make_approval_request()
        await gate.request_approval(req)
        status = await gate.check_status(req.request_id)
        assert status == ApprovalStatus.PENDING

    @pytest.mark.asyncio
    async def test_resolve_approve(self):
        gate = InMemoryApprovalGate()
        req = _make_approval_request()
        await gate.request_approval(req)
        await gate.resolve(req.request_id, ApprovalStatus.APPROVED, "alice")
        status = await gate.check_status(req.request_id)
        assert status == ApprovalStatus.APPROVED

    @pytest.mark.asyncio
    async def test_resolve_reject(self):
        gate = InMemoryApprovalGate()
        req = _make_approval_request()
        await gate.request_approval(req)
        await gate.resolve(req.request_id, ApprovalStatus.REJECTED, "bob")
        status = await gate.check_status(req.request_id)
        assert status == ApprovalStatus.REJECTED

    @pytest.mark.asyncio
    async def test_resolve_already_resolved_raises(self):
        gate = InMemoryApprovalGate()
        req = _make_approval_request()
        await gate.request_approval(req)
        await gate.resolve(req.request_id, ApprovalStatus.APPROVED, "alice")
        with pytest.raises(ValueError, match="cannot resolve"):
            await gate.resolve(req.request_id, ApprovalStatus.REJECTED, "bob")

    @pytest.mark.asyncio
    async def test_resolve_unknown_request_raises(self):
        gate = InMemoryApprovalGate()
        with pytest.raises(LookupError):
            await gate.resolve("nonexistent", ApprovalStatus.APPROVED, "alice")

    @pytest.mark.asyncio
    async def test_list_pending(self):
        gate = InMemoryApprovalGate()
        req1 = _make_approval_request(request_id="req_a")
        req2 = _make_approval_request(request_id="req_b")
        await gate.request_approval(req1)
        await gate.request_approval(req2)
        # Resolve one
        await gate.resolve("req_a", ApprovalStatus.APPROVED, "alice")
        pending = await gate.list_pending()
        assert len(pending) == 1
        assert pending[0].request_id == "req_b"

    @pytest.mark.asyncio
    async def test_expiry(self):
        gate = InMemoryApprovalGate()
        req = _make_approval_request(ttl_seconds=0)
        # Manually set created_at in the past
        req.created_at = datetime.now(UTC) - timedelta(seconds=1)
        await gate.request_approval(req)
        status = await gate.check_status(req.request_id)
        assert status == ApprovalStatus.EXPIRED

    @pytest.mark.asyncio
    async def test_expired_not_in_pending_list(self):
        gate = InMemoryApprovalGate()
        req = _make_approval_request(ttl_seconds=0)
        req.created_at = datetime.now(UTC) - timedelta(seconds=1)
        await gate.request_approval(req)
        pending = await gate.list_pending()
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_auto_approve_flag(self):
        gate = InMemoryApprovalGate(auto_approve=True)
        req = _make_approval_request()
        status = await gate.request_approval(req)
        assert status == ApprovalStatus.APPROVED
        assert req.resolved_by == "auto"

    @pytest.mark.asyncio
    async def test_check_status_unknown_raises(self):
        gate = InMemoryApprovalGate()
        with pytest.raises(LookupError):
            await gate.check_status("nonexistent")


# ===========================================================================
# AutoApproveGate tests
# ===========================================================================


class TestAutoApproveGate:
    @pytest.mark.asyncio
    async def test_always_approves(self):
        gate = AutoApproveGate()
        req = _make_approval_request()
        status = await gate.request_approval(req)
        assert status == ApprovalStatus.APPROVED

    @pytest.mark.asyncio
    async def test_check_status_after_approval(self):
        gate = AutoApproveGate()
        req = _make_approval_request()
        await gate.request_approval(req)
        status = await gate.check_status(req.request_id)
        assert status == ApprovalStatus.APPROVED

    @pytest.mark.asyncio
    async def test_list_pending_empty(self):
        gate = AutoApproveGate()
        req = _make_approval_request()
        await gate.request_approval(req)
        pending = await gate.list_pending()
        assert len(pending) == 0


# ===========================================================================
# ToolExecutor integration tests
# ===========================================================================


class TestToolExecutorWithApprovalGate:
    @pytest.mark.asyncio
    async def test_low_risk_bypasses_approval(self):
        gate = InMemoryApprovalGate()
        _, executor = _make_registry_and_executor(
            risk_level="low", approval_gate=gate,
        )
        result = await executor.execute(
            "test_tool", {}, allowed_tools=["test_tool"],
        )
        assert result.trace.status == "success"
        # No approval requests should have been created
        pending = await gate.list_pending()
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_medium_risk_bypasses_approval(self):
        gate = InMemoryApprovalGate()
        _, executor = _make_registry_and_executor(
            risk_level="medium", approval_gate=gate,
        )
        result = await executor.execute(
            "test_tool", {}, allowed_tools=["test_tool"],
        )
        assert result.trace.status == "success"
        pending = await gate.list_pending()
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_high_risk_pending_returns_expired(self):
        """When gate returns PENDING and stays PENDING, executor treats it as expired."""
        gate = InMemoryApprovalGate()
        _, executor = _make_registry_and_executor(
            risk_level="high", approval_gate=gate,
        )
        result = await executor.execute(
            "test_tool", {}, allowed_tools=["test_tool"],
        )
        assert result.trace.status == "denied"
        assert result.trace.error == "APPROVAL_EXPIRED"

    @pytest.mark.asyncio
    async def test_high_risk_auto_approve_gate(self):
        gate = AutoApproveGate()
        _, executor = _make_registry_and_executor(
            risk_level="high", approval_gate=gate,
        )
        result = await executor.execute(
            "test_tool", {}, allowed_tools=["test_tool"],
        )
        assert result.trace.status == "success"
        assert result.output == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_critical_risk_auto_approve_gate(self):
        gate = AutoApproveGate()
        _, executor = _make_registry_and_executor(
            risk_level="critical", approval_gate=gate,
        )
        result = await executor.execute(
            "test_tool", {}, allowed_tools=["test_tool"],
        )
        assert result.trace.status == "success"

    @pytest.mark.asyncio
    async def test_high_risk_rejected(self):
        gate = InMemoryApprovalGate(auto_approve=False)
        _, executor = _make_registry_and_executor(
            risk_level="high", approval_gate=gate,
        )

        # We need to pre-resolve the approval to "rejected" -- but the
        # executor creates the request internally.  Instead, use a custom
        # gate that always rejects.
        class RejectGate:
            async def request_approval(self, request: ApprovalRequest) -> ApprovalStatus:
                return ApprovalStatus.REJECTED

            async def check_status(self, request_id: str) -> ApprovalStatus:
                return ApprovalStatus.REJECTED

            async def resolve(self, request_id, status, actor):
                pass

            async def list_pending(self):
                return []

        reject_gate = RejectGate()
        _, executor = _make_registry_and_executor(
            risk_level="high", approval_gate=reject_gate,
        )
        result = await executor.execute(
            "test_tool", {}, allowed_tools=["test_tool"],
        )
        assert result.trace.status == "denied"
        assert result.trace.error == "APPROVAL_DENIED"

    @pytest.mark.asyncio
    async def test_no_approval_gate_high_risk_proceeds(self):
        """Without an approval gate, even high-risk tools execute normally."""
        _, executor = _make_registry_and_executor(
            risk_level="high", approval_gate=None,
        )
        result = await executor.execute(
            "test_tool", {}, allowed_tools=["test_tool"],
        )
        assert result.trace.status == "success"


# ===========================================================================
# API endpoint tests
# ===========================================================================


class TestApprovalAPI:
    @pytest.fixture
    def app(self, monkeypatch):
        """Create a fresh app with HITL_ENABLED=true."""
        monkeypatch.setenv("HITL_ENABLED", "true")
        # Clear the cached settings so env var takes effect
        from agent_platform.config import get_settings
        get_settings.cache_clear()
        from agent_platform.api.app import create_app
        application = create_app()
        yield application
        get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_list_pending_empty(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/approvals/pending")
            assert resp.status_code == 200
            assert resp.json() == []

    @pytest.mark.asyncio
    async def test_resolve_not_found(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/approvals/nonexistent/resolve",
                json={"status": "approved", "actor": "alice"},
            )
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_create_and_resolve(self, app):
        gate = app.state.approval_gate
        # Manually create a pending request
        req = _make_approval_request(request_id="apr_test_001")
        await gate.request_approval(req)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # List pending
            resp = await client.get("/api/v1/approvals/pending")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["request_id"] == "apr_test_001"

            # Resolve it
            resp = await client.post(
                "/api/v1/approvals/apr_test_001/resolve",
                json={"status": "approved", "actor": "alice"},
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "approved"

            # Pending should now be empty
            resp = await client.get("/api/v1/approvals/pending")
            assert resp.status_code == 200
            assert resp.json() == []

    @pytest.mark.asyncio
    async def test_resolve_invalid_status(self, app):
        gate = app.state.approval_gate
        req = _make_approval_request(request_id="apr_test_002")
        await gate.request_approval(req)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/approvals/apr_test_002/resolve",
                json={"status": "invalid_status", "actor": "alice"},
            )
            assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_resolve_already_resolved(self, app):
        gate = app.state.approval_gate
        req = _make_approval_request(request_id="apr_test_003")
        await gate.request_approval(req)
        await gate.resolve("apr_test_003", ApprovalStatus.APPROVED, "alice")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/approvals/apr_test_003/resolve",
                json={"status": "rejected", "actor": "bob"},
            )
            assert resp.status_code == 409
