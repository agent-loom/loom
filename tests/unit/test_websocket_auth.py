"""Tests for WebSocket auth, backpressure, capacity — src/agent_platform/api/websocket.py"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_platform.api.auth import ApiKeyRecord
from agent_platform.api.websocket import (
    MAX_MESSAGE_SIZE,
    MAX_PENDING_MESSAGES,
    AgentWebSocketManager,
)


def _make_manager(**kwargs) -> AgentWebSocketManager:
    return AgentWebSocketManager(
        router=MagicMock(),
        runtime_manager=MagicMock(),
        **kwargs,
    )


def _make_key_record(**overrides) -> ApiKeyRecord:
    defaults = dict(
        key_id="k-1",
        key_hash="h",
        tenant_id="t",
        role="platform_admin",
        scopes=["chat", "deploy"],
        created_by="test",
    )
    defaults.update(overrides)
    return ApiKeyRecord(**defaults)


class TestConstruction:
    def test_default_max_connections(self):
        m = _make_manager()
        assert m._max_connections == 100

    def test_custom_max_connections(self):
        m = _make_manager(max_connections=50)
        assert m._max_connections == 50

    def test_accepts_api_key(self):
        m = _make_manager(api_key="my-key")
        assert m._api_key == "my-key"

    def test_accepts_key_store(self):
        store = MagicMock()
        m = _make_manager(key_store=store)
        assert m._key_store is store


class TestAuthentication:
    @pytest.mark.asyncio
    async def test_no_auth_configured_returns_anonymous(self):
        m = _make_manager(api_key=None, key_store=None)
        ws = MagicMock()
        result = await m._authenticate(ws)
        assert result is not None
        assert result["subject"] == "anonymous"
        assert result["role"] == "platform_admin"

    @pytest.mark.asyncio
    async def test_api_key_from_query_param(self):
        m = _make_manager(api_key="secret-key")
        ws = MagicMock()
        ws.query_params = {"token": "secret-key"}
        ws.headers = {}
        result = await m._authenticate(ws)
        assert result is not None
        assert result["subject"] == "api-key-user"

    @pytest.mark.asyncio
    async def test_api_key_from_bearer_header(self):
        m = _make_manager(api_key="bearer-key")
        ws = MagicMock()
        ws.query_params = {}
        ws.headers = {"authorization": "Bearer bearer-key"}
        result = await m._authenticate(ws)
        assert result is not None
        assert result["subject"] == "api-key-user"

    @pytest.mark.asyncio
    async def test_api_key_from_x_api_key_header(self):
        m = _make_manager(api_key="x-key")
        ws = MagicMock()
        ws.query_params = {}
        ws.headers = {"x-api-key": "x-key"}
        result = await m._authenticate(ws)
        assert result is not None

    @pytest.mark.asyncio
    async def test_invalid_key_closes_with_4001(self):
        m = _make_manager(api_key="correct-key")
        ws = MagicMock()
        ws.query_params = {"token": "wrong-key"}
        ws.headers = {}
        ws.close = AsyncMock()

        result = await m._authenticate(ws)
        assert result is None
        ws.close.assert_called_once_with(code=4001, reason="invalid credentials")

    @pytest.mark.asyncio
    async def test_no_token_closes_with_4001(self):
        m = _make_manager(api_key="needs-key")
        ws = MagicMock()
        ws.query_params = {}
        ws.headers = {}
        ws.close = AsyncMock()

        result = await m._authenticate(ws)
        assert result is None
        ws.close.assert_called_once_with(code=4001, reason="authentication required")

    @pytest.mark.asyncio
    async def test_key_store_sync_verify(self):
        store = MagicMock(spec=["verify"])
        record = _make_key_record()
        store.verify.return_value = record
        m = _make_manager(key_store=store)

        ws = MagicMock()
        ws.query_params = {"token": "store-key"}
        ws.headers = {}

        result = await m._authenticate(ws)
        assert result is not None
        assert result["subject"] == record.created_by
        assert result["role"] == record.role

    @pytest.mark.asyncio
    async def test_key_store_async_verify(self):
        store = MagicMock()
        store.verify_async = AsyncMock(return_value=_make_key_record())
        m = _make_manager(key_store=store)

        ws = MagicMock()
        ws.query_params = {"token": "async-key"}
        ws.headers = {}

        result = await m._authenticate(ws)
        assert result is not None
        assert result["key_id"] == "k-1"


class TestCapacity:
    @pytest.mark.asyncio
    async def test_capacity_limit_closes_connection(self):
        m = _make_manager(max_connections=2)
        m._connections = {"ws-1": MagicMock(), "ws-2": MagicMock()}

        ws = MagicMock()
        ws.close = AsyncMock()

        await m.handle(ws)
        ws.close.assert_called_once_with(code=1013, reason="server at capacity")


class TestProcessMessage:
    @pytest.mark.asyncio
    async def test_ping_returns_pong(self):
        m = _make_manager()
        result = await m._process_message(
            {"type": "ping"}, "ws-1", {"subject": "test"},
        )
        assert result == {"type": "pong"}

    @pytest.mark.asyncio
    async def test_unknown_type_returns_error(self):
        m = _make_manager()
        result = await m._process_message(
            {"type": "unknown"}, "ws-1", {"subject": "test"},
        )
        assert result["type"] == "error"
        assert result["error"]["code"] == "UNKNOWN_TYPE"


class TestCloseAll:
    @pytest.mark.asyncio
    async def test_close_all_clears_connections(self):
        m = _make_manager()
        ws1 = MagicMock()
        ws1.close = AsyncMock()
        ws2 = MagicMock()
        ws2.close = AsyncMock()

        m._connections = {"ws-1": ws1, "ws-2": ws2}
        m._pending = {"ws-1": 0, "ws-2": 3}

        await m.close_all("test shutdown")
        assert m.active_connections == 0
        assert len(m._pending) == 0
        ws1.close.assert_called_once_with(code=1001, reason="test shutdown")
        ws2.close.assert_called_once_with(code=1001, reason="test shutdown")

    @pytest.mark.asyncio
    async def test_close_all_handles_errors_gracefully(self):
        m = _make_manager()
        ws = MagicMock()
        ws.close = AsyncMock(side_effect=Exception("already closed"))
        m._connections = {"ws-err": ws}
        m._pending = {"ws-err": 0}

        await m.close_all()
        assert m.active_connections == 0


class TestConstants:
    def test_max_pending_messages_value(self):
        assert MAX_PENDING_MESSAGES == 32

    def test_max_message_size_value(self):
        assert MAX_MESSAGE_SIZE == 65536


class TestPendingTracking:
    def test_pending_dict_initialized_empty(self):
        m = _make_manager()
        assert m._pending == {}

    def test_active_connections_reflects_state(self):
        m = _make_manager()
        m._connections["a"] = MagicMock()
        m._connections["b"] = MagicMock()
        assert m.active_connections == 2
        del m._connections["a"]
        assert m.active_connections == 1
