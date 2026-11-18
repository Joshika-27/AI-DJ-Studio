"""Module 2 automated tests: PostgreSQL/Async SQLAlchemy setup, session management, 10 relational models, relationships, indexes, and constraints."""

from datetime import timedelta

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.db.base import Base, utc_now
from app.db.session import (
    _normalize_async_database_url,
    build_async_engine,
    check_db_connection,
    configure_database,
    create_session_maker,
    drop_db,
    get_db,
    init_db,
)
from app.main import create_app
from app.models import (
    ChatMessage,
    DJLounge,
    Interaction,
    QueueEntry,
    QueueEvent,
    SoundclashBattle,
    SoundclashCandidate,
    Track,
    TrackTimelineEvent,
    User,
)


@pytest.fixture
async def db_engine() -> AsyncEngine:
    """Create an isolated in-memory async database engine with all Module 2 tables initialized."""
    engine = configure_database("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    yield engine
    await drop_db(engine)
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncSession:
    """Yield an AsyncSession bound to the initialized test database."""
    session_maker = create_session_maker(db_engine)
    async with session_maker() as session:
        yield session


async def test_all_ten_relational_tables_and_indexes_registered(
    db_engine: AsyncEngine,
) -> None:
    """Verify all 10 relational tables and required composite indexes exist in metadata (REQ-051, REQ-053)."""
    expected_tables = {
        "users",
        "tracks",
        "track_timeline_events",
        "dj_lounges",
        "queue_entries",
        "queue_events",
        "soundclash_battles",
        "soundclash_candidates",
        "chat_messages",
        "interactions",
    }
    assert expected_tables.issubset(set(Base.metadata.tables.keys()))

    tracks_indexes = {idx.name for idx in Base.metadata.tables["tracks"].indexes}
    assert "idx_tracks_acoustic" in tracks_indexes

    queue_indexes = {idx.name for idx in Base.metadata.tables["queue_entries"].indexes}
    assert "idx_queue_lounge_position" in queue_indexes

    event_indexes = {idx.name for idx in Base.metadata.tables["queue_events"].indexes}
    assert "idx_queue_events_lounge_ts" in event_indexes


async def test_end_to_end_crud_and_relationships_across_all_models(
    db_session: AsyncSession,
) -> None:
    """Verify CRUD persistence, sparse queue ordering, and ORM relationships across all 10 models."""
    host = User(
        id="u_501",
        email="host@spotify-intern.local",
        password_hash="hashed_secret_1",
        display_name="DJ Nova",
        role="host",
        preferences={"genreAffinity": ["High-Energy EDM"]},
    )
    listener = User(
        id="u_502",
        email="listener@spotify-intern.local",
        password_hash="hashed_secret_2",
        display_name="Party Guest",
        role="listener",
    )

    track1 = Track(
        id="t_2001",
        title="Night Drive",
        artist="Nova Circuit",
        duration_seconds=215,
        audio_asset_url="/assets/audio/night_drive.wav",
        bpm=124.0,
        camelot_key="8A",
        energy=0.78,
        valence=0.65,
    )
    track2 = Track(
        id="t_2002",
        title="Neon Horizon",
        artist="Pulse Echo",
        duration_seconds=198,
        audio_asset_url="/assets/audio/neon_horizon.wav",
        bpm=126.0,
        camelot_key="8B",
        energy=0.82,
        valence=0.71,
    )

    evt1 = TrackTimelineEvent(
        id="evt_5541",
        track_id="t_2001",
        ts_seconds=12,
        event_type="sample_credit",
        text_body="Vocal chop sampled from a 1981 soul record.",
        payload={"text": "Vocal chop sampled from a 1981 soul record."},
    )
    evt2 = TrackTimelineEvent(
        id="evt_5542",
        track_id="t_2001",
        ts_seconds=90,
        event_type="trivia",
        text_body="Produced during an all-night session in Berlin.",
        payload={"text": "Produced during an all-night session in Berlin."},
    )

    lounge = DJLounge(
        id="lg_77120",
        invite_code="SP-9X2L",
        host_user_id="u_501",
        vibe_profile="High-Energy EDM",
        current_track_id="t_2001",
        status="live",
        max_participants=40,
        bpm_tolerance_pct=8.0,
        soundclash_enabled=True,
        ai_verbosity="normal",
        peak_participants=12,
    )

    # Sparse integer positions (1000, 2000) per REQ-054
    q_entry1 = QueueEntry(
        id="qe_1",
        lounge_id="lg_77120",
        track_id="t_2002",
        position=2000,
        vote_count=1,
        added_by_user_id="u_502",
    )
    q_entry2 = QueueEntry(
        id="qe_2",
        lounge_id="lg_77120",
        track_id="t_2001",
        position=1000,
        vote_count=4,
        added_by_user_id="u_501",
    )

    battle = SoundclashBattle(
        id="bt_301",
        lounge_id="lg_77120",
        closes_at=utc_now() + timedelta(seconds=30),
        status="open",
    )
    candidate1 = SoundclashCandidate(
        id="sc_1",
        battle_id="bt_301",
        track_id="t_2001",
        submitted_by_user_id="u_501",
        vote_count=3,
    )
    candidate2 = SoundclashCandidate(
        id="sc_2",
        battle_id="bt_301",
        track_id="t_2002",
        submitted_by_user_id="u_502",
        vote_count=5,
    )

    chat_msg = ChatMessage(
        id="msg_901",
        lounge_id="lg_77120",
        user_id="u_502",
        msg_type="chat",
        body="This drop is insane!",
        sentiment_score=0.92,
        sentiment_bucket="positive",
        ts_seconds=145,
    )
    ai_msg = ChatMessage(
        id="msg_902",
        lounge_id="lg_77120",
        user_id=None,
        msg_type="dj_announcement",
        body="Sliding into Neon Horizon at 126 BPM.",
        source_events=["evt_5541"],
        ts_seconds=198,
    )

    q_event = QueueEvent(
        id="qev_1",
        lounge_id="lg_77120",
        user_id="u_502",
        event_type="vote",
        payload={"trackId": "t_2001", "newPosition": 1000},
        ts=1690000000123,
    )

    interaction = Interaction(
        id="int_1",
        user_id="u_502",
        track_id="t_2001",
        lounge_id="lg_77120",
        played_seconds=210,
        completed=True,
        liked=True,
        co_listened_with=["u_501"],
    )

    db_session.add_all(
        [
            host,
            listener,
            track1,
            track2,
            evt1,
            evt2,
            lounge,
            q_entry1,
            q_entry2,
            battle,
            candidate1,
            candidate2,
            chat_msg,
            ai_msg,
            q_event,
            interaction,
        ]
    )
    await db_session.commit()

    # Query back with relationships loaded
    stmt = (
        select(DJLounge)
        .where(DJLounge.id == "lg_77120")
        .options(
            selectinload(DJLounge.host_user),
            selectinload(DJLounge.current_track).selectinload(Track.timeline_events),
            selectinload(DJLounge.queue_entries),
            selectinload(DJLounge.soundclash_battles).selectinload(
                SoundclashBattle.candidates
            ),
            selectinload(DJLounge.chat_messages),
            selectinload(DJLounge.queue_events),
        )
    )
    loaded_lounge = (await db_session.execute(stmt)).scalar_one()

    assert loaded_lounge.invite_code == "SP-9X2L"
    assert loaded_lounge.host_user.display_name == "DJ Nova"
    assert loaded_lounge.current_track is not None
    assert loaded_lounge.current_track.title == "Night Drive"
    assert [e.id for e in loaded_lounge.current_track.timeline_events] == [
        "evt_5541",
        "evt_5542",
    ]
    # Queue entries ordered by sparse integer position (1000 then 2000)
    assert [q.position for q in loaded_lounge.queue_entries] == [1000, 2000]
    assert len(loaded_lounge.soundclash_battles) == 1
    assert len(loaded_lounge.soundclash_battles[0].candidates) == 2
    assert len(loaded_lounge.chat_messages) == 2
    assert loaded_lounge.chat_messages[1].source_events == ["evt_5541"]
    assert len(loaded_lounge.queue_events) == 1


async def test_database_check_constraints_enforced(db_session: AsyncSession) -> None:
    """Verify CheckConstraints reject invalid roles, BPM <= 0, invalid Camelot keys, and out-of-range energy (REQ-030, REQ-049, REQ-070)."""
    # 1. Invalid user role
    db_session.add(
        User(
            email="badrole@test.local",
            password_hash="hash",
            display_name="Bad Role",
            role="unauthorized_role",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    # 2. Invalid BPM <= 0
    db_session.add(
        Track(
            title="Bad BPM",
            artist="Test",
            duration_seconds=180,
            audio_asset_url="/assets/audio/test.wav",
            bpm=-10.0,
            camelot_key="8A",
            energy=0.5,
            valence=0.5,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    # 3. Invalid Camelot key (not in 1A..12A, 1B..12B)
    db_session.add(
        Track(
            title="Bad Key",
            artist="Test",
            duration_seconds=180,
            audio_asset_url="/assets/audio/test.wav",
            bpm=120.0,
            camelot_key="13A",
            energy=0.5,
            valence=0.5,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    # 4. Invalid energy > 1.0
    db_session.add(
        Track(
            title="Bad Energy",
            artist="Test",
            duration_seconds=180,
            audio_asset_url="/assets/audio/test.wav",
            bpm=120.0,
            camelot_key="8A",
            energy=1.4,
            valence=0.5,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_cascade_deletes_and_session_dependency(db_engine: AsyncEngine) -> None:
    """Verify ON DELETE CASCADE for timeline events and get_db session dependency lifecycle."""
    async for session in get_db():
        track = Track(
            id="t_cascade",
            title="Cascade Track",
            artist="Cascade Artist",
            duration_seconds=120,
            audio_asset_url="/assets/audio/c.wav",
            bpm=128.0,
            camelot_key="12A",
            energy=0.9,
            valence=0.8,
        )
        evt = TrackTimelineEvent(
            id="evt_cascade",
            track_id="t_cascade",
            ts_seconds=15,
            event_type="glossary",
            text_body="Sidechain compression.",
            payload={"text": "Sidechain compression."},
        )
        session.add_all([track, evt])
        await session.commit()

        await session.delete(track)
        await session.commit()

        remaining = (
            await session.execute(
                select(TrackTimelineEvent).where(
                    TrackTimelineEvent.id == "evt_cascade"
                )
            )
        ).scalar_one_or_none()
        assert remaining is None


async def test_database_url_normalization_and_health_integration(
    db_engine: AsyncEngine,
) -> None:
    """Verify PostgreSQL asyncpg URL normalization and live DB health reporting on GET /api/v1/health."""
    assert (
        _normalize_async_database_url("postgresql://user:pass@localhost:5432/aidj")
        == "postgresql+asyncpg://user:pass@localhost:5432/aidj"
    )
    assert (
        _normalize_async_database_url("postgres://user:pass@localhost:5432/aidj")
        == "postgresql+asyncpg://user:pass@localhost:5432/aidj"
    )

    pg_engine = build_async_engine("postgresql+asyncpg://postgres:postgres@localhost:5432/aidj_studio")
    assert pg_engine.dialect.name == "postgresql"
    await pg_engine.dispose()

    health_probe = await check_db_connection(db_engine)
    assert health_probe["status"] == "connected"

    app = create_app(Settings(_env_file=None, APP_ENV="testing"))
    with TestClient(app) as client:
        res = client.get("/api/v1/health")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "ok"
        assert body["db"] == "connected"
