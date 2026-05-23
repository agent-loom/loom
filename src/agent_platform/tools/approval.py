"""高危工具人机协同审批门禁：定义审批协议与模拟机制。

设计定位：
  能力/工具执行层 (Capabilities/Tool Execution Layer) 的安全确认卡口 (Approval Gate)。
  对应 docs/02-architecture/agent-platform-design.md 中的"人机协同审批拦截 (HITL)"机制。
  当 AI Agent 试图调用被判定为高风险 (HIGH/CRITICAL) 的工具（如修改敏感数据、执行系统指令、推送 Git 仓库）时，
  ToolExecutor 会向本模块注册一个 ApprovalRequest，挂起当前 Agent 的执行，
  等待外部审批人员（通过 REST/WebSocket API）显式下发批准 (APPROVED) 或驳回 (REJECTED) 决策，
  超时未决的请求自动流转为过期 (EXPIRED)。
"""

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
    """高危工具调用的人机协同审批请求模型。"""

    request_id: str
    tool_name: str
    # TODO Design Gap:
    # risk_level 字段目前是普通 str 类型，没有使用强类型约束 (例如 Literal 或 Enum)。
    # 导致在 ToolExecutor 中硬编码比对 `"high"` 或 `"critical"` 时，若输入发生了大小写错误 (如 "CRITICAL" 或 "High")，
    # 将默默漏过审批机制，应统一规范定义为强类型枚举。
    risk_level: str
    payload: dict[str, Any]
    agent_id: str | None = None
    run_id: str | None = None
    reason: str = ""
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    ttl_seconds: int = 300  # 默认 5 分钟超时


@runtime_checkable
class ApprovalGate(Protocol):
    """审批门禁组件的运行时 Protocol 契约定义。"""

    async def request_approval(self, request: ApprovalRequest) -> ApprovalStatus: ...

    async def check_status(self, request_id: str) -> ApprovalStatus: ...

    async def resolve(
        self, request_id: str, status: ApprovalStatus, actor: str
    ) -> None: ...

    async def list_pending(self) -> list[ApprovalRequest]: ...


class InMemoryApprovalGate:
    """内存态人机协同审批网关 (In-Memory Approval Gate)

    在开发与单元测试中充当审批桩，支持 TTL 自动过期与一键自动批准 (auto_approve)。
    """

    def __init__(self, *, auto_approve: bool = False) -> None:
        # TODO Design Gap:
        # 1. 持久化层级缺陷：在 `app.py` 生产模式中激活 HITL 流程时，系统默认直接使用了该内存态实现 `InMemoryApprovalGate`，
        #    缺少任何基于 Redis 或 PostgreSQL 的分布式持久化网关实现。
        #    这意味着如果平台重启，所有存留在内存中等待人工确认的挂起工具调用都将瞬间物理消失，导致 Agent 执行流僵死挂挂。
        # 2. TTL 过期扫描是惰性 (Lazy) 的，仅仅在主动调用 `check_status` 或 `list_pending` 时才会对过期数据改写状态。
        #    如果缺乏外部轮询，过期的请求将永远残留在 _requests 内存映射中。
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
        # 惰性检查过期
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
            # 惰性检查过期
            if req.status == ApprovalStatus.PENDING:
                elapsed = (datetime.now(UTC) - req.created_at).total_seconds()
                if elapsed >= req.ttl_seconds:
                    req.status = ApprovalStatus.EXPIRED
                    req.resolved_at = datetime.now(UTC)
                else:
                    pending.append(req)
        return pending


class AutoApproveGate:
    """自动放行审批门禁桩 (Auto-Approve Gate)

    用于完全免人工干预的非生产验证场景。
    """

    def __init__(self) -> None:
        # TODO Design Gap:
        # 1. 架构冗余：本类基本等效于 `InMemoryApprovalGate(auto_approve=True)`，代码有不必要的重复。
        # 2. 状态改写越轨漏洞：本类的 resolve 方法在实现时并未检查 `req.status != ApprovalStatus.PENDING`，
        #    这允许调用方直接覆写已经被 auto_approve 决断后的审批单状态（例如将 APPROVED 的单子改写为 REJECTED），
        #    在流程审计上是一个潜在的绕过风险。
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
