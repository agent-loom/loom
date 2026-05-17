"""PII and secret redaction utilities for logs and traces."""

from __future__ import annotations

import re

from agent_platform.domain.models import AgentRun, ToolCallTrace

# ---------------------------------------------------------------------------
# Compiled pattern tables
# ---------------------------------------------------------------------------

PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Chinese mobile phone numbers (11 digits starting with 1[3-9])
    (re.compile(r"\b(1[3-9]\d)\d{4}(\d{4})\b"), r"\1****\2"),
    # Chinese national ID card (18 digits)
    (re.compile(r"\b(\d{6})\d{8}(\d{3}[\dXx])\b"), r"\1****\2"),
    # Bank card numbers (16-19 digits, with word boundaries)
    (re.compile(r"\b(\d{4})\d{8,12}(\d{4})\b"), r"\1****\2"),
    # Email addresses
    (re.compile(r"([a-zA-Z0-9])[a-zA-Z0-9.]*@"), r"\1***@"),
    # International phone numbers (+country code)
    (re.compile(r"\+\d{1,3}[\s-]?(\d{2,3})[\s-]?\d{3,4}[\s-]?(\d{4})"), r"+***\1****\2"),
    # IPv4 addresses
    (re.compile(r"\b(\d{1,3})\.\d{1,3}\.\d{1,3}\.(\d{1,3})\b"), r"\1.*.*.\2"),
]

SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # API key formats (sk-xxx, key-xxx, token-xxx)
    (re.compile(r"(sk-|key-|token-)[a-zA-Z0-9]{8,}"), r"***SECRET***"),
    # Bearer tokens
    (re.compile(r"Bearer\s+[a-zA-Z0-9._-]{20,}"), r"Bearer ***SECRET***"),
]


class LogSanitizer:
    """Applies PII and secret redaction to arbitrary text."""

    @classmethod
    def sanitize(cls, text: str) -> str:
        """Apply all PII and SECRET patterns to *text*."""
        for pattern, replacement in PII_PATTERNS:
            text = pattern.sub(replacement, text)
        for pattern, replacement in SECRET_PATTERNS:
            text = pattern.sub(replacement, text)
        return text

    @classmethod
    def sanitize_with_secrets(
        cls,
        text: str,
        known_secrets: list[str] | None = None,
    ) -> str:
        """Sanitize *text* and additionally replace any *known_secrets*."""
        text = cls.sanitize(text)
        if known_secrets:
            for secret in known_secrets:
                if secret:
                    text = text.replace(secret, "***SECRET***")
        return text


class TraceSanitizer:
    """Sanitizes domain trace objects in-place and returns them."""

    @staticmethod
    def sanitize_tool_trace(trace: ToolCallTrace) -> ToolCallTrace:
        """Redact the *error* field of a single tool-call trace."""
        if trace.error:
            trace.error = LogSanitizer.sanitize(trace.error)
        return trace

    @staticmethod
    def sanitize_run(run: AgentRun) -> AgentRun:
        """Redact all tool_calls and error.message in an *AgentRun*."""
        for tc in run.tool_calls:
            TraceSanitizer.sanitize_tool_trace(tc)
        if run.error and run.error.message:
            run.error.message = LogSanitizer.sanitize(run.error.message)
        return run
