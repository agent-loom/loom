"""Tests for RuntimeManager aggregate query methods."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_platform.runtime.manager import RuntimeManager


def _make_manager(**kwargs) -> RuntimeManager:
    return RuntimeManager(**kwargs)


class TestListRuns:
    @pytest.mark.asyncio
    async def test_list_runs_delegates_to_store(self):
        store = MagicMock()
        store.list_runs = AsyncMock(return_value=["run-1", "run-2"])
        m = _make_manager(run_store=store)
        result = await m.list_runs(agent_id="echo")
        assert result == ["run-1", "run-2"]
        store.list_runs.assert_called_once_with(agent_id="echo", limit=100)

    @pytest.mark.asyncio
    async def test_list_runs_default_params(self):
        store = MagicMock()
        store.list_runs = AsyncMock(return_value=[])
        m = _make_manager(run_store=store)
        await m.list_runs()
        store.list_runs.assert_called_once_with(agent_id=None, limit=100)


class TestGetRun:
    @pytest.mark.asyncio
    async def test_get_run_delegates_to_store(self):
        store = MagicMock()
        store.get = AsyncMock(return_value="run-obj")
        m = _make_manager(run_store=store)
        result = await m.get_run("r-123")
        assert result == "run-obj"
        store.get.assert_called_once_with("r-123")


class TestListSessions:
    @pytest.mark.asyncio
    async def test_list_sessions_delegates_to_store(self):
        store = MagicMock()
        store.list_sessions = AsyncMock(return_value=["ses-1"])
        m = _make_manager(session_store=store)
        result = await m.list_sessions(agent_id="echo")
        assert result == ["ses-1"]
        store.list_sessions.assert_called_once_with(agent_id="echo")


class TestLoadSession:
    @pytest.mark.asyncio
    async def test_load_session_delegates_to_store(self):
        store = MagicMock()
        store.load = AsyncMock(return_value="session-obj")
        m = _make_manager(session_store=store)
        result = await m.load_session("ses-123")
        assert result == "session-obj"
        store.load.assert_called_once_with("ses-123")


class TestDeleteSession:
    @pytest.mark.asyncio
    async def test_delete_session_delegates_to_store(self):
        store = MagicMock()
        store.delete = AsyncMock()
        m = _make_manager(session_store=store)
        await m.delete_session("ses-123")
        store.delete.assert_called_once_with("ses-123")
