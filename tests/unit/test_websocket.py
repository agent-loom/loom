"""Tests for AgentWebSocketManager — src/agent_platform/api/websocket.py (lightweight)"""

from __future__ import annotations

from unittest.mock import MagicMock

from agent_platform.api.websocket import AgentWebSocketManager

# ---------------------------------------------------------------------------
# Tests — Construction
# ---------------------------------------------------------------------------

def test_websocket_manager_can_be_instantiated():
    """AgentWebSocketManager should be constructable with mocked dependencies."""
    mock_router = MagicMock()
    mock_runtime = MagicMock()

    manager = AgentWebSocketManager(
        router=mock_router,
        runtime_manager=mock_runtime,
    )

    assert manager.router is mock_router
    assert manager.runtime_manager is mock_runtime


def test_websocket_manager_initial_connections_empty():
    """New manager should have zero active connections."""
    manager = AgentWebSocketManager(
        router=MagicMock(),
        runtime_manager=MagicMock(),
    )

    assert manager.active_connections == 0


def test_websocket_manager_connections_dict_is_private():
    """Internal connections dict should exist and be empty."""
    manager = AgentWebSocketManager(
        router=MagicMock(),
        runtime_manager=MagicMock(),
    )

    assert isinstance(manager._connections, dict)
    assert len(manager._connections) == 0


def test_websocket_manager_has_handle_method():
    """Manager should have an async handle method."""
    manager = AgentWebSocketManager(
        router=MagicMock(),
        runtime_manager=MagicMock(),
    )

    assert callable(manager.handle)


def test_websocket_manager_has_process_message_method():
    """Manager should have a private _process_message method."""
    manager = AgentWebSocketManager(
        router=MagicMock(),
        runtime_manager=MagicMock(),
    )

    assert callable(manager._process_message)


def test_websocket_manager_active_connections_property():
    """active_connections property should reflect _connections length."""
    manager = AgentWebSocketManager(
        router=MagicMock(),
        runtime_manager=MagicMock(),
    )

    # Simulate adding connections
    manager._connections["ws_1"] = MagicMock()
    assert manager.active_connections == 1

    manager._connections["ws_2"] = MagicMock()
    assert manager.active_connections == 2

    del manager._connections["ws_1"]
    assert manager.active_connections == 1
