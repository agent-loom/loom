from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


def create_engine(url: str | None = None) -> AsyncEngine:
    database_url = url or os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./agent_platform.db")
    return create_async_engine(database_url, echo=False)


def get_session_factory(engine: AsyncEngine) -> sessionmaker[AsyncSession]:
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
