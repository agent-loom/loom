from __future__ import annotations

import contextvars
from dataclasses import dataclass


@dataclass(frozen=True)
class AuditContext:
    request_id: str | None = None
    actor: str = "system"
    tenant_id: str | None = None


_audit_ctx: contextvars.ContextVar[AuditContext] = contextvars.ContextVar(
    "audit_ctx",
)

_DEFAULT_CTX = AuditContext()


def get_audit_context() -> AuditContext:
    return _audit_ctx.get(_DEFAULT_CTX)


def set_audit_context(ctx: AuditContext) -> None:
    _audit_ctx.set(ctx)
