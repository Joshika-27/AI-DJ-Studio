"""Focused Unit and Concurrency Tests for RoomStateManager (Module 7 — REQ-020, REQ-021, REQ-027, REQ-054, REQ-055)."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.exceptions import AppException
from app.db.base import utc_now
from app.db.session import (
    configure_database,
    create_session_maker,
    drop_db,
    init_db,
)
from app.models.lounge import DJLounge
from app.models.queue import QueueEntry
from app.seeds.seed_data import seed_database
from app.services.room_state_manager import (
    DEFAULT_SPARSE_STEP,
    QueueTrackEntry,
    RoomStateManager,
    room_state_manager,
)

TEST_SEED_PASSWORD = "TestFixtureSeedPass!2026"


@pytest.fixture
async def state_mgr_db_engine() -> AsyncEngine:
    """Create an isolated in-memory sqlite database seeded with catalog tracks and users."""
    engine = configure_database("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    session_maker = create_session_maker(engine)
    async with session_maker() as session:
        await seed_database(
            session,
            seed_password=TEST_SEED_PASSWORD,
            app_env="testing",
        )
    yield engine
    await drop_db(engine)
    await engine.dispose()


@pytest.fixture(autouse=True)
def clean_state_manager_cache():
    """Ensure in-memory cache is clean before and after each test."""
    room_state_manager.clear_cache_for_testing()
    yield
    room_state_manager.clear_cache_for_testing()


@pytest.fixture
async def test_lounge_id(state_mgr_db_engine: AsyncEngine) -> str:
    """Create a sample DJ Lounge in scheduled status with seed track t_2001 in Now Playing."""
    session_maker = create_session_maker(state_mgr_db_engine)
    async with session_maker() as session:
        lounge = DJLounge(
            id="lg_test_queue_001",
            invite_code="SP-TEST01",
            host_user_id="u_501",
            vibe_profile="Deep House Sunset",
            current_track_id="t_2001",
            status="scheduled",
            max_participants=50,
            bpm_tolerance_pct=8.0,
            soundclash_enabled=True,
            ai_verbosity="normal",
            is_locked=False,
            queue_version=1,
        )
        session.add(lounge)
        await session.commit()
        return lounge.id


# ==============================================================================
# Baseline Core Functionality Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_1_three_zone_state_initialization(
    state_mgr_db_engine: AsyncEngine,
    test_lounge_id: str,
):
    """Verify 3-zone room initialization (Now Playing, Next Up, Soundclash Stage)."""
    session_maker = create_session_maker(state_mgr_db_engine)

    async with session_maker() as session:
        room = await room_state_manager.get_or_hydrate_room(session, test_lounge_id)
        assert room.lounge_id == test_lounge_id
        assert room.status == "scheduled"
        assert room.now_playing is not None
        assert room.now_playing.track_id == "t_2001"
        assert room.now_playing.title == "Night Drive"
        assert len(room.next_up) == 0

        # Zone 3: Soundclash Stage data structure placeholder
        assert room.soundclash_stage.status == "idle"
        assert room.soundclash_stage.candidate_track_ids == []
        assert room.soundclash_stage.active_battle_id is None

        # Schema serialization verification
        snapshot = room.to_schema()
        assert snapshot.loungeId == test_lounge_id
        assert snapshot.nowPlaying is not None
        assert snapshot.nowPlaying.trackId == "t_2001"
        assert snapshot.nextUp == []
        assert snapshot.soundclashStage.status == "idle"


@pytest.mark.asyncio
async def test_2_track_insertion_sparse_positions_and_duplicate_rejection(
    state_mgr_db_engine: AsyncEngine,
    test_lounge_id: str,
):
    """Verify track addition assigns sparse integer positions (1000, 2000...) and rejects duplicates."""
    session_maker = create_session_maker(state_mgr_db_engine)

    async with session_maker() as session:
        # Add track 1: t_2002 -> position 1000
        room = await room_state_manager.add_track(
            db=session,
            lounge_id=test_lounge_id,
            track_id="t_2002",
            user_id="u_502",
        )
        assert len(room.next_up) == 1
        assert room.next_up[0].track_id == "t_2002"
        assert room.next_up[0].position == 1000
        assert room.next_up[0].insertion_seq == 1

        # Add track 2: t_2003 -> position 2000
        room = await room_state_manager.add_track(
            db=session,
            lounge_id=test_lounge_id,
            track_id="t_2003",
            user_id="u_503",
        )
        assert len(room.next_up) == 2
        assert room.next_up[1].track_id == "t_2003"
        assert room.next_up[1].position == 2000
        assert room.next_up[1].insertion_seq == 2

        # Duplicate check 1: Reject track already in Now Playing (t_2001)
        with pytest.raises(AppException) as exc_np:
            await room_state_manager.add_track(
                db=session,
                lounge_id=test_lounge_id,
                track_id="t_2001",
                user_id="u_502",
            )
        assert exc_np.value.status_code == 400
        assert exc_np.value.code == "DUPLICATE_TRACK"

        # Duplicate check 2: Reject track already in Next Up (t_2003)
        with pytest.raises(AppException) as exc_queue:
            await room_state_manager.add_track(
                db=session,
                lounge_id=test_lounge_id,
                track_id="t_2003",
                user_id="u_502",
            )
        assert exc_queue.value.status_code == 400
        assert exc_queue.value.code == "DUPLICATE_TRACK"


# ==============================================================================
# Audit Remediation: Eight Mandatory Test Cases
# ==============================================================================

@pytest.mark.asyncio
async def test_case_1_restart_hydration_preserves_insertion_order(
    state_mgr_db_engine: AsyncEngine,
    test_lounge_id: str,
):
    """Case 1: Reconstruct immutable insertion order using (created_at, id) after restart,
    preserving deterministic tie-breaking even after vote reordering and position changes.
    """
    session_maker = create_session_maker(state_mgr_db_engine)
    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Directly seed 3 entries into DB with distinct timestamps and positions:
    # Arrival 1: t_2002 at 12:00:00, arrival 2: t_2003 at 12:01:00, arrival 3: t_2004 at 12:02:00.
    # Suppose prior votes moved t_2004 to position 500, t_2002 to 1000, t_2003 to 2000.
    async with session_maker() as session:
        e1 = QueueEntry(
            id="qe_test_01",
            lounge_id=test_lounge_id,
            track_id="t_2002",
            position=1000,
            vote_count=1,
            added_by_user_id="u_501",
            created_at=base_time,
        )
        e2 = QueueEntry(
            id="qe_test_02",
            lounge_id=test_lounge_id,
            track_id="t_2003",
            position=2000,
            vote_count=0,
            added_by_user_id="u_502",
            created_at=base_time + timedelta(minutes=1),
        )
        e3 = QueueEntry(
            id="qe_test_03",
            lounge_id=test_lounge_id,
            track_id="t_2004",
            position=500,
            vote_count=1,
            added_by_user_id="u_503",
            created_at=base_time + timedelta(minutes=2),
        )
        session.add_all([e1, e2, e3])
        await session.commit()

    # Simulate node restart by clearing in-memory cache
    room_state_manager.clear_cache_for_testing()

    async with session_maker() as session:
        # Hydrate after restart
        room = await room_state_manager.get_or_hydrate_room(session, test_lounge_id)
        assert len(room.next_up) == 3

        # Verify insertion_seq was reconstructed strictly by (created_at, id)
        seq_map = {e.track_id: e.insertion_seq for e in room.next_up}
        assert seq_map["t_2002"] == 1
        assert seq_map["t_2003"] == 2
        assert seq_map["t_2004"] == 3

        # Now test deterministic tie-break: both t_2002 and t_2004 have 1 vote.
        # Since t_2002 has insertion_seq=1 and t_2004 has insertion_seq=3,
        # sort_next_up_deterministically MUST place t_2002 before t_2004!
        sorted_entries = room_state_manager.sort_next_up_deterministically(room.next_up)
        assert sorted_entries[0].track_id == "t_2002"
        assert sorted_entries[1].track_id == "t_2004"
        assert sorted_entries[2].track_id == "t_2003"


@pytest.mark.asyncio
async def test_case_2_force_segue_rollback_restores_both_queue_zones(
    state_mgr_db_engine: AsyncEngine,
    test_lounge_id: str,
):
    """Case 2: Verify force_segue failure cleanly restores BOTH Now Playing and Next Up zones."""
    session_maker = create_session_maker(state_mgr_db_engine)

    async with session_maker() as session:
        await room_state_manager.add_track(session, test_lounge_id, "t_2002", "u_501")
        await room_state_manager.add_track(session, test_lounge_id, "t_2003", "u_502")

        initial_room = await room_state_manager.get_or_hydrate_room(session, test_lounge_id)
        assert initial_room.now_playing.track_id == "t_2001"
        assert [e.track_id for e in initial_room.next_up] == ["t_2002", "t_2003"]
        initial_version = initial_room.queue_version

        # Mock db.commit to raise an exception during force_segue
        with patch.object(session, "commit", side_effect=RuntimeError("Simulated Segue DB Failure")):
            with pytest.raises(AppException) as exc_info:
                await room_state_manager.force_segue(
                    db=session,
                    lounge_id=test_lounge_id,
                    user_id="u_501",
                    target_track_id=None,
                )
            assert exc_info.value.status_code == 500
            assert exc_info.value.code == "PERSISTENCE_FAILED"

        # Verify rollback restored BOTH zones completely
        rolled_back = await room_state_manager.get_or_hydrate_room(session, test_lounge_id)
        assert rolled_back.now_playing.track_id == "t_2001"
        assert [e.track_id for e in rolled_back.next_up] == ["t_2002", "t_2003"]
        assert rolled_back.queue_version == initial_version


@pytest.mark.asyncio
async def test_case_3_locked_lounge_rejects_additions(
    state_mgr_db_engine: AsyncEngine,
    test_lounge_id: str,
):
    """Case 3: Reject track additions to locked lounges with HTTP 403 ROOM_LOCKED."""
    session_maker = create_session_maker(state_mgr_db_engine)

    # Lock the lounge
    async with session_maker() as session:
        await session.execute(
            update(DJLounge)
            .where(DJLounge.id == test_lounge_id)
            .values(is_locked=True)
        )
        await session.commit()

    # Attempt to add track to locked room
    async with session_maker() as session:
        with pytest.raises(AppException) as exc_info:
            await room_state_manager.add_track(
                db=session,
                lounge_id=test_lounge_id,
                track_id="t_2002",
                user_id="u_502",
            )
        assert exc_info.value.status_code == 403
        assert exc_info.value.code == "ROOM_LOCKED"
        assert "locked by Host" in exc_info.value.message


@pytest.mark.asyncio
async def test_case_4_external_changes_invalidate_stale_cache(
    state_mgr_db_engine: AsyncEngine,
    test_lounge_id: str,
):
    """Case 4: External database changes (queue_version, current_track_id) invalidate stale cache."""
    session_maker = create_session_maker(state_mgr_db_engine)

    # 1. Warm in-memory cache
    async with session_maker() as session:
        cached_room = await room_state_manager.get_or_hydrate_room(session, test_lounge_id)
        assert cached_room.queue_version == 1
        assert cached_room.now_playing.track_id == "t_2001"

    # 2. Simulate external worker updating DJLounge in PostgreSQL directly
    async with session_maker() as session:
        await session.execute(
            update(DJLounge)
            .where(DJLounge.id == test_lounge_id)
            .values(queue_version=88, current_track_id="t_2004")
        )
        await session.commit()

    # 3. Request room without force_refresh; stale cache must detect divergence and reload
    async with session_maker() as session:
        fresh_room = await room_state_manager.get_or_hydrate_room(
            db=session,
            lounge_id=test_lounge_id,
            force_refresh=False,
        )
        assert fresh_room.queue_version == 88
        assert fresh_room.now_playing.track_id == "t_2004"


@pytest.mark.asyncio
async def test_case_5_mixed_concurrent_add_vote_promote_operations(
    state_mgr_db_engine: AsyncEngine,
    test_lounge_id: str,
):
    """Case 5: Simultaneous mixed coroutines (add, vote, promote) serialize deterministically."""
    session_maker = create_session_maker(state_mgr_db_engine)

    # Pre-seed 2 tracks: t_2002, t_2003
    async with session_maker() as session:
        await room_state_manager.add_track(session, test_lounge_id, "t_2002", "u_501")
        await room_state_manager.add_track(session, test_lounge_id, "t_2003", "u_502")

    # Launch mixed concurrent operations:
    # Client 1: add t_2004
    # Client 2: vote t_2002
    # Client 3: vote t_2003
    # Client 4: add t_2005
    # Client 5: promote t_2003 to head
    async def op_add(tid: str, uid: str):
        async with session_maker() as session:
            await room_state_manager.add_track(session, test_lounge_id, tid, uid)

    async def op_vote(tid: str, uid: str):
        async with session_maker() as session:
            await room_state_manager.vote_track(session, test_lounge_id, tid, uid)

    async def op_promote(tid: str, uid: str):
        async with session_maker() as session:
            await room_state_manager.promote_track(session, test_lounge_id, tid, uid)

    tasks = [
        op_add("t_2004", "u_503"),
        op_vote("t_2002", "u_501"),
        op_vote("t_2003", "u_502"),
        op_add("t_2005", "u_504"),
        op_promote("t_2003", "u_501"),
    ]
    await asyncio.gather(*tasks)

    # Verify room state integrity
    async with session_maker() as session:
        room = await room_state_manager.get_or_hydrate_room(session, test_lounge_id)
        assert len(room.next_up) == 4
        # All positions must be strictly positive and strictly ascending
        positions = [e.position for e in room.next_up]
        assert all(p > 0 for p in positions)
        assert positions == sorted(positions)
        assert len(set(positions)) == 4


@pytest.mark.asyncio
async def test_case_6_head_promotion_collision_and_respacing(
    state_mgr_db_engine: AsyncEngine,
    test_lounge_id: str,
):
    """Case 6: Head promotion repeatedly halves head position and cleanly triggers atomic re-spacing."""
    session_maker = create_session_maker(state_mgr_db_engine)

    async with session_maker() as session:
        await room_state_manager.add_track(session, test_lounge_id, "t_2002", "u_501")
        await room_state_manager.add_track(session, test_lounge_id, "t_2003", "u_501")
        await room_state_manager.add_track(session, test_lounge_id, "t_2004", "u_501")

        # Initial head position: 1000
        room = await room_state_manager.get_or_hydrate_room(session, test_lounge_id)
        assert room.next_up[0].position == 1000

        # Promote tail (t_2004) to head: 1000 // 2 = 500
        room = await room_state_manager.promote_track(session, test_lounge_id, "t_2004", "u_501")
        assert room.next_up[0].position == 500

        # Keep promoting tail until head position collapses <= 1 and triggers re-spacing
        respaced = False
        for _ in range(15):
            tail = room.next_up[-1].track_id
            room = await room_state_manager.promote_track(session, test_lounge_id, tail, "u_501")
            positions = [e.position for e in room.next_up]
            if positions == [1000, 2000, 3000]:
                respaced = True
                break

        assert respaced is True
        assert [e.position for e in room.next_up] == [1000, 2000, 3000]


@pytest.mark.asyncio
async def test_case_7_queue_version_increments_and_rolls_back_on_failure(
    state_mgr_db_engine: AsyncEngine,
    test_lounge_id: str,
):
    """Case 7: Persistent queue_version increments on successful mutations and rolls back on failure."""
    session_maker = create_session_maker(state_mgr_db_engine)

    async with session_maker() as session:
        room_initial = await room_state_manager.get_or_hydrate_room(session, test_lounge_id)
        assert room_initial.queue_version == 1

        # Mutation 1: add_track increments queue_version to 2
        room_v2 = await room_state_manager.add_track(session, test_lounge_id, "t_2002", "u_501")
        assert room_v2.queue_version == 2

        # Check DB persisted queue_version = 2
        lounge_db = (
            await session.execute(select(DJLounge).where(DJLounge.id == test_lounge_id))
        ).scalar_one()
        assert lounge_db.queue_version == 2

        # Mutation 2: simulated failure during vote_track
        with patch.object(session, "commit", side_effect=RuntimeError("Simulated Failure")):
            with pytest.raises(AppException) as exc_info:
                await room_state_manager.vote_track(session, test_lounge_id, "t_2002", "u_501")
            assert exc_info.value.code == "PERSISTENCE_FAILED"

        # Verify in-memory room rolled back to queue_version 2
        room_after_fail = await room_state_manager.get_or_hydrate_room(session, test_lounge_id)
        assert room_after_fail.queue_version == 2

        # Mutation 3: subsequent successful add_track increments cleanly to 3
        room_v3 = await room_state_manager.add_track(session, test_lounge_id, "t_2003", "u_502")
        assert room_v3.queue_version == 3

        lounge_db_final = (
            await session.execute(select(DJLounge).where(DJLounge.id == test_lounge_id))
        ).scalar_one()
        assert lounge_db_final.queue_version == 3


@pytest.mark.asyncio
async def test_case_8_ordinary_votes_do_not_unnecessarily_update_every_queue_entry(
    state_mgr_db_engine: AsyncEngine,
    test_lounge_id: str,
):
    """Case 8: Ordinary votes that do not change relative queue order update only the voted entry in DB."""
    session_maker = create_session_maker(state_mgr_db_engine)

    async with session_maker() as session:
        # Seed 4 tracks: t_2002 (1000), t_2003 (2000), t_2004 (3000), t_2005 (4000)
        await room_state_manager.add_track(session, test_lounge_id, "t_2002", "u_501")
        await room_state_manager.add_track(session, test_lounge_id, "t_2003", "u_501")
        await room_state_manager.add_track(session, test_lounge_id, "t_2004", "u_501")
        await room_state_manager.add_track(session, test_lounge_id, "t_2005", "u_501")

        # Spy on session.execute during vote_track for t_2002 (head track)
        # Since t_2002 is already at head, its relative index does not change.
        # It should execute exactly ONE update statement on QueueEntry (updating vote_count for t_2002),
        # NOT 4 updates!
        original_execute = session.execute
        executed_stmts = []

        async def execute_spy(stmt, *args, **kwargs):
            executed_stmts.append(stmt)
            return await original_execute(stmt, *args, **kwargs)

        with patch.object(session, "execute", side_effect=execute_spy):
            await room_state_manager.vote_track(session, test_lounge_id, "t_2002", "u_502")

        # Filter the executed statements for QueueEntry updates
        queue_updates = [
            s for s in executed_stmts
            if hasattr(s, "is_update") and s.is_update and hasattr(s, "table") and s.table.name == "queue_entries"
        ]

        # Verify exactly 1 QueueEntry update was executed (only the target track!)
        assert len(queue_updates) == 1
