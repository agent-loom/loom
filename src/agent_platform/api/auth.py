"""Authentication & authorization primitives for the Agent Platform API.

Provides:
- AuthIdentity / ApiKeyRecord models
- ApiKeyStore protocol + in-memory implementation
- require_role / require_scope FastAPI dependencies
- ROLE_PERMISSIONS constant
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class AuthIdentity(BaseModel):
    """Authenticated caller identity attached to ``request.state.auth``."""

    subject: str  # caller ID (user_id or service_id)
    tenant_id: str  # tenant
    role: str  # platform_admin | agent_developer | agent_operator | readonly
    scopes: list[str]  # operation scopes: ["chat", "deploy", "admin", "eval"]
    key_id: str | None = None  # API key ID for audit trail


class ApiKeyRecord(BaseModel):
    """Persisted metadata for a single API key."""

    key_id: str  # unique identifier for audit
    key_hash: str  # hash of the key, never store plaintext
    tenant_id: str  # bound to tenant
    role: str  # platform_admin | agent_developer | agent_operator | readonly
    scopes: list[str]  # allowed operation scopes
    created_by: str  # creator
    expires_at: datetime | None = None  # expiration time
    active: bool = True


# ---------------------------------------------------------------------------
# Key store protocol + in-memory implementation
# ---------------------------------------------------------------------------


@runtime_checkable
class ApiKeyStore(Protocol):
    """Minimal interface for API key verification."""

    def verify(self, key_plaintext: str) -> ApiKeyRecord | None:
        """Look up and verify an API key. Returns record if valid, None otherwise."""
        ...


class InMemoryApiKeyStore:
    """In-memory API key store using ``hashlib.sha256`` for hashing.

    Suitable for bootstrapping, dev, and testing.  For production use, swap in a
    persistent store backed by a database.
    """

    def __init__(self) -> None:
        self._keys: dict[str, ApiKeyRecord] = {}  # key_hash -> record

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _hash(key_plaintext: str) -> str:
        return hashlib.sha256(key_plaintext.encode()).hexdigest()

    # -- public API ----------------------------------------------------------

    def add_key(self, key_plaintext: str, record: ApiKeyRecord) -> None:
        """Register a key (for bootstrapping / dev)."""
        h = self._hash(key_plaintext)
        # Ensure the record's key_hash matches what we store.
        record = record.model_copy(update={"key_hash": h})
        self._keys[h] = record

    def verify(self, key_plaintext: str) -> ApiKeyRecord | None:
        """Look up a key by its SHA-256 hash; return ``None`` if invalid."""
        h = self._hash(key_plaintext)
        record = self._keys.get(h)
        if record is None:
            return None
        if not record.active:
            return None
        if record.expires_at is not None and record.expires_at <= datetime.now(UTC):
            return None
        return record


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


def require_role(*roles: str):
    """FastAPI dependency: verify caller has one of the required roles."""

    async def check(request: Request) -> AuthIdentity:
        auth = getattr(request.state, "auth", None)
        if auth is None:
            raise HTTPException(status_code=401, detail="not authenticated")
        if auth.role not in roles:
            raise HTTPException(status_code=403, detail="insufficient role")
        return auth

    return Depends(check)


def require_scope(scope: str):
    """FastAPI dependency: verify caller has the required scope."""

    async def check(request: Request) -> AuthIdentity:
        auth = getattr(request.state, "auth", None)
        if auth is None:
            raise HTTPException(status_code=401, detail="not authenticated")
        if scope not in auth.scopes:
            raise HTTPException(status_code=403, detail=f"missing scope: {scope}")
        return auth

    return Depends(check)


# ---------------------------------------------------------------------------
# Role → scope mapping
# ---------------------------------------------------------------------------

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "platform_admin": {"chat", "deploy", "admin", "eval", "register", "rollback"},
    "agent_developer": {"chat", "deploy:dev", "deploy:staging", "eval", "register"},
    "agent_operator": {"chat", "deploy:staging", "deploy:prod", "eval", "rollback"},
    "readonly": {"read"},
}
