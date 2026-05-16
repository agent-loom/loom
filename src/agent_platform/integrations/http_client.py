"""Shared async HTTP client with connection pooling and retry logic."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from agent_platform.integrations.errors import IntegrationError, RetryableError

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {500, 502, 503, 504, 429}
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 0.5
DEFAULT_TIMEOUT = 30


class HttpClient:
    """Async HTTP client with connection pooling and exponential backoff retry.

    Designed to be composed into adapter classes. The underlying
    ``httpx.AsyncClient`` is created lazily and reused across requests
    for connection pooling.
    """

    def __init__(
        self,
        *,
        base_url: str,
        headers: dict[str, str],
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._headers = headers
        self._timeout = timeout
        self._max_retries = max_retries
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers=self._headers,
                timeout=self._timeout,
                transport=self._transport,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def request(
        self,
        method: str,
        path: str,
        *,
        error_cls: type[IntegrationError] = IntegrationError,
        **kwargs: Any,
    ) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                client = self._get_client()
                response = await client.request(method, path, **kwargs)
                if response.status_code in RETRYABLE_STATUS_CODES:
                    raise RetryableError(
                        f"{method} {path} returned {response.status_code}",
                        status_code=response.status_code,
                    )
                response.raise_for_status()
                return response.json()
            except RetryableError as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    delay = DEFAULT_BACKOFF_BASE * (2 ** (attempt - 1))
                    logger.warning(
                        "Retryable error on %s %s (attempt %d/%d), retrying in %.1fs: %s",
                        method, path, attempt, self._max_retries, delay, exc,
                    )
                    await asyncio.sleep(delay)
                    continue
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    delay = DEFAULT_BACKOFF_BASE * (2 ** (attempt - 1))
                    logger.warning(
                        "Timeout on %s %s (attempt %d/%d), retrying in %.1fs",
                        method, path, attempt, self._max_retries, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
            except httpx.HTTPStatusError as exc:
                raise error_cls(
                    f"{method} {path} failed: {exc.response.status_code} {exc.response.text[:200]}",
                    status_code=exc.response.status_code,
                ) from exc

        raise error_cls(f"{method} {path} failed after {self._max_retries} attempts: {last_exc}")
