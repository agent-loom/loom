from __future__ import annotations

from typing import Protocol

from agent_platform.domain.models import AgentSession, _utc_now


class SessionStore(Protocol):
    def load(self, session_id: str) -> AgentSession | None:
        ...

    def save(self, session: AgentSession) -> None:
        ...

    def delete(self, session_id: str) -> None:
        ...

    def list_sessions(self, agent_id: str | None = None) -> list[AgentSession]:
        ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, AgentSession] = {}

    def load(self, session_id: str) -> AgentSession | None:
        return self._sessions.get(session_id)

    def save(self, session: AgentSession) -> None:
        session.updated_at = _utc_now()
        self._sessions[session.session_id] = session

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def list_sessions(self, agent_id: str | None = None) -> list[AgentSession]:
        sessions = list(self._sessions.values())
        if agent_id:
            sessions = [s for s in sessions if s.agent_id == agent_id]
        return sessions
