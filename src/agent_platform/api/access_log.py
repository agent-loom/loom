"""Structured request/response logging middleware for audit trail."""

from __future__ import annotations

import logging
import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("agent_platform.access")

_SKIP_PATHS = {"/health", "/health/ready", "/metrics"}


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Logs structured JSON for every HTTP request/response pair."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _SKIP_PATHS:
            return await call_next(request)

        start = time.monotonic()
        response = await call_next(request)
        latency_ms = round((time.monotonic() - start) * 1000, 1)

        auth = getattr(request.state, "auth", None)
        request_id = getattr(request.state, "request_id", None)

        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "latency_ms": latency_ms,
                "request_id": request_id,
                "subject": auth.subject if auth else None,
                "tenant_id": auth.tenant_id if auth else None,
                "key_id": auth.key_id if auth else None,
                "client": (
                    request.client.host if request.client else None
                ),
            },
        )
        return response
