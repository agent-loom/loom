"""请求级审计上下文，基于 contextvars 实现协程安全传递。"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass


@dataclass(frozen=True)
class AuditContext:
    """不可变审计上下文，携带 request_id、操作人和租户信息。"""
    request_id: str | None = None
    actor: str = "system"
    tenant_id: str | None = None


_audit_ctx: contextvars.ContextVar[AuditContext] = contextvars.ContextVar(
    "audit_ctx",
)

_DEFAULT_CTX = AuditContext()


def get_audit_context() -> AuditContext:
    """获取当前协程的审计上下文，未设置时返回默认值。"""
    return _audit_ctx.get(_DEFAULT_CTX)


def set_audit_context(ctx: AuditContext) -> None:
    """设置当前协程的审计上下文。"""
    _audit_ctx.set(ctx)
