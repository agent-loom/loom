"""Tests for SqlApiKeyStore — src/agent_platform/persistence/sql.py"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.persistence.sql import SqlApiKeyStore
from agent_platform.storage.base import Base


@pytest.fixture()
def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return async_sessionmaker(engine, expire_on_commit=False)

    sf = asyncio.get_event_loop().run_until_complete(_setup())
    yield sf
    asyncio.get_event_loop().run_until_complete(engine.dispose())


@pytest.fixture()
def store(session_factory):
    return SqlApiKeyStore(session_factory)


class TestAddAndVerify:
    @pytest.mark.asyncio
    async def test_add_and_verify_returns_record(self, store):
        await store.add_key(
            "my-secret-key",
            key_id="k-1",
            tenant_id="tenant-a",
            role="platform_admin",
            created_by="test",
        )
        record = await store.verify_async("my-secret-key")
        assert record is not None
        assert record.key_id == "k-1"
        assert record.tenant_id == "tenant-a"
        assert record.role == "platform_admin"

    @pytest.mark.asyncio
    async def test_verify_wrong_key_returns_none(self, store):
        await store.add_key("correct-key", key_id="k-1")
        result = await store.verify_async("wrong-key")
        assert result is None

    @pytest.mark.asyncio
    async def test_verify_returns_scopes(self, store):
        await store.add_key(
            "scoped-key",
            key_id="k-scoped",
            scopes=["chat", "eval"],
        )
        record = await store.verify_async("scoped-key")
        assert record is not None
        assert "chat" in record.scopes
        assert "eval" in record.scopes

    @pytest.mark.asyncio
    async def test_default_scopes_when_none(self, store):
        await store.add_key("default-scopes", key_id="k-default")
        record = await store.verify_async("default-scopes")
        assert record is not None
        assert len(record.scopes) > 0


class TestExpiry:
    @pytest.mark.asyncio
    async def test_expired_key_returns_none(self, store):
        past = datetime.now(UTC) - timedelta(hours=1)
        await store.add_key(
            "expired-key",
            key_id="k-exp",
            expires_at=past,
        )
        result = await store.verify_async("expired-key")
        assert result is None

    @pytest.mark.asyncio
    async def test_future_expiry_still_valid(self, store):
        future = datetime.now(UTC) + timedelta(hours=24)
        await store.add_key(
            "valid-key",
            key_id="k-valid",
            expires_at=future,
        )
        record = await store.verify_async("valid-key")
        assert record is not None
        assert record.key_id == "k-valid"

    @pytest.mark.asyncio
    async def test_no_expiry_is_valid(self, store):
        await store.add_key("no-exp-key", key_id="k-noexp")
        record = await store.verify_async("no-exp-key")
        assert record is not None


class TestRevocation:
    @pytest.mark.asyncio
    async def test_revoke_existing_key(self, store):
        await store.add_key("revocable-key", key_id="k-rev")
        revoked = await store.revoke_key("k-rev")
        assert revoked is True
        result = await store.verify_async("revocable-key")
        assert result is None

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_key(self, store):
        revoked = await store.revoke_key("k-nope")
        assert revoked is False

    @pytest.mark.asyncio
    async def test_revoked_key_not_in_list(self, store):
        await store.add_key("list-key", key_id="k-list")
        await store.revoke_key("k-list")
        keys = await store.list_keys()
        assert all(k["key_id"] != "k-list" for k in keys)


class TestListKeys:
    @pytest.mark.asyncio
    async def test_list_returns_all_active(self, store):
        await store.add_key("key-1", key_id="k-1", tenant_id="t1")
        await store.add_key("key-2", key_id="k-2", tenant_id="t2")
        keys = await store.list_keys()
        assert len(keys) == 2

    @pytest.mark.asyncio
    async def test_list_filters_by_tenant(self, store):
        await store.add_key("key-1", key_id="k-1", tenant_id="t1")
        await store.add_key("key-2", key_id="k-2", tenant_id="t2")
        keys = await store.list_keys(tenant_id="t1")
        assert len(keys) == 1
        assert keys[0]["key_id"] == "k-1"

    @pytest.mark.asyncio
    async def test_list_key_fields(self, store):
        await store.add_key(
            "field-key",
            key_id="k-fields",
            tenant_id="t-f",
            role="readonly",
            created_by="admin",
        )
        keys = await store.list_keys()
        assert len(keys) == 1
        k = keys[0]
        assert k["key_id"] == "k-fields"
        assert k["tenant_id"] == "t-f"
        assert k["role"] == "readonly"
        assert k["created_by"] == "admin"


class TestSyncVerifyRaises:
    def test_sync_verify_raises_runtime_error(self, store):
        with pytest.raises(RuntimeError, match="verify_async"):
            store.verify("any-key")
