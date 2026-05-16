"""Langfuse integration for LLM-specific observability.

Provides tracing of LLM calls, prompt/completion pairs, token usage,
and latency metrics through the Langfuse platform.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_langfuse_available = False
try:
    from langfuse import Langfuse
    from langfuse.callback import CallbackHandler
    _langfuse_available = True
except ImportError:
    Langfuse = None
    CallbackHandler = None


class LangfuseTracer:
    """Wraps the Langfuse SDK for agent platform observability.

    When langfuse is not installed, all methods are safe no-ops.
    """

    def __init__(
        self,
        *,
        public_key: str | None = None,
        secret_key: str | None = None,
        host: str | None = None,
    ):
        self._client = None
        self._enabled = False

        if not _langfuse_available:
            logger.info("Langfuse SDK not installed — LLM tracing disabled")
            return

        if not public_key or not secret_key:
            logger.info("Langfuse keys not configured — LLM tracing disabled")
            return

        try:
            self._client = Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                host=host or "https://cloud.langfuse.com",
            )
            self._enabled = True
            logger.info("Langfuse tracer initialized (host=%s)", host)
        except Exception:
            logger.warning("Failed to initialize Langfuse client", exc_info=True)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def trace(
        self,
        *,
        name: str,
        session_id: str | None = None,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ):
        if not self._enabled or self._client is None:
            return _NoOpTrace()
        try:
            return self._client.trace(
                name=name,
                session_id=session_id,
                user_id=user_id,
                metadata=metadata or {},
                tags=tags or [],
            )
        except Exception:
            logger.debug("Failed to create Langfuse trace", exc_info=True)
            return _NoOpTrace()

    def generation(
        self,
        trace,
        *,
        name: str,
        model: str,
        input: str | dict | None = None,
        output: str | dict | None = None,
        usage: dict[str, int] | None = None,
        metadata: dict[str, Any] | None = None,
        latency_ms: int | None = None,
    ):
        if not self._enabled or trace is None or isinstance(trace, _NoOpTrace):
            return
        try:
            gen = trace.generation(
                name=name,
                model=model,
                input=input,
                output=output,
                usage=usage,
                metadata=metadata or {},
            )
            if latency_ms is not None and hasattr(gen, "end"):
                gen.end()
        except Exception:
            logger.debug("Failed to record Langfuse generation", exc_info=True)

    def span(
        self,
        trace,
        *,
        name: str,
        input: Any = None,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
    ):
        if not self._enabled or trace is None or isinstance(trace, _NoOpTrace):
            return _NoOpSpan()
        try:
            return trace.span(
                name=name,
                input=input,
                output=output,
                metadata=metadata or {},
            )
        except Exception:
            logger.debug("Failed to create Langfuse span", exc_info=True)
            return _NoOpSpan()

    def score(
        self,
        trace,
        *,
        name: str,
        value: float,
        comment: str | None = None,
    ):
        if not self._enabled or trace is None or isinstance(trace, _NoOpTrace):
            return
        try:
            trace.score(name=name, value=value, comment=comment)
        except Exception:
            logger.debug("Failed to record Langfuse score", exc_info=True)

    def flush(self) -> None:
        if self._enabled and self._client is not None:
            try:
                self._client.flush()
            except Exception:
                logger.debug("Failed to flush Langfuse", exc_info=True)

    async def shutdown(self) -> None:
        self.flush()
        if self._client is not None:
            try:
                self._client.shutdown()
            except Exception:
                pass

    async def close(self) -> None:
        await self.shutdown()

    async def health_check(self) -> bool:
        if not self._enabled:
            return False
        try:
            self._client.auth_check()
            return True
        except Exception:
            return False


class _NoOpTrace:
    def generation(self, **kwargs):
        return _NoOpSpan()

    def span(self, **kwargs):
        return _NoOpSpan()

    def score(self, **kwargs):
        pass


class _NoOpSpan:
    def end(self, **kwargs):
        pass

    def update(self, **kwargs):
        pass
