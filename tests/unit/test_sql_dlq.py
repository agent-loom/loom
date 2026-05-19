import pytest
import pytest_asyncio
from datetime import UTC, datetime, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from agent_platform.persistence.tables import Base
from agent_platform.persistence.sql import SqlDeadLetterQueue
from agent_platform.webhooks.dead_letter import DeadLetterEntry

@pytest_asyncio.fixture
async def async_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    yield session_factory
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()

@pytest.fixture
def dlq(async_session_factory):
    return SqlDeadLetterQueue(async_session_factory)

@pytest.mark.asyncio
async def test_dlq_enqueue_and_get(dlq):
    entry = DeadLetterEntry(
        source="test",
        event_type="test_event",
        payload={"key": "value"},
        error_message="error"
    )
    
    await dlq.enqueue(entry)
    
    retrieved = await dlq.get_entry(entry.id)
    assert retrieved is not None
    assert retrieved.id == entry.id
    assert retrieved.source == "test"
    assert retrieved.event_type == "test_event"
    assert retrieved.payload == {"key": "value"}
    assert retrieved.error_message == "error"
    assert retrieved.status == "pending"

@pytest.mark.asyncio
async def test_dlq_dequeue_ready(dlq):
    now = datetime.now(UTC)
    
    e1 = DeadLetterEntry(
        source="test", event_type="e1", error_message="err",
        status="pending", next_retry_at=now - timedelta(minutes=1)
    )
    e2 = DeadLetterEntry(
        source="test", event_type="e2", error_message="err",
        status="pending", next_retry_at=now + timedelta(minutes=1)
    )
    e3 = DeadLetterEntry(
        source="test", event_type="e3", error_message="err",
        status="retrying", next_retry_at=now - timedelta(minutes=1)
    )
    e4 = DeadLetterEntry(
        source="test", event_type="e4", error_message="err",
        status="resolved", next_retry_at=now - timedelta(minutes=1)
    )
    
    for e in [e1, e2, e3, e4]:
        await dlq.enqueue(e)
        
    ready = await dlq.dequeue_ready()
    ready_ids = [e.id for e in ready]
    
    assert len(ready) == 2
    assert e1.id in ready_ids
    assert e3.id in ready_ids
    assert e2.id not in ready_ids
    assert e4.id not in ready_ids

@pytest.mark.asyncio
async def test_dlq_mark_resolved(dlq):
    entry = DeadLetterEntry(source="test", event_type="test", error_message="err")
    await dlq.enqueue(entry)
    
    await dlq.mark_resolved(entry.id)
    
    retrieved = await dlq.get_entry(entry.id)
    assert retrieved.status == "resolved"

@pytest.mark.asyncio
async def test_dlq_mark_exhausted(dlq):
    entry = DeadLetterEntry(source="test", event_type="test", error_message="err")
    await dlq.enqueue(entry)
    
    await dlq.mark_exhausted(entry.id)
    
    retrieved = await dlq.get_entry(entry.id)
    assert retrieved.status == "exhausted"

@pytest.mark.asyncio
async def test_dlq_update_retry(dlq):
    entry = DeadLetterEntry(source="test", event_type="test", error_message="err")
    await dlq.enqueue(entry)
    
    next_retry = datetime.now(UTC) + timedelta(minutes=5)
    await dlq.update_retry(entry.id, next_retry, 2)
    
    retrieved = await dlq.get_entry(entry.id)
    assert retrieved.status == "retrying"
    assert retrieved.retry_count == 2
    # SQLite returns naive datetime objects by default, replace tzinfo to fix comparison
    assert retrieved.next_retry_at.replace(tzinfo=UTC) == next_retry

@pytest.mark.asyncio
async def test_dlq_list_entries(dlq):
    for i in range(5):
        entry = DeadLetterEntry(
            source="test", event_type=f"e{i}", error_message="err",
            status="pending" if i % 2 == 0 else "resolved"
        )
        await dlq.enqueue(entry)
        
    all_entries = await dlq.list_entries()
    assert len(all_entries) == 5
    
    pending_entries = await dlq.list_entries(status="pending")
    assert len(pending_entries) == 3
    
    resolved_entries = await dlq.list_entries(status="resolved")
    assert len(resolved_entries) == 2

@pytest.mark.asyncio
async def test_dlq_purge_resolved(dlq):
    now = datetime.now(UTC)
    
    e1 = DeadLetterEntry(source="test", event_type="e1", error_message="err", status="resolved")
    e1.updated_at = now - timedelta(days=10)
    
    e2 = DeadLetterEntry(source="test", event_type="e2", error_message="err", status="resolved")
    e2.updated_at = now - timedelta(days=2)
    
    e3 = DeadLetterEntry(source="test", event_type="e3", error_message="err", status="pending")
    e3.updated_at = now - timedelta(days=10)
    
    for e in [e1, e2, e3]:
        await dlq.enqueue(e)
        
    # The updated_at is overridden by DB default/update, so let's mock it
    async with dlq._sf() as session:
        from sqlalchemy import update
        from agent_platform.persistence.tables import DeadLetterEntryModel
        
        await session.execute(
            update(DeadLetterEntryModel)
            .where(DeadLetterEntryModel.id == e1.id)
            .values(updated_at=now - timedelta(days=10))
        )
        await session.execute(
            update(DeadLetterEntryModel)
            .where(DeadLetterEntryModel.id == e2.id)
            .values(updated_at=now - timedelta(days=2))
        )
        await session.execute(
            update(DeadLetterEntryModel)
            .where(DeadLetterEntryModel.id == e3.id)
            .values(updated_at=now - timedelta(days=10))
        )
        await session.commit()
    
    purged = await dlq.purge_resolved(older_than_days=7)
    assert purged == 1
    
    all_entries = await dlq.list_entries()
    assert len(all_entries) == 2
    assert all_entries[0].id in (e2.id, e3.id)
    assert all_entries[1].id in (e2.id, e3.id)
