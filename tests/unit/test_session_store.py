"""Tests for session enhancements — src/agent_platform/session/store.py"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_platform.domain.models import AgentSession, SessionMessage
from agent_platform.session.store import (
    FileSessionStore,
    build_scoped_session_id,
    compress_history,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def file_store(tmp_path: Path) -> FileSessionStore:
    return FileSessionStore(base_dir=tmp_path)


def _make_session(
    session_id: str = "sess_001",
    agent_id: str = "myj",
    **kwargs,
) -> AgentSession:
    return AgentSession(session_id=session_id, agent_id=agent_id, **kwargs)


# ---------------------------------------------------------------------------
# Tests — FileSessionStore save/load
# ---------------------------------------------------------------------------

def test_file_store_save_and_load(file_store: FileSessionStore):
    session = _make_session()
    session.add_message("user", "hello")
    file_store.save(session)

    loaded = file_store.load("sess_001")
    assert loaded is not None
    assert loaded.session_id == "sess_001"
    assert loaded.agent_id == "myj"
    assert len(loaded.history) == 1
    assert loaded.history[0].content == "hello"
    assert loaded.history[0].role == "user"


def test_file_store_load_nonexistent(file_store: FileSessionStore):
    result = file_store.load("nonexistent")
    assert result is None


def test_file_store_save_overwrites(file_store: FileSessionStore):
    session = _make_session()
    session.add_message("user", "first")
    file_store.save(session)

    session.add_message("assistant", "second")
    file_store.save(session)

    loaded = file_store.load("sess_001")
    assert loaded is not None
    assert len(loaded.history) == 2


# ---------------------------------------------------------------------------
# Tests — FileSessionStore list
# ---------------------------------------------------------------------------

def test_file_store_list_all(file_store: FileSessionStore):
    file_store.save(_make_session(session_id="s1", agent_id="myj"))
    file_store.save(_make_session(session_id="s2", agent_id="echo"))
    file_store.save(_make_session(session_id="s3", agent_id="myj"))

    sessions = file_store.list_sessions()
    assert len(sessions) == 3


def test_file_store_list_by_agent(file_store: FileSessionStore):
    file_store.save(_make_session(session_id="s1", agent_id="myj"))
    file_store.save(_make_session(session_id="s2", agent_id="echo"))
    file_store.save(_make_session(session_id="s3", agent_id="myj"))

    myj_sessions = file_store.list_sessions(agent_id="myj")
    assert len(myj_sessions) == 2
    assert all(s.agent_id == "myj" for s in myj_sessions)


def test_file_store_list_empty(file_store: FileSessionStore):
    sessions = file_store.list_sessions()
    assert sessions == []


# ---------------------------------------------------------------------------
# Tests — FileSessionStore delete
# ---------------------------------------------------------------------------

def test_file_store_delete(file_store: FileSessionStore):
    file_store.save(_make_session(session_id="s1"))
    file_store.delete("s1")
    assert file_store.load("s1") is None


def test_file_store_delete_nonexistent(file_store: FileSessionStore):
    # Should not raise
    file_store.delete("nonexistent")


# ---------------------------------------------------------------------------
# Tests — FileSessionStore persistence across instances
# ---------------------------------------------------------------------------

def test_file_store_persistence_across_instances(tmp_path: Path):
    store1 = FileSessionStore(base_dir=tmp_path)
    session = _make_session()
    session.add_message("user", "persist me")
    store1.save(session)

    # Create a new store instance pointing to the same directory
    store2 = FileSessionStore(base_dir=tmp_path)
    loaded = store2.load("sess_001")

    assert loaded is not None
    assert loaded.session_id == "sess_001"
    assert len(loaded.history) == 1
    assert loaded.history[0].content == "persist me"


def test_file_store_list_persists_across_instances(tmp_path: Path):
    store1 = FileSessionStore(base_dir=tmp_path)
    store1.save(_make_session(session_id="s1", agent_id="myj"))
    store1.save(_make_session(session_id="s2", agent_id="echo"))

    store2 = FileSessionStore(base_dir=tmp_path)
    sessions = store2.list_sessions()
    assert len(sessions) == 2


# ---------------------------------------------------------------------------
# Tests — FileSessionStore path safety
# ---------------------------------------------------------------------------

def test_file_store_sanitizes_session_id(file_store: FileSessionStore):
    """Session IDs with slashes or '..' are sanitized for safe file paths."""
    session = _make_session(session_id="tenant/store/../secret")
    file_store.save(session)

    # The file should be saved safely (no directory traversal)
    loaded = file_store.load("tenant/store/../secret")
    assert loaded is not None
    assert loaded.session_id == "tenant/store/../secret"


# ---------------------------------------------------------------------------
# Tests — compress_history
# ---------------------------------------------------------------------------

def _make_messages(count: int, content_len: int = 200, role: str = "user") -> list[SessionMessage]:
    from agent_platform.domain.models import _utc_now
    return [
        SessionMessage(role=role, content="x" * content_len, timestamp=_utc_now())
        for _ in range(count)
    ]


def test_compress_history_below_threshold_unchanged():
    """Short history should be returned unchanged."""
    messages = _make_messages(5, content_len=10)  # well below threshold
    result = compress_history(messages, threshold_tokens=12000)
    assert result == messages


def test_compress_history_above_threshold_compresses():
    """Long history should be compressed."""
    # 100 messages * 200 chars / 4 = 5000 tokens per message * ... too small
    # Need enough chars: threshold_tokens=100, chars_per_token=4 => 400 chars
    # 10 messages * 200 chars = 2000 chars / 4 = 500 tokens > 100 threshold
    messages = _make_messages(10, content_len=200)
    result = compress_history(messages, threshold_tokens=100)

    assert len(result) < len(messages)


def test_compress_history_preserves_system_messages():
    """System messages should always be preserved."""
    from agent_platform.domain.models import _utc_now
    system_msg = SessionMessage(
        role="system", content="You are a helpful agent.",
        timestamp=_utc_now(),
    )
    user_msgs = _make_messages(20, content_len=200)
    all_msgs = [system_msg] + user_msgs

    result = compress_history(all_msgs, threshold_tokens=100)

    # The original system message should still be present
    system_in_result = [m for m in result if m.role == "system" and "helpful agent" in m.content]
    assert len(system_in_result) == 1


def test_compress_history_keeps_recent_messages():
    """Recent non-system messages should be preserved."""
    messages = _make_messages(20, content_len=200)
    # Mark the last message with unique content
    messages[-1] = SessionMessage(
        role="user",
        content="UNIQUE_LAST_MESSAGE",
        timestamp=messages[-1].timestamp,
    )

    result = compress_history(messages, threshold_tokens=100)

    last_contents = [m.content for m in result]
    assert "UNIQUE_LAST_MESSAGE" in last_contents


def test_compress_history_adds_summary_message():
    """When compressing, a summary system message should be added."""
    messages = _make_messages(20, content_len=200)

    result = compress_history(messages, threshold_tokens=100)

    summary_msgs = [
        m for m in result
        if m.role == "system" and m.metadata.get("compressed") is True
    ]
    assert len(summary_msgs) == 1
    assert "Previous conversation summary" in summary_msgs[0].content


# ---------------------------------------------------------------------------
# Tests — build_scoped_session_id
# ---------------------------------------------------------------------------

def test_build_scoped_session_id_tenant_store_user():
    result = build_scoped_session_id(
        session_id="sess_001",
        scope="tenant_store_user",
        tenant_id="t1",
        store_id="s1",
        user_id="u1",
    )
    assert result == "t1:s1:u1:sess_001"


def test_build_scoped_session_id_tenant_store_user_partial():
    """Missing parts should be omitted from the scoped ID."""
    result = build_scoped_session_id(
        session_id="sess_001",
        scope="tenant_store_user",
        tenant_id="t1",
        store_id=None,
        user_id="u1",
    )
    assert result == "t1:u1:sess_001"


def test_build_scoped_session_id_tenant():
    result = build_scoped_session_id(
        session_id="sess_001",
        scope="tenant",
        tenant_id="t1",
    )
    assert result == "t1:sess_001"


def test_build_scoped_session_id_tenant_no_tenant_id():
    result = build_scoped_session_id(
        session_id="sess_001",
        scope="tenant",
    )
    assert result == ":sess_001"


def test_build_scoped_session_id_session_scope():
    """Default 'session' scope returns plain session_id."""
    result = build_scoped_session_id(
        session_id="sess_001",
        scope="session",
        tenant_id="t1",
        store_id="s1",
    )
    assert result == "sess_001"


def test_build_scoped_session_id_unknown_scope():
    """Unknown scopes should fall through to plain session_id."""
    result = build_scoped_session_id(
        session_id="sess_001",
        scope="global",
    )
    assert result == "sess_001"
