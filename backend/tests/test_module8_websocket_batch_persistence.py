"""Module 8.5 Focused Tests: Background Batch Persistence & Performance (REQ-056, PRD Sec 9.1-9.4).

Tests:
1. High-frequency writes batched in memory without immediate synchronous DB commit.
2. Periodic time-based flush triggers after configured interval.
3. Capacity size-based early flush triggers immediately when max_batch_size is reached.
4. Strict FIFO ordering preserved across mixed chat and queue events.
5. In-flight pending ChatMessage lookup allows immediate reactions before DB flush.
6. Lounge isolation during selective flush (flush_lounge).
7. Clean shutdown and lifecycle drain flush on stop().
8. Transient failure and item-level fallback retry handling.
9. Permanent failure isolation into dead-letter storage without silent data loss.
10. Concurrent enqueues and flushes maintain data integrity and accurate totals.
11. Service integration: end_lounge flushes pending batched items cleanly.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.db.session import (
    configure_database,
    create_session_maker,
    drop_db,
    get_session_maker,
    init_db,
)
from app.models.chat import ChatMessage
from app.models.lounge import DJLounge
from app.models.queue import QueueEvent
from app.models.user import User
from app.seeds.seed_data import seed_database
from app.services.lounge_service import (
    end_lounge,
    send_lounge_chat_message,
)
from app.services.room_state_manager import room_state_manager
from app.websocket.batch_persistence import (
    BatchPersistenceWorker,
    batch_persistence,
)
from app.websocket.connection_manager import connection_manager

TEST_SEED_PASSWORD = "TestFixtureSeedPass!2026"


@pytest.fixture
async def ws_db_engine() -> AsyncEngine:
    """Create an isolated in-memory sqlite database initialized with schema and test entities."""
    engine = configure_database("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    session_maker = create_session_maker(engine)
    async with session_maker() as session:
        user = User(
            id="u_test_host",
            email="host@spotify-intern.local",
            password_hash="testpasshash",
            display_name="Test Host",
            role="host",
        )
        session.add(user)
        for idx, lid in enumerate((
            "lounge_alpha",
            "lounge_seq",
            "lounge_A",
            "lounge_B",
            "lounge_drain",
            "lounge_test",
            "lounge_dead",
            "lounge_concurrent",
        )):
            lounge = DJLounge(
                id=lid,
                invite_code=f"SP-TEST{idx:03d}",
                host_user_id="u_test_host",
                status="live",
                vibe_profile="Deep House",
            )
            session.add(lounge)
        await session.commit()
    yield engine
    await drop_db(engine)
    await engine.dispose()


@pytest.fixture(autouse=True)
def clean_batch_state():
    """Ensure clean state before and after every test."""
    batch_persistence.reset_for_testing()
    room_state_manager.clear_cache_for_testing()
    connection_manager.clear_for_testing()
    yield
    batch_persistence.reset_for_testing()
    room_state_manager.clear_cache_for_testing()
    connection_manager.clear_for_testing()


async def _get_db_counts() -> Tuple[int, int]:
    session_maker = get_session_maker()
    async with session_maker() as session:
        chat_count = (await session.execute(select(func.count(ChatMessage.id)))).scalar_one()
        event_count = (await session.execute(select(func.count(QueueEvent.id)))).scalar_one()
        return chat_count, event_count


# ==============================================================================
# Focused Module 8.5 Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_1_high_frequency_writes_batched_in_memory(ws_db_engine: AsyncEngine):
    """Test 1: Enqueuing writes holds them in memory without immediate DB commit."""
    chats_before, events_before = await _get_db_counts()

    # Enqueue 5 chat messages and 3 queue events
    for i in range(5):
        batch_persistence.enqueue_chat_message({
            "id": f"msg_batch_{i}",
            "lounge_id": "lounge_alpha",
            "user_id": "u_test_host",
            "body": f"Batch message {i}",
        })

    for i in range(3):
        batch_persistence.enqueue_queue_event({
            "id": f"qev_batch_{i}",
            "lounge_id": "lounge_alpha",
            "event_type": "vote",
            "payload": {"trackId": f"t_200{i}", "action": "upvote"},
        })

    # Assert they are held in memory
    assert batch_persistence.pending_count == 8
    assert batch_persistence.pending_chat_count == 5
    assert batch_persistence.pending_event_count == 3

    # Assert zero immediate database writes have occurred yet
    chats_after, events_after = await _get_db_counts()
    assert chats_after == chats_before
    assert events_after == events_before

    # Now manually flush and verify DB persistence
    flushed = await batch_persistence.flush()
    assert flushed == 8
    assert batch_persistence.pending_count == 0

    chats_final, events_final = await _get_db_counts()
    assert chats_final == chats_before + 5
    assert events_final == events_before + 3


@pytest.mark.asyncio
async def test_2_periodic_time_based_flush(ws_db_engine: AsyncEngine):
    """Test 2: Periodic worker flushes pending writes after flush_interval_seconds."""
    chats_before, _ = await _get_db_counts()

    # Create worker with a short interval for testing (200ms)
    worker = BatchPersistenceWorker(flush_interval_seconds=0.2, max_batch_size=50)
    worker.start()

    try:
        # Enqueue 3 messages
        for i in range(3):
            worker.enqueue_chat_message({
                "id": f"msg_timer_{i}",
                "lounge_id": "lounge_alpha",
                "body": f"Timer message {i}",
            })

        assert worker.pending_count == 3

        # Wait slightly longer than the interval (350ms)
        await asyncio.sleep(0.35)

        # Worker should have flushed automatically
        assert worker.pending_count == 0

        chats_after, _ = await _get_db_counts()
        assert chats_after == chats_before + 3
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_3_capacity_size_based_early_flush(ws_db_engine: AsyncEngine):
    """Test 3: Reaching max_batch_size triggers early flush before the interval timer."""
    chats_before, _ = await _get_db_counts()

    # Configure worker with long timer (10s) and small batch size (4)
    worker = BatchPersistenceWorker(flush_interval_seconds=10.0, max_batch_size=4)
    worker.start()

    try:
        # Enqueue 3 messages (below threshold)
        for i in range(3):
            worker.enqueue_chat_message({
                "id": f"msg_cap_{i}",
                "lounge_id": "lounge_alpha",
                "body": f"Cap message {i}",
            })

        # Allow short tick for event loop
        await asyncio.sleep(0.05)
        assert worker.pending_count == 3
        chats_interim, _ = await _get_db_counts()
        assert chats_interim == chats_before

        # Enqueue 4th message - hits max_batch_size=4, triggering early flush
        worker.enqueue_chat_message({
            "id": "msg_cap_3",
            "lounge_id": "lounge_alpha",
            "body": "Cap message 3 (trigger)",
        })

        # Wait briefly for worker task to process flush event (< 100ms)
        await asyncio.sleep(0.1)

        # Verified flushed well before 10.0s interval
        assert worker.pending_count == 0
        chats_after, _ = await _get_db_counts()
        assert chats_after == chats_before + 4
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_4_strict_fifo_ordering_preserved(ws_db_engine: AsyncEngine):
    """Test 4: Strict FIFO insertion ordering is preserved across all queued records."""
    worker = BatchPersistenceWorker()

    # Enqueue interleaved chat messages and queue events
    sequence = [
        ("chat", "msg_seq_1", "lounge_seq"),
        ("queue_event", "qev_seq_1", "lounge_seq"),
        ("chat", "msg_seq_2", "lounge_seq"),
        ("queue_event", "qev_seq_2", "lounge_seq"),
        ("chat", "msg_seq_3", "lounge_seq"),
    ]

    for kind, item_id, l_id in sequence:
        if kind == "chat":
            worker.enqueue_chat_message({
                "id": item_id,
                "lounge_id": l_id,
                "body": f"Body for {item_id}",
            })
        else:
            worker.enqueue_queue_event({
                "id": item_id,
                "lounge_id": l_id,
                "event_type": "vote",
                "payload": {"id": item_id},
            })

    # Internal queue order check
    queued_ids = [q.item_id for q in worker._queue]
    assert queued_ids == ["msg_seq_1", "qev_seq_1", "msg_seq_2", "qev_seq_2", "msg_seq_3"]

    # Flush to DB
    flushed = await worker.flush()
    assert flushed == 5

    # Verify persisted in DB
    session_maker = get_session_maker()
    async with session_maker() as session:
        chats = (await session.execute(
            select(ChatMessage.id).where(ChatMessage.lounge_id == "lounge_seq")
        )).scalars().all()
        events = (await session.execute(
            select(QueueEvent.id).where(QueueEvent.lounge_id == "lounge_seq")
        )).scalars().all()

        assert "msg_seq_1" in chats
        assert "msg_seq_2" in chats
        assert "msg_seq_3" in chats
        assert "qev_seq_1" in events
        assert "qev_seq_2" in events


@pytest.mark.asyncio
async def test_5_in_flight_pending_chat_message_lookup(ws_db_engine: AsyncEngine):
    """Test 5: In-flight ChatMessage can be looked up immediately before DB flush."""
    worker = BatchPersistenceWorker()

    msg = worker.enqueue_chat_message({
        "id": "msg_lookup_1",
        "lounge_id": "lounge_alpha",
        "user_id": "u_test_host",
        "body": "Lookup testing message",
    })

    # 1. Successful lookup by correct lounge and messageId
    found = worker.get_pending_chat_message("lounge_alpha", "msg_lookup_1")
    assert found is not None
    assert found.id == "msg_lookup_1"
    assert found.body == "Lookup testing message"

    # 2. Cross-lounge lookup isolation (wrong lounge returns None)
    isolated = worker.get_pending_chat_message("lounge_other", "msg_lookup_1")
    assert isolated is None

    # 3. Non-existent ID returns None
    missing = worker.get_pending_chat_message("lounge_alpha", "msg_nonexistent")
    assert missing is None

    # 4. After flush, pending lookup returns None
    await worker.flush()
    assert worker.get_pending_chat_message("lounge_alpha", "msg_lookup_1") is None


@pytest.mark.asyncio
async def test_6_lounge_isolation_flush(ws_db_engine: AsyncEngine):
    """Test 6: flush_lounge only flushes the targeted lounge; other lounges remain queued."""
    worker = BatchPersistenceWorker()

    # Enqueue items for lounge_A and lounge_B
    worker.enqueue_chat_message({"id": "msg_A1", "lounge_id": "lounge_A", "body": "A1"})
    worker.enqueue_chat_message({"id": "msg_A2", "lounge_id": "lounge_A", "body": "A2"})
    worker.enqueue_chat_message({"id": "msg_B1", "lounge_id": "lounge_B", "body": "B1"})
    worker.enqueue_queue_event({"id": "qev_B1", "lounge_id": "lounge_B", "event_type": "vote", "payload": {}})

    assert worker.pending_count == 4

    # Flush only lounge_A
    flushed_A = await worker.flush_lounge("lounge_A")
    assert flushed_A == 2

    # Lounge B items remain in memory
    assert worker.pending_count == 2
    assert worker.get_pending_chat_message("lounge_B", "msg_B1") is not None
    assert worker.get_pending_chat_message("lounge_A", "msg_A1") is None

    # Verify only lounge_A items exist in DB
    session_maker = get_session_maker()
    async with session_maker() as session:
        chats_A = (await session.execute(
            select(ChatMessage.id).where(ChatMessage.lounge_id == "lounge_A")
        )).scalars().all()
        chats_B = (await session.execute(
            select(ChatMessage.id).where(ChatMessage.lounge_id == "lounge_B")
        )).scalars().all()

        assert len(chats_A) == 2
        assert len(chats_B) == 0

    # Flush lounge_B
    flushed_B = await worker.flush_lounge("lounge_B")
    assert flushed_B == 2
    assert worker.pending_count == 0


@pytest.mark.asyncio
async def test_7_clean_stop_shutdown_flush(ws_db_engine: AsyncEngine):
    """Test 7: stop() cleanly drains and flushes all remaining queued items."""
    chats_before, _ = await _get_db_counts()

    worker = BatchPersistenceWorker(flush_interval_seconds=10.0, max_batch_size=100)
    worker.start()
    assert worker.is_running is True

    # Enqueue several items
    for i in range(5):
        worker.enqueue_chat_message({"id": f"msg_drain_{i}", "lounge_id": "lounge_drain", "body": f"Drain {i}"})

    assert worker.pending_count == 5

    # Stop the worker - must flush remaining items
    flushed = await worker.stop()
    assert flushed == 5
    assert worker.is_running is False
    assert worker.pending_count == 0

    chats_after, _ = await _get_db_counts()
    assert chats_after == chats_before + 5


@pytest.mark.asyncio
async def test_8_transient_failure_and_item_level_retry(ws_db_engine: AsyncEngine):
    """Test 8: Bulk commit failure falls back to item-level recovery and re-queues transient errors."""
    worker = BatchPersistenceWorker(max_retries=3)

    # Enqueue 2 valid items and 1 item that will fail
    worker.enqueue_chat_message({"id": "msg_valid_1", "lounge_id": "lounge_test", "body": "Valid 1"})
    worker.enqueue_chat_message({"id": "msg_valid_2", "lounge_id": "lounge_test", "body": "Valid 2"})
    # Item with an invalid foreign key / constraint when committed
    invalid_event = QueueEvent(
        id="qev_invalid",
        lounge_id="lounge_test",
        event_type="completely_invalid_type_fails_check_constraint",
        payload={},
        ts=12345,
        created_at=datetime.now(timezone.utc),
    )
    worker._queue.append(
        # Manually wrap to bypass enqueue validation and test DB constraint fallback
        type(worker._queue[0])(invalid_event, "queue_event", "lounge_test", "qev_invalid", datetime.now(timezone.utc))
    )

    assert worker.pending_count == 3

    # Flush: the bulk insert will fail due to the check constraint on invalid_event.
    # Item-level recovery will commit msg_valid_1 and msg_valid_2, and retry invalid_event.
    flushed = await worker.flush()

    # Both valid messages are successfully persisted!
    assert flushed == 2

    # The failing event was retried (attempt 1/3) and re-queued
    assert worker.pending_count == 1
    stats = worker.get_stats()
    assert stats["failedBatches"] == 1
    assert stats["flushedTotal"] == 2

    # Verify valid items are safely in DB
    session_maker = get_session_maker()
    async with session_maker() as session:
        chats = (await session.execute(
            select(ChatMessage.id).where(ChatMessage.lounge_id == "lounge_test")
        )).scalars().all()
        assert "msg_valid_1" in chats
        assert "msg_valid_2" in chats


@pytest.mark.asyncio
async def test_9_permanent_failure_dead_letter_isolation(ws_db_engine: AsyncEngine):
    """Test 9: Item that exceeds max_retries is isolated into dead-letter store without data loss."""
    worker = BatchPersistenceWorker(max_retries=2)

    invalid_event = QueueEvent(
        id="qev_poison_pill",
        lounge_id="lounge_dead",
        event_type="invalid_event_type_exceeds_constraint",
        payload={"note": "poison pill payload"},
        ts=99999,
        created_at=datetime.now(timezone.utc),
    )
    worker.enqueue_chat_message({"id": "msg_dead_1", "lounge_id": "lounge_dead", "body": "Good message"})
    from app.websocket.batch_persistence import QueuedBatchItem
    worker._queue.append(
        QueuedBatchItem(invalid_event, "queue_event", "lounge_dead", "qev_poison_pill", datetime.now(timezone.utc))
    )

    # First flush: good message persists, bad event fails and increments retry to 1 (< 2)
    flushed_1 = await worker.flush()
    assert flushed_1 == 1
    assert worker.pending_count == 1
    assert len(worker.get_dead_letter_items()) == 0

    # Second flush: bad event fails again, reaching max_retries=2 -> moved to dead-letter!
    flushed_2 = await worker.flush()
    assert flushed_2 == 0
    assert worker.pending_count == 0

    dead_items = worker.get_dead_letter_items()
    assert len(dead_items) == 1
    dead = dead_items[0]
    assert dead["id"] == "qev_poison_pill"
    assert dead["itemType"] == "queue_event"
    assert dead["retries"] == 2
    assert "error" in dead


@pytest.mark.asyncio
async def test_10_concurrent_enqueues_and_flush_safety(ws_db_engine: AsyncEngine):
    """Test 10: Concurrent producers enqueuing into running worker maintain integrity."""
    chats_before, _ = await _get_db_counts()
    worker = BatchPersistenceWorker(flush_interval_seconds=0.1, max_batch_size=20)
    worker.start()

    TOTAL_TASKS = 5
    MESSAGES_PER_TASK = 10

    async def producer(task_idx: int):
        for m_idx in range(MESSAGES_PER_TASK):
            worker.enqueue_chat_message({
                "id": f"msg_concurrent_{task_idx}_{m_idx}",
                "lounge_id": "lounge_concurrent",
                "body": f"Concurrent task {task_idx} msg {m_idx}",
            })
            await asyncio.sleep(0.01)

    try:
        await asyncio.gather(*(producer(i) for i in range(TOTAL_TASKS)))
        # Wait for worker to finish flushing remaining messages
        await asyncio.sleep(0.3)
        await worker.flush()

        assert worker.pending_count == 0
        chats_after, _ = await _get_db_counts()
        expected_total = TOTAL_TASKS * MESSAGES_PER_TASK
        assert chats_after == chats_before + expected_total
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_11_service_integration_end_lounge_flushes_pending(ws_db_engine: AsyncEngine):
    """Test 11: end_lounge safely flushes pending batched writes for the lounge."""
    session_maker = get_session_maker()
    async with session_maker() as session:
        # Load host user
        host = (await session.execute(select(User).where(User.email == "host@spotify-intern.local"))).scalar_one()

        # Create active lounge
        lounge = DJLounge(
            id="lounge_service_batch_test",
            invite_code="SP-TESTBAT1",
            host_user_id=host.id,
            status="live",
            vibe_profile="Deep House",
        )
        session.add(lounge)
        await session.commit()

        # Enqueue a batched chat message and queue event for this lounge
        batch_persistence.enqueue_chat_message({
            "id": "msg_svc_pending_1",
            "lounge_id": lounge.id,
            "user_id": host.id,
            "body": "Pending batched chat before lounge ends",
        })
        batch_persistence.enqueue_queue_event({
            "id": "qev_svc_pending_1",
            "lounge_id": lounge.id,
            "user_id": host.id,
            "event_type": "presence",
            "payload": {"action": "joined"},
        })

        assert batch_persistence.pending_count == 2

        # End the lounge via service
        await end_lounge(session, lounge.id, host)

        # Batch persistence queue for this lounge MUST be drained and persisted!
        assert batch_persistence.get_pending_chat_message(lounge.id, "msg_svc_pending_1") is None
        assert batch_persistence.pending_count == 0

        # Assert rows exist in database
        persisted_chat = (await session.execute(
            select(ChatMessage).where(ChatMessage.id == "msg_svc_pending_1")
        )).scalar_one_or_none()
        assert persisted_chat is not None
        assert persisted_chat.body == "Pending batched chat before lounge ends"

        persisted_event = (await session.execute(
            select(QueueEvent).where(QueueEvent.id == "qev_svc_pending_1")
        )).scalar_one_or_none()
        assert persisted_event is not None


@pytest.mark.asyncio
async def test_12_in_flight_chat_lookup_during_flush(ws_db_engine: AsyncEngine):
    """Test 12: In-flight message is retrievable during active flush commit I/O (Finding 3 fix)."""
    worker = BatchPersistenceWorker()
    worker.enqueue_chat_message({
        "id": "msg_inflight_1",
        "lounge_id": "lounge_alpha",
        "user_id": "u_test_host",
        "body": "In flight message",
    })

    original_session_maker = worker._get_session_maker()
    lookup_during_commit = None

    class MonitoredSession:
        def __init__(self, real_session_ctx):
            self._ctx = real_session_ctx
            self._real = None

        async def __aenter__(self):
            self._real = await self._ctx.__aenter__()
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return await self._ctx.__aexit__(exc_type, exc_val, exc_tb)

        def add_all(self, items):
            self._real.add_all(items)

        def add(self, item):
            self._real.add(item)

        async def commit(self):
            nonlocal lookup_during_commit
            # Check in-flight lookup while commit is actively running
            lookup_during_commit = worker.get_pending_chat_message("lounge_alpha", "msg_inflight_1")
            await self._real.commit()

        async def rollback(self):
            await self._real.rollback()

    worker._session_maker_factory = lambda: lambda: MonitoredSession(original_session_maker())

    flushed = await worker.flush()
    assert flushed == 1
    # Verify that during commit, message was found in _in_flight_chats
    assert lookup_during_commit is not None
    assert lookup_during_commit.id == "msg_inflight_1"
    # After commit finishes, it is removed from in-flight and exists in DB
    assert worker.get_pending_chat_message("lounge_alpha", "msg_inflight_1") is None


@pytest.mark.asyncio
async def test_13_queue_event_batching_via_vote(ws_db_engine: AsyncEngine):
    """Test 13: Vote QueueEvent is batched when worker is running (Finding 2 fix)."""
    from app.models.queue import QueueEntry
    from app.models.track import Track

    session_maker = get_session_maker()
    async with session_maker() as session:
        host = (await session.execute(select(User).where(User.email == "host@spotify-intern.local"))).scalar_one()
        lounge = (await session.execute(select(DJLounge).where(DJLounge.id == "lounge_alpha"))).scalar_one()

        # Ensure seed track exists
        track = Track(
            id="t_test_vote_1",
            title="Test Track",
            artist="Test Artist",
            bpm=120.0,
            camelot_key="8A",
            energy=0.8,
            valence=0.7,
            duration_seconds=180,
            audio_asset_url="/assets/audio/t_test_vote_1.wav",
        )
        session.add(track)

        # Add a queue entry to upvote
        qe = QueueEntry(
            id="qe_test_vote_1",
            lounge_id=lounge.id,
            track_id="t_test_vote_1",
            position=1000,
            vote_count=0,
            added_by_user_id=host.id,
        )
        session.add(qe)
        await session.commit()

        # Start batch persistence worker
        batch_persistence.start(flush_interval_seconds=10.0, max_batch_size=50)

        try:
            # Vote track
            events_before = (await session.execute(select(func.count(QueueEvent.id)))).scalar_one()
            await room_state_manager.vote_track(session, lounge.id, "t_test_vote_1", host.id)

            # Assert QueueEvent is batched in batch_persistence (not immediately written to DB)
            assert batch_persistence.pending_event_count >= 1
            events_after = (await session.execute(select(func.count(QueueEvent.id)))).scalar_one()
            assert events_after == events_before  # Not committed to DB yet!

            # Flush and verify QueueEvent is persisted
            await batch_persistence.flush()
            events_final = (await session.execute(select(func.count(QueueEvent.id)))).scalar_one()
            assert events_final == events_before + 1
        finally:
            await batch_persistence.stop()


@pytest.mark.asyncio
async def test_14_fastapi_lifespan_starts_and_stops_worker():
    """Test 14: FastAPI lifespan starts and stops batch_persistence worker (Finding 1 fix)."""
    from app.main import create_app

    app = create_app()
    assert batch_persistence.is_running is False

    # Simulate lifespan context
    async with app.router.lifespan_context(app):
        assert batch_persistence.is_running is True

    assert batch_persistence.is_running is False
