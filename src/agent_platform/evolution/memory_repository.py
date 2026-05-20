"""EvolutionMemory 和 SkillEntry 持久化：Protocol + InMemory 实现。"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from .memory_models import (
    EvolutionMemory,
    MemoryStatus,
    MemoryType,
    SkillEntry,
    RuntimeMemory,
    RuntimeMemoryScope,
    RuntimeMemoryType,
)


@runtime_checkable
class RuntimeMemoryRepository(Protocol):
    async def create(self, memory: RuntimeMemory) -> None: ...
    async def get(self, memory_id: str) -> RuntimeMemory | None: ...
    async def list_by_agent(
        self,
        agent_id: str,
        *,
        scope: RuntimeMemoryScope | None = None,
        status: MemoryStatus | None = None,
        limit: int = 50,
    ) -> list[RuntimeMemory]: ...
    async def list_by_user(
        self,
        user_id: str,
        *,
        status: MemoryStatus | None = None,
        limit: int = 50,
    ) -> list[RuntimeMemory]: ...
    async def list_by_session(
        self,
        session_id: str,
        *,
        status: MemoryStatus | None = None,
        limit: int = 50,
    ) -> list[RuntimeMemory]: ...
    async def list_by_tenant(
        self,
        tenant_id: str,
        *,
        scope: RuntimeMemoryScope | None = None,
        status: MemoryStatus | None = None,
        limit: int = 50,
    ) -> list[RuntimeMemory]: ...
    async def list_all(
        self,
        *,
        scope: RuntimeMemoryScope | None = None,
        status: MemoryStatus | None = None,
        limit: int = 100,
    ) -> list[RuntimeMemory]: ...
    async def update(self, memory: RuntimeMemory) -> None: ...
    async def delete(self, memory_id: str) -> bool: ...



@runtime_checkable
class EvolutionMemoryRepository(Protocol):
    async def create(self, memory: EvolutionMemory) -> None: ...
    async def get(self, memory_id: str) -> EvolutionMemory | None: ...
    async def list_by_agent(
        self,
        agent_id: str,
        *,
        memory_type: MemoryType | None = None,
        status: MemoryStatus | None = None,
        limit: int = 50,
    ) -> list[EvolutionMemory]: ...
    async def list_by_tenant(
        self,
        tenant_id: str,
        *,
        memory_type: MemoryType | None = None,
        status: MemoryStatus | None = None,
        limit: int = 50,
    ) -> list[EvolutionMemory]: ...
    async def list_all(
        self,
        *,
        memory_type: MemoryType | None = None,
        status: MemoryStatus | None = None,
        limit: int = 100,
    ) -> list[EvolutionMemory]: ...
    async def update(self, memory: EvolutionMemory) -> None: ...
    async def delete(self, memory_id: str) -> bool: ...


@runtime_checkable
class SkillRepository(Protocol):
    async def create(self, skill: SkillEntry) -> None: ...
    async def get(self, skill_id: str) -> SkillEntry | None: ...
    async def list_by_agent(
        self,
        agent_id: str,
        *,
        status: MemoryStatus | None = None,
        limit: int = 50,
    ) -> list[SkillEntry]: ...
    async def list_all(
        self,
        *,
        status: MemoryStatus | None = None,
        limit: int = 100,
    ) -> list[SkillEntry]: ...
    async def update(self, skill: SkillEntry) -> None: ...
    async def delete(self, skill_id: str) -> bool: ...


class InMemoryEvolutionMemoryRepository:
    def __init__(self) -> None:
        self._store: dict[str, EvolutionMemory] = {}
        self._by_agent: dict[str, list[str]] = defaultdict(list)
        self._by_tenant: dict[str, list[str]] = defaultdict(list)

    async def create(self, memory: EvolutionMemory) -> None:
        self._store[memory.memory_id] = memory
        self._by_agent[memory.agent_id].append(memory.memory_id)
        self._by_tenant[memory.tenant_id].append(memory.memory_id)

    async def get(self, memory_id: str) -> EvolutionMemory | None:
        return self._store.get(memory_id)

    async def list_by_agent(
        self,
        agent_id: str,
        *,
        memory_type: MemoryType | None = None,
        status: MemoryStatus | None = None,
        limit: int = 50,
    ) -> list[EvolutionMemory]:
        ids = self._by_agent.get(agent_id, [])
        return self._filter_and_sort(ids, memory_type=memory_type, status=status, limit=limit)

    async def list_by_tenant(
        self,
        tenant_id: str,
        *,
        memory_type: MemoryType | None = None,
        status: MemoryStatus | None = None,
        limit: int = 50,
    ) -> list[EvolutionMemory]:
        ids = self._by_tenant.get(tenant_id, [])
        return self._filter_and_sort(ids, memory_type=memory_type, status=status, limit=limit)

    async def list_all(
        self,
        *,
        memory_type: MemoryType | None = None,
        status: MemoryStatus | None = None,
        limit: int = 100,
    ) -> list[EvolutionMemory]:
        ids = list(self._store.keys())
        return self._filter_and_sort(ids, memory_type=memory_type, status=status, limit=limit)

    async def update(self, memory: EvolutionMemory) -> None:
        if memory.memory_id not in self._store:
            return
        memory.updated_at = datetime.now(UTC)
        self._store[memory.memory_id] = memory

    async def delete(self, memory_id: str) -> bool:
        memory = self._store.pop(memory_id, None)
        if memory is None:
            return False
        agent_ids = self._by_agent.get(memory.agent_id, [])
        if memory_id in agent_ids:
            agent_ids.remove(memory_id)
        tenant_ids = self._by_tenant.get(memory.tenant_id, [])
        if memory_id in tenant_ids:
            tenant_ids.remove(memory_id)
        return True

    def _filter_and_sort(
        self,
        ids: list[str],
        *,
        memory_type: MemoryType | None,
        status: MemoryStatus | None,
        limit: int,
    ) -> list[EvolutionMemory]:
        result = [self._store[mid] for mid in ids if mid in self._store]
        if memory_type is not None:
            result = [m for m in result if m.type == memory_type]
        if status is not None:
            result = [m for m in result if m.status == status]
        return sorted(result, key=lambda m: m.created_at, reverse=True)[:limit]


class InMemorySkillRepository:
    def __init__(self) -> None:
        self._store: dict[str, SkillEntry] = {}
        self._by_agent: dict[str, list[str]] = defaultdict(list)

    async def create(self, skill: SkillEntry) -> None:
        self._store[skill.skill_id] = skill
        self._by_agent[skill.agent_id].append(skill.skill_id)

    async def get(self, skill_id: str) -> SkillEntry | None:
        return self._store.get(skill_id)

    async def list_by_agent(
        self,
        agent_id: str,
        *,
        status: MemoryStatus | None = None,
        limit: int = 50,
    ) -> list[SkillEntry]:
        ids = self._by_agent.get(agent_id, [])
        result = [self._store[sid] for sid in ids if sid in self._store]
        if status is not None:
            result = [s for s in result if s.status == status]
        return sorted(result, key=lambda s: s.created_at, reverse=True)[:limit]

    async def list_all(
        self,
        *,
        status: MemoryStatus | None = None,
        limit: int = 100,
    ) -> list[SkillEntry]:
        result = list(self._store.values())
        if status is not None:
            result = [s for s in result if s.status == status]
        return sorted(result, key=lambda s: s.created_at, reverse=True)[:limit]

    async def update(self, skill: SkillEntry) -> None:
        if skill.skill_id not in self._store:
            return
        skill.updated_at = datetime.now(UTC)
        self._store[skill.skill_id] = skill

    async def delete(self, skill_id: str) -> bool:
        skill = self._store.pop(skill_id, None)
        if skill is None:
            return False
        agent_ids = self._by_agent.get(skill.agent_id, [])
        if skill_id in agent_ids:
            agent_ids.remove(skill_id)
        return True


class InMemoryRuntimeMemoryRepository:
    """Runtime Memory 内存仓储实现。"""

    def __init__(self) -> None:
        self._store: dict[str, RuntimeMemory] = {}

    async def create(self, memory: RuntimeMemory) -> None:
        self._store[memory.memory_id] = memory

    async def get(self, memory_id: str) -> RuntimeMemory | None:
        return self._store.get(memory_id)

    async def list_by_agent(
        self,
        agent_id: str,
        *,
        scope: RuntimeMemoryScope | None = None,
        status: MemoryStatus | None = None,
        limit: int = 50,
    ) -> list[RuntimeMemory]:
        result = [m for m in self._store.values() if m.agent_id == agent_id]
        return self._filter_and_sort(result, scope=scope, status=status, limit=limit)

    async def list_by_user(
        self,
        user_id: str,
        *,
        status: MemoryStatus | None = None,
        limit: int = 50,
    ) -> list[RuntimeMemory]:
        result = [m for m in self._store.values() if m.scope == RuntimeMemoryScope.USER and m.subject_id == user_id]
        return self._filter_and_sort(result, status=status, limit=limit)

    async def list_by_session(
        self,
        session_id: str,
        *,
        status: MemoryStatus | None = None,
        limit: int = 50,
    ) -> list[RuntimeMemory]:
        result = [m for m in self._store.values() if m.session_id == session_id]
        return self._filter_and_sort(result, status=status, limit=limit)

    async def list_by_tenant(
        self,
        tenant_id: str,
        *,
        scope: RuntimeMemoryScope | None = None,
        status: MemoryStatus | None = None,
        limit: int = 50,
    ) -> list[RuntimeMemory]:
        result = [m for m in self._store.values() if m.tenant_id == tenant_id]
        return self._filter_and_sort(result, scope=scope, status=status, limit=limit)

    async def list_all(
        self,
        *,
        scope: RuntimeMemoryScope | None = None,
        status: MemoryStatus | None = None,
        limit: int = 100,
    ) -> list[RuntimeMemory]:
        result = list(self._store.values())
        return self._filter_and_sort(result, scope=scope, status=status, limit=limit)

    async def update(self, memory: RuntimeMemory) -> None:
        if memory.memory_id not in self._store:
            return
        self._store[memory.memory_id] = memory

    async def delete(self, memory_id: str) -> bool:
        if memory_id in self._store:
            self._store.pop(memory_id)
            return True
        return False

    def _filter_and_sort(
        self,
        memories: list[RuntimeMemory],
        *,
        scope: RuntimeMemoryScope | None = None,
        status: MemoryStatus | None = None,
        limit: int,
    ) -> list[RuntimeMemory]:
        result = memories
        if scope is not None:
            result = [m for m in result if m.scope == scope]
        if status is not None:
            result = [m for m in result if m.status == status]
        # 动态过滤已过期的数据
        result = [m for m in result if not m.is_expired()]
        return sorted(result, key=lambda m: m.created_at, reverse=True)[:limit]

