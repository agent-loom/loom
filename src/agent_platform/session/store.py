from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Protocol, runtime_checkable

from agent_platform.domain.models import AgentSession, SessionMessage

logger = logging.getLogger(__name__)


@runtime_checkable
class SessionStore(Protocol):
    def load(self, session_id: str) -> AgentSession | None: ...
    def save(self, session: AgentSession) -> None: ...
    def delete(self, session_id: str) -> None: ...
    def list_sessions(self, agent_id: str | None = None) -> list[AgentSession]: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._store: dict[str, AgentSession] = {}

    def load(self, session_id: str) -> AgentSession | None:
        return self._store.get(session_id)

    def save(self, session: AgentSession) -> None:
        self._store[session.session_id] = session

    def delete(self, session_id: str) -> None:
        self._store.pop(session_id, None)

    def list_sessions(self, agent_id: str | None = None) -> list[AgentSession]:
        sessions = list(self._store.values())
        if agent_id:
            sessions = [s for s in sessions if s.agent_id == agent_id]
        return sessions


class FileSessionStore:
    """File-based session persistence. Each session is a JSON file."""

    def __init__(self, base_dir: str | Path = ".sessions") -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        safe_id = session_id.replace("/", "_").replace("..", "_")
        return self._base_dir / f"{safe_id}.json"

    def load(self, session_id: str) -> AgentSession | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return AgentSession.model_validate(data)
        except Exception:
            logger.exception("failed to load session %s", session_id)
            return None

    def save(self, session: AgentSession) -> None:
        path = self._path(session.session_id)
        data = session.model_dump(mode="json")
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def delete(self, session_id: str) -> None:
        path = self._path(session_id)
        if path.exists():
            path.unlink()

    def list_sessions(self, agent_id: str | None = None) -> list[AgentSession]:
        sessions: list[AgentSession] = []
        for path in self._base_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                session = AgentSession.model_validate(data)
                if agent_id and session.agent_id != agent_id:
                    continue
                sessions.append(session)
            except Exception:
                logger.warning("skipping corrupt session file: %s", path)
        return sessions


def session_id_with_scope(session: AgentSession) -> str:
    """Generate a scoped session key based on session scope metadata.
    Falls back to plain session_id for backward compat."""
    return session.session_id


def compress_history(
    history: list[SessionMessage],
    threshold_tokens: int = 12000,
    chars_per_token: int = 4,
) -> list[SessionMessage]:
    """Compress session history when it exceeds the token threshold.
    Uses a simple strategy: keep system messages + summarize older messages + keep recent."""
    total_chars = sum(len(m.content) for m in history)
    estimated_tokens = total_chars // chars_per_token

    if estimated_tokens <= threshold_tokens:
        return history

    system_msgs = [m for m in history if m.role == "system"]
    non_system = [m for m in history if m.role != "system"]

    keep_recent = max(4, len(non_system) // 4)
    old_msgs = non_system[:-keep_recent]
    recent_msgs = non_system[-keep_recent:]

    if old_msgs:
        summary_parts = []
        for m in old_msgs:
            summary_parts.append(f"[{m.role}] {m.content[:100]}")
        summary_text = "Previous conversation summary:\n" + "\n".join(summary_parts[-10:])
        from agent_platform.domain.models import _utc_now
        summary_msg = SessionMessage(
            role="system",
            content=summary_text,
            timestamp=_utc_now(),
            metadata={"compressed": True},
        )
        return system_msgs + [summary_msg] + recent_msgs

    return history


def build_scoped_session_id(
    session_id: str,
    scope: str,
    tenant_id: str | None = None,
    store_id: str | None = None,
    user_id: str | None = None,
) -> str:
    """Build a scoped session ID based on the manifest session.scope setting."""
    if scope == "tenant_store_user":
        parts = [tenant_id or "", store_id or "", user_id or "", session_id]
        return ":".join(p for p in parts if p)
    if scope == "tenant":
        return f"{tenant_id or ''}:{session_id}"
    return session_id
