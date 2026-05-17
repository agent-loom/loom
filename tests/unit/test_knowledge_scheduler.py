"""Tests for KnowledgeSyncScheduler."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_platform.domain.models import ManifestKnowledgeSource
from agent_platform.knowledge.scheduler import KnowledgeSyncScheduler


@pytest.fixture
def mock_data_sync():
    sync = MagicMock()
    sync.sync_directory = AsyncMock(return_value={"status": "ok"})
    return sync


@pytest.fixture
def source():
    return ManifestKnowledgeSource(
        id="test-source",
        collection="test_docs",
        type="weaviate",
        backend="weaviate",
    )


def test_add_source(mock_data_sync, source):
    scheduler = KnowledgeSyncScheduler(mock_data_sync)
    scheduler.add_source(source, Path("/tmp/docs"))
    assert len(scheduler._sources) == 1


@pytest.mark.asyncio
async def test_sync_all(mock_data_sync, source):
    scheduler = KnowledgeSyncScheduler(mock_data_sync)
    scheduler.add_source(source, Path("/tmp/docs"))
    results = await scheduler.sync_all()
    assert len(results) == 1
    assert results[0]["collection"] == "test_docs"
    assert results[0]["status"] == "ok"
    mock_data_sync.sync_directory.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_all_handles_error(source):
    sync = MagicMock()
    sync.sync_directory = AsyncMock(side_effect=RuntimeError("connection refused"))
    scheduler = KnowledgeSyncScheduler(sync)
    scheduler.add_source(source, Path("/tmp/docs"))
    results = await scheduler.sync_all()
    assert len(results) == 1
    assert results[0]["status"] == "error"


@pytest.mark.asyncio
async def test_start_stop(mock_data_sync, source):
    scheduler = KnowledgeSyncScheduler(mock_data_sync, interval_seconds=0.05)
    scheduler.add_source(source, Path("/tmp/docs"))
    await scheduler.start()
    assert scheduler._running is True
    await asyncio.sleep(0.15)
    await scheduler.stop()
    assert scheduler._running is False
    assert mock_data_sync.sync_directory.await_count >= 1


@pytest.mark.asyncio
async def test_start_idempotent(mock_data_sync):
    scheduler = KnowledgeSyncScheduler(mock_data_sync, interval_seconds=100)
    await scheduler.start()
    task1 = scheduler._task
    await scheduler.start()
    assert scheduler._task is task1
    await scheduler.stop()


@pytest.mark.asyncio
async def test_sync_all_multiple_sources(mock_data_sync):
    scheduler = KnowledgeSyncScheduler(mock_data_sync)
    scheduler.add_source(
        ManifestKnowledgeSource(id="s1", collection="c1", type="weaviate", backend="weaviate"),
        Path("/a"),
    )
    scheduler.add_source(
        ManifestKnowledgeSource(id="s2", collection="c2", type="weaviate", backend="weaviate"),
        Path("/b"),
    )
    results = await scheduler.sync_all()
    assert len(results) == 2
    assert mock_data_sync.sync_directory.await_count == 2
