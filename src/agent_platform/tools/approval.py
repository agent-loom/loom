"""Human-in-the-loop approval gate for high-risk tool calls."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalRequest(BaseModel):
    """A request for human approval before executing a high-risk tool."""

    request_id: str
    tool_name: str
    risk_level: str
    payload: dict[str, Any]
    agent_id: str | None = None
    run_id: str | None = None
    reason: str = ""
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    ttl_seconds: int = 300  # 5 minute default


@runtime_checkable
class ApprovalGate(Protocol):
    """Protocol for approval gate implementations."""

    async def request_approval(self, request: ApprovalRequest) -> ApprovalStatus: ...

    async def check_status(self, request_id: str) -> ApprovalStatus: ...

    async def resolve(
        self, request_id: str, status: ApprovalStatus, actor: str
    ) -> None: ...

    async def list_pending(self) -> list[ApprovalRequest]: ...


class InMemoryApprovalGate:
    """In-memory approval gate for testing and development.

    Stores approval requests in a dictionary.  Supports auto-expiry based
    on ``ttl_seconds`` and an ``auto_approve`` flag that instantly approves
    every request (useful in integration tests).
    """

    def __init__(self, *, auto_approve: bool = False) -> None:
        self._requests: dict[str, ApprovalRequest] = {}
        self.auto_approve = auto_approve

    async def request_approval(self, request: ApprovalRequest) -> ApprovalStatus:
        if self.auto_approve:
            request.status = ApprovalStatus.APPROVED
            request.resolved_at = datetime.now(UTC)
            request.resolved_by = "auto"
            self._requests[request.request_id] = request
            return ApprovalStatus.APPROVED

        self._requests[request.request_id] = request
        return ApprovalStatus.PENDING

    async def check_status(self, request_id: str) -> ApprovalStatus:
        req = self._requests.get(request_id)
        if req is None:
            raise LookupError(f"approval request not found: {request_id}")
        # Check for expiry
        if req.status == ApprovalStatus.PENDING:
            elapsed = (datetime.now(UTC) - req.created_at).total_seconds()
            if elapsed >= req.ttl_seconds:
                req.status = ApprovalStatus.EXPIRED
                req.resolved_at = datetime.now(UTC)
        return req.status

    async def resolve(
        self, request_id: str, status: ApprovalStatus, actor: str
    ) -> None:
        req = self._requests.get(request_id)
        if req is None:
            raise LookupError(f"approval request not found: {request_id}")
        if req.status != ApprovalStatus.PENDING:
            raise ValueError(
                f"cannot resolve request {request_id}: "
                f"current status is {req.status}"
            )
        req.status = status
        req.resolved_at = datetime.now(UTC)
        req.resolved_by = actor

    async def list_pending(self) -> list[ApprovalRequest]:
        pending: list[ApprovalRequest] = []
        for req in self._requests.values():
            # Expire stale requests on the fly
            if req.status == ApprovalStatus.PENDING:
                elapsed = (datetime.now(UTC) - req.created_at).total_seconds()
                if elapsed >= req.ttl_seconds:
                    req.status = ApprovalStatus.EXPIRED
                    req.resolved_at = datetime.now(UTC)
                else:
                    pending.append(req)
        return pending


class AutoApproveGate:
    """Approval gate that automatically approves every request.

    Intended for development and test environments where human-in-the-loop
    approval is not desired.
    """

    def __init__(self) -> None:
        self._requests: dict[str, ApprovalRequest] = {}

    async def request_approval(self, request: ApprovalRequest) -> ApprovalStatus:
        request.status = ApprovalStatus.APPROVED
        request.resolved_at = datetime.now(UTC)
        request.resolved_by = "auto"
        self._requests[request.request_id] = request
        return ApprovalStatus.APPROVED

    async def check_status(self, request_id: str) -> ApprovalStatus:
        req = self._requests.get(request_id)
        if req is None:
            raise LookupError(f"approval request not found: {request_id}")
        return req.status

    async def resolve(
        self, request_id: str, status: ApprovalStatus, actor: str
    ) -> None:
        req = self._requests.get(request_id)
        if req is None:
            raise LookupError(f"approval request not found: {request_id}")
        req.status = status
        req.resolved_at = datetime.now(UTC)
        req.resolved_by = actor

    async def list_pending(self) -> list[ApprovalRequest]:
        return [
            r
            for r in self._requests.values()
            if r.status == ApprovalStatus.PENDING
        ]
