"""异步数据库引擎与会话工厂。"""

from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


def create_engine(url: str | None = None) -> AsyncEngine:
    """创建异步数据库引擎，默认使用 SQLite。"""
    database_url = url or os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./agent_platform.db")
    return create_async_engine(database_url, echo=False)


def get_session_factory(engine: AsyncEngine) -> sessionmaker[AsyncSession]:
    """基于给定引擎创建异步会话工厂。"""
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
