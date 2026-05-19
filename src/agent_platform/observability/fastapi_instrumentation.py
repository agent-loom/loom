"""FastAPI OpenTelemetry instrumentation helper.

Wraps ``opentelemetry-instrumentation-fastapi`` in an import-guard so the
platform keeps working even when the optional ``otel`` extra is not installed.

Usage (inside ``create_app``)::

    from agent_platform.observability.fastapi_instrumentation import instrument_app
    instrument_app(app, service_name="agent-platform")
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal: try to import the instrumentation library
# ---------------------------------------------------------------------------

try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # type: ignore[import-untyped]

    _FASTAPI_INSTRUMENTOR_AVAILABLE = True
except ImportError:
    _FASTAPI_INSTRUMENTOR_AVAILABLE = False

try:
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor  # type: ignore[import-untyped]

    _HTTPX_INSTRUMENTOR_AVAILABLE = True
except ImportError:
    _HTTPX_INSTRUMENTOR_AVAILABLE = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def instrument_app(
    app: "FastAPI",
    service_name: str = "agent-platform",
    otlp_endpoint: str | None = None,
    excluded_urls: str | None = None,
) -> None:
    """Attach OpenTelemetry instrumentation to *app*.

    Parameters
    ----------
    app:
        The :class:`fastapi.FastAPI` instance to instrument.
    service_name:
        ``service.name`` attribute used in all emitted spans.
    otlp_endpoint:
        OTLP gRPC endpoint (e.g. ``http://localhost:4317``).  When *None* the
        endpoint is read from the ``OTEL_EXPORTER_OTLP_ENDPOINT`` env-var, and
        if that is also absent a ``ConsoleSpanExporter`` is used.
    excluded_urls:
        Comma-separated URL path patterns to exclude from tracing (passed
        directly to ``FastAPIInstrumentor``).  Defaults to health / metrics
        paths that are noisy and uninteresting.
    """
    from agent_platform.observability.tracing import configure_tracing

    # Resolve endpoint: explicit arg > env-var > None (→ console)
    resolved_endpoint = otlp_endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")

    # Bootstrap TracerProvider (no-op when opentelemetry-sdk is absent)
    configure_tracing(service_name=service_name, endpoint=resolved_endpoint)

    # Instrument HTTP client so outbound calls are traced automatically
    if _HTTPX_INSTRUMENTOR_AVAILABLE:
        HTTPXClientInstrumentor().instrument()
        logger.debug("HTTPX client instrumented for distributed tracing")
    else:
        logger.debug("opentelemetry-instrumentation-httpx not installed; skipping HTTPX tracing")

    # Instrument FastAPI routes
    if _FASTAPI_INSTRUMENTOR_AVAILABLE:
        _default_excluded = (
            "health,health/ready,health/live,metrics,docs,redoc,openapi.json"
        )
        FastAPIInstrumentor.instrument_app(
            app,
            excluded_urls=excluded_urls or _default_excluded,
            # Forward trace context from incoming ``traceparent`` / ``tracestate`` headers
            http_capture_headers_server_request=["x-request-id", "x-tenant-id"],
            http_capture_headers_server_response=["x-request-id"],
        )
        logger.info(
            "FastAPI OpenTelemetry instrumentation enabled "
            "(service=%s, endpoint=%s)",
            service_name,
            resolved_endpoint or "console",
        )
    else:
        logger.debug(
            "opentelemetry-instrumentation-fastapi not installed; "
            "request-level tracing is disabled"
        )


def uninstrument_app(app: "FastAPI") -> None:
    """Remove OTel instrumentation from *app* (useful in tests)."""
    if _FASTAPI_INSTRUMENTOR_AVAILABLE:
        FastAPIInstrumentor.uninstrument_app(app)
    if _HTTPX_INSTRUMENTOR_AVAILABLE:
        HTTPXClientInstrumentor().uninstrument()
