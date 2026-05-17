"""Hermes Memory 桥接平台 SessionStore，将 Hermes agent 的对话记忆持久化到平台会话存储。

本模块提供两层抽象：
- PlatformMemoryProvider：底层 provider，实现 load/save/clear 接口
- HermesMemoryBridge：高层桥接器，在 HermesRuntimeBackend.run() 前后自动管理记忆
"""

from __future__ import annotations

import logging
from typing import Any

import anyio.from_thread

from agent_platform.domain.models import AgentSession, SessionMessage

logger = logging.getLogger(__name__)


class PlatformMemoryProvider:
    """Hermes memory provider 的平台实现，将对话记忆持久化到 AgentSessionRepository。

    通过 anyio.from_thread.run() 桥接异步 SessionStore 与可能的同步 Hermes 调用，
    避免嵌套事件循环问题。
    """

    def __init__(
        self,
        session_store: Any,  # AgentSessionRepository（Protocol）
        agent_id: str,
        session_id: str,
    ) -> None:
        """初始化 PlatformMemoryProvider。

        Args:
            session_store: 平台的 AgentSessionRepository 实例
            agent_id: Agent 标识符
            session_id: 会话标识符
        """
        self._store = session_store
        self._agent_id = agent_id
        self._session_id = session_id

    def load(self, session_id: str) -> list[dict[str, str]]:
        """从 SessionStore 加载会话历史，转换为 Hermes 格式。

        Args:
            session_id: 会话 ID

        Returns:
            Hermes 格式的消息列表 [{"role": "user", "content": "..."}]
        """
        try:
            session: AgentSession | None = anyio.from_thread.run(
                self._store.load, session_id
            )
        except RuntimeError:
            # 如果不在异步上下文中（例如单元测试），直接返回空列表
            logger.warning("无法从异步上下文加载会话 %s，返回空历史", session_id)
            return []

        if session is None:
            return []

        return self._session_to_hermes_messages(session)

    async def load_async(self, session_id: str) -> list[dict[str, str]]:
        """异步版本的 load，直接调用异步 SessionStore。

        Args:
            session_id: 会话 ID

        Returns:
            Hermes 格式的消息列表
        """
        session = await self._store.load(session_id)
        if session is None:
            return []
        return self._session_to_hermes_messages(session)

    def save(self, session_id: str, messages: list[dict[str, str]]) -> None:
        """将 Hermes 格式消息保存到 SessionStore。

        Args:
            session_id: 会话 ID
            messages: Hermes 格式的消息列表
        """
        try:
            anyio.from_thread.run(
                self._save_async, session_id, messages
            )
        except RuntimeError:
            logger.warning("无法在异步上下文中保存会话 %s", session_id)

    async def save_async(
        self, session_id: str, messages: list[dict[str, str]]
    ) -> None:
        """异步版本的 save，直接调用异步 SessionStore。

        Args:
            session_id: 会话 ID
            messages: Hermes 格式的消息列表
        """
        await self._save_async(session_id, messages)

    async def _save_async(
        self, session_id: str, messages: list[dict[str, str]]
    ) -> None:
        """内部异步保存实现。"""
        session = await self._store.load(session_id)
        if session is None:
            session = AgentSession(
                session_id=session_id,
                agent_id=self._agent_id,
            )

        # 用新消息替换历史记录
        session.history = [
            SessionMessage(
                role=msg.get("role", "user"),
                content=msg.get("content", ""),
            )
            for msg in messages
        ]
        await self._store.save(session)

    def clear(self, session_id: str) -> None:
        """清空指定会话的历史消息。

        Args:
            session_id: 会话 ID
        """
        try:
            anyio.from_thread.run(self._clear_async, session_id)
        except RuntimeError:
            logger.warning("无法在异步上下文中清空会话 %s", session_id)

    async def clear_async(self, session_id: str) -> None:
        """异步版本的 clear。

        Args:
            session_id: 会话 ID
        """
        await self._clear_async(session_id)

    async def _clear_async(self, session_id: str) -> None:
        """内部异步清空实现。"""
        await self._store.delete(session_id)

    @staticmethod
    def _session_to_hermes_messages(
        session: AgentSession,
    ) -> list[dict[str, str]]:
        """将平台 AgentSession 的历史消息转换为 Hermes 格式。

        Args:
            session: 平台会话对象

        Returns:
            Hermes 格式消息列表
        """
        return [
            {"role": msg.role, "content": msg.content}
            for msg in session.history
        ]


class HermesMemoryBridge:
    """高层记忆桥接器，在 HermesRuntimeBackend.run() 前后自动加载/保存记忆。

    在 run() 前调用 prepare() 加载已有记忆，run() 后调用 commit() 保存新消息。
    支持 max_history 参数限制历史消息数量。
    """

    def __init__(
        self,
        provider: PlatformMemoryProvider,
        max_history: int = 50,
    ) -> None:
        """初始化 HermesMemoryBridge。

        Args:
            provider: PlatformMemoryProvider 实例
            max_history: 最大历史消息数量，默认 50
        """
        self._provider = provider
        self._max_history = max_history

    async def prepare(self, session_id: str) -> list[dict[str, str]]:
        """加载已有记忆，返回 Hermes 格式的消息历史。

        自动截断超出 max_history 限制的旧消息。

        Args:
            session_id: 会话 ID

        Returns:
            Hermes 格式的消息列表（已截断）
        """
        messages = await self._provider.load_async(session_id)
        # 截断超出 max_history 的旧消息
        if len(messages) > self._max_history:
            messages = messages[-self._max_history :]
        return messages

    async def commit(
        self, session_id: str, new_messages: list[dict[str, str]]
    ) -> None:
        """保存新消息到 SessionStore，自动截断超出限制的旧消息。

        Args:
            session_id: 会话 ID
            new_messages: 新的完整消息列表
        """
        # 截断超出 max_history 的旧消息
        if len(new_messages) > self._max_history:
            new_messages = new_messages[-self._max_history :]
        await self._provider.save_async(session_id, new_messages)
