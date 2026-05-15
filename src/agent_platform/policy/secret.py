"""Secure secret management: backends, resolver, and validation helpers."""

from __future__ import annotations

import copy
import os
import re
from typing import Any, Protocol, runtime_checkable


class SecretNotFoundError(KeyError):
    """Raised when a referenced secret cannot be resolved."""


@runtime_checkable
class SecretBackend(Protocol):
    def get(self, key: str, *, tenant_id: str | None = None) -> str | None: ...
    def exists(self, key: str, *, tenant_id: str | None = None) -> bool: ...


class EnvSecretBackend:
    """Secret backend that reads values from environment variables.

    When *tenant_id* is provided the backend first tries a tenant-scoped
    variable ``{TENANT_ID}_{KEY}`` (with hyphens replaced by underscores and
    upper-cased) before falling back to the plain ``KEY``.
    """

    def get(self, key: str, *, tenant_id: str | None = None) -> str | None:
        if tenant_id is not None:
            scoped_key = f"{tenant_id.replace('-', '_').upper()}_{key}"
            value = os.environ.get(scoped_key)
            if value is not None:
                return value
        return os.environ.get(key)

    def exists(self, key: str, *, tenant_id: str | None = None) -> bool:
        return self.get(key, tenant_id=tenant_id) is not None


class SecretResolver:
    """Resolve ``$secret:KEY`` references inside configuration dicts."""

    SECRET_PATTERN = re.compile(r"^\$secret:([A-Z0-9_]+(?:/[A-Z0-9_]+)?)$")

    def __init__(self, backend: SecretBackend) -> None:
        self._backend = backend

    def resolve_config(
        self,
        config: dict[str, Any],
        *,
        tenant_id: str | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        """Recursively resolve ``$secret:`` references in *config*.

        Returns ``(resolved_config, secret_values)`` where *secret_values*
        is the list of plaintext values that were injected.  Callers should
        use this list for runtime output-scanning and **discard it** once
        the request is complete.
        """
        secrets: list[str] = []
        resolved = self._walk(config, tenant_id=tenant_id, secrets=secrets)
        return resolved, secrets

    # ------------------------------------------------------------------

    def _walk(
        self,
        node: Any,
        *,
        tenant_id: str | None,
        secrets: list[str],
    ) -> Any:
        if isinstance(node, dict):
            return {
                k: self._walk(v, tenant_id=tenant_id, secrets=secrets)
                for k, v in node.items()
            }
        if isinstance(node, list):
            return [
                self._walk(item, tenant_id=tenant_id, secrets=secrets)
                for item in node
            ]
        if isinstance(node, str):
            match = self.SECRET_PATTERN.match(node)
            if match:
                return self._resolve_ref(
                    match.group(1), tenant_id=tenant_id, secrets=secrets,
                )
        return copy.deepcopy(node) if isinstance(node, (dict, list)) else node

    def _resolve_ref(
        self,
        ref: str,
        *,
        tenant_id: str | None,
        secrets: list[str],
    ) -> str:
        if "/" in ref:
            path_tenant, key = ref.split("/", 1)
            effective_tenant = path_tenant
        else:
            key = ref
            effective_tenant = tenant_id

        value = self._backend.get(key, tenant_id=effective_tenant)
        if value is None:
            raise SecretNotFoundError(ref)
        secrets.append(value)
        return value


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_SECRET_PREFIX = "$secret:"


def find_secret_refs(data: Any) -> list[str]:
    """Recursively find all ``$secret:...`` strings in a nested dict/list."""
    refs: list[str] = []
    _collect_refs(data, refs)
    return refs


def _collect_refs(node: Any, refs: list[str]) -> None:
    if isinstance(node, dict):
        for v in node.values():
            _collect_refs(v, refs)
    elif isinstance(node, list):
        for item in node:
            _collect_refs(item, refs)
    elif isinstance(node, str) and node.startswith(_SECRET_PREFIX):
        refs.append(node)


_VALID_SECRET_REF = re.compile(r"^\$secret:[A-Z0-9_]+(?:/[A-Z0-9_]+)?$")


def validate_secret_refs(data: Any) -> list[str]:
    """Validate format of all ``$secret:`` references.

    Returns a list of human-readable error messages (empty when valid).
    """
    errors: list[str] = []
    for ref in find_secret_refs(data):
        if not _VALID_SECRET_REF.fullmatch(ref):
            errors.append(f"invalid secret reference format: {ref!r}")
    return errors
