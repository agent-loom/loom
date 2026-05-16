"""Integration error hierarchy for SCM and project management adapters."""

from __future__ import annotations


class IntegrationError(Exception):
    """Base error for all external integration failures."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ScmError(IntegrationError):
    """SCM adapter operation failed (GitLab, GitHub, etc.)."""


class PlaneError(IntegrationError):
    """Plane API operation failed."""


class RetryableError(IntegrationError):
    """Transient error that may succeed on retry (5xx, timeout)."""
