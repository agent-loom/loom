"""知识同步调度器，基于后台任务定期将知识源同步到向量数据库。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from agent_platform.domain.models import ManifestKnowledgeSource
from agent_platform.knowledge.sync import DataSynchronization

logger = logging.getLogger(__name__)


class KnowledgeSyncScheduler:
    """Periodically syncs knowledge sources to the vector backend."""

    def __init__(
        self,
        data_sync: DataSynchronization,
        interval_seconds: float = 3600.0,
    ):
        self._data_sync = data_sync
        self._interval = interval_seconds
        self._sources: list[tuple[ManifestKnowledgeSource, Path]] = []
        self._task: asyncio.Task[None] | None = None
        self._running = False

    def add_source(
        self,
        source: ManifestKnowledgeSource,
        directory: Path,
    ) -> None:
        self._sources.append((source, directory))

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "knowledge sync scheduler started: %d sources, interval=%ds",
            len(self._sources),
            self._interval,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("knowledge sync scheduler stopped")

    async def sync_all(self) -> list[dict[str, Any]]:
        """Run a single sync pass across all registered sources."""
        results: list[dict[str, Any]] = []
        for source, directory in self._sources:
            try:
                result = await self._data_sync.sync_directory(source, directory)
                results.append({
                    "collection": source.collection,
                    "directory": str(directory),
                    **result,
                })
                logger.info(
                    "synced knowledge source %s: %s",
                    source.collection,
                    result.get("status", "unknown"),
                )
            except Exception:
                logger.exception(
                    "failed to sync knowledge source %s", source.collection,
                )
                results.append({
                    "collection": source.collection,
                    "directory": str(directory),
                    "status": "error",
                })
        return results

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.sync_all()
            except Exception:
                logger.exception("knowledge sync cycle failed")
            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break
