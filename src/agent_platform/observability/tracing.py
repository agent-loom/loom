"""Optional OpenTelemetry tracing integration.

If ``opentelemetry-api`` is installed the module delegates to the real OTel
tracer; otherwise it provides lightweight no-op implementations so that
instrumentation call-sites never need to care about availability.
"""

from __future__ import annotations

import functools
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------

try:
    import opentelemetry.trace as _otel_trace  # type: ignore[import-untyped]

    OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover – tested via monkeypatch
    _otel_trace = None  # type: ignore[assignment]
    OTEL_AVAILABLE = False


# ---------------------------------------------------------------------------
# No-op fallbacks
# ---------------------------------------------------------------------------


class NoOpSpan:
    """Minimal span that silently discards all operations."""

    def set_attribute(self, key: str, value: Any) -> None:  # noqa: D401
        """No-op."""

    def set_status(self, status: Any, description: str | None = None) -> None:
        """No-op."""

    def record_exception(self, exception: BaseException, **kwargs: Any) -> None:
        """No-op."""

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        """No-op."""

    def __enter__(self) -> NoOpSpan:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        pass


class NoOpTracer:
    """Tracer that always returns :class:`NoOpSpan`."""

    def start_as_current_span(self, name: str, **kwargs: Any) -> NoOpSpan:
        """Return a *NoOpSpan* context manager."""
        return NoOpSpan()


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_tracer(name: str) -> Any:
    """Return an OpenTelemetry tracer or a :class:`NoOpTracer`.

    Parameters
    ----------
    name:
        Instrumentation scope name (e.g. ``"agent_platform.runtime"``).
    """
    if OTEL_AVAILABLE:
        return _otel_trace.get_tracer(name)  # type: ignore[union-attr]
    return NoOpTracer()


def traced(name: str | None = None):
    """Decorator that wraps an *async* function in an OTel span.

    When OpenTelemetry is not installed the decorator is effectively a
    pass-through with negligible overhead.

    Parameters
    ----------
    name:
        Span name.  Defaults to the qualified function name.
    """

    def decorator(fn):  # noqa: ANN001,ANN202
        span_name = name or fn.__qualname__
        tracer = get_tracer(fn.__module__)

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            with tracer.start_as_current_span(span_name) as span:
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status("ERROR", str(exc))
                    raise

        return wrapper

    return decorator


def configure_tracing(
    service_name: str,
    endpoint: str | None = None,
) -> None:
    """Bootstrap an OTel ``TracerProvider`` if the SDK is available.

    * When *endpoint* is given an OTLP/gRPC exporter is configured.
    * Otherwise a ``ConsoleSpanExporter`` is used.
    * If ``opentelemetry-sdk`` is not installed a warning is logged and the
      call is a no-op.
    """
    if not OTEL_AVAILABLE:
        logger.warning(
            "opentelemetry-api is not installed; tracing is disabled"
        )
        return

    try:
        from opentelemetry.sdk.resources import Resource  # type: ignore[import-untyped]
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import-untyped]
        from opentelemetry.sdk.trace.export import (  # type: ignore[import-untyped]
            BatchSpanProcessor,
            ConsoleSpanExporter,
        )
    except ImportError:
        logger.warning(
            "opentelemetry-sdk is not installed; tracing configuration skipped"
        )
        return

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # type: ignore[import-untyped]
                OTLPSpanExporter,
            )

            exporter = OTLPSpanExporter(endpoint=endpoint)
        except ImportError:
            logger.warning(
                "opentelemetry-exporter-otlp-proto-grpc is not installed; "
                "falling back to ConsoleSpanExporter"
            )
            exporter = ConsoleSpanExporter()
    else:
        exporter = ConsoleSpanExporter()

    provider.add_span_processor(BatchSpanProcessor(exporter))
    _otel_trace.set_tracer_provider(provider)  # type: ignore[union-attr]
    logger.info("OpenTelemetry tracing configured (service=%s)", service_name)
