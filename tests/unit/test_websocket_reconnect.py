"""Tests for WebSocket reconnection support — replay buffer and seq numbers."""

from __future__ import annotations

from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_platform.api.websocket import (
    REPLAY_BUFFER_SIZE,
    AgentWebSocketManager,
)


def _make_manager(**kwargs) -> AgentWebSocketManager:
    return AgentWebSocketManager(
        router=MagicMock(),
        runtime_manager=MagicMock(),
        **kwargs,
    )


class TestReplayBuffer:
    def test_replay_buffer_initialized_empty(self):
        m = _make_manager()
        assert m._replay_buffers == {}

    def test_last_seq_initialized_empty(self):
        m = _make_manager()
        assert m._last_seq == {}

    def test_replay_buffer_size_constant(self):
        assert REPLAY_BUFFER_SIZE == 50

    @pytest.mark.asyncio
    async def test_replay_missed_sends_missed_messages(self):
        m = _make_manager()
        m._replay_buffers["ws-1"] = deque([
            {"type": "response", "seq": 1, "data": "a"},
            {"type": "response", "seq": 2, "data": "b"},
            {"type": "response", "seq": 3, "data": "c"},
        ], maxlen=REPLAY_BUFFER_SIZE)

        ws = MagicMock()
        ws.send_json = AsyncMock()

        await m._replay_missed(ws, "ws-1", last_seen_seq=1)

        ws.send_json.assert_called_once()
        replay_msg = ws.send_json.call_args[0][0]
        assert replay_msg["type"] == "replay"
        assert replay_msg["count"] == 2
        assert len(replay_msg["messages"]) == 2
        assert replay_msg["messages"][0]["seq"] == 2
        assert replay_msg["messages"][1]["seq"] == 3

    @pytest.mark.asyncio
    async def test_replay_missed_no_buffer_is_noop(self):
        m = _make_manager()
        ws = MagicMock()
        ws.send_json = AsyncMock()

        await m._replay_missed(ws, "ws-unknown", last_seen_seq=0)
        ws.send_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_replay_missed_all_seen_sends_nothing(self):
        m = _make_manager()
        m._replay_buffers["ws-1"] = deque([
            {"type": "response", "seq": 1},
            {"type": "response", "seq": 2},
        ], maxlen=REPLAY_BUFFER_SIZE)

        ws = MagicMock()
        ws.send_json = AsyncMock()

        await m._replay_missed(ws, "ws-1", last_seen_seq=5)
        ws.send_json.assert_not_called()


class TestSeqTracking:
    def test_seq_starts_at_zero(self):
        m = _make_manager()
        m._last_seq["ws-1"] = 0
        assert m._last_seq["ws-1"] == 0

    def test_seq_increments(self):
        m = _make_manager()
        m._last_seq["ws-1"] = 0
        m._last_seq["ws-1"] += 1
        assert m._last_seq["ws-1"] == 1
