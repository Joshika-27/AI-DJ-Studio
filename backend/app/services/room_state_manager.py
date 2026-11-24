"""Authoritative 3-zone in-memory Queue State Machine & Room State Manager (Module 7 — REQ-020, REQ-021, REQ-027, REQ-054, REQ-055, REQ-070).

CONCURRENCY BOUNDARY & PROCESS SAFETY DOCUMENTATION:
=====================================================
1. Process Event-Loop Boundary:
   - Each lounge is isolated by an in-memory `asyncio.Lock` keyed by `lounge_id`.
   - This ensures strict concurrency serialization for all async coroutines executing
     on the current Python process event loop. Simultaneous operations on the same
     room queue (e.g. concurrent votes) are serialized deterministically (REQ-027).

2. Multi-Worker / Multi-Process PostgreSQL Boundary:
   - `asyncio.Lock` alone DOES NOT coordinate across multiple operating-system processes,
     Gunicorn/Uvicorn workers, or horizontally scaled containers.
   - For multi-worker deployments:
     a) PostgreSQL Row-Level Locking: All queue mutations acquire a row lock on the
        corresponding `dj_lounges` row using `SELECT ... FOR UPDATE` before reading or
        modifying queue state. This guarantees consistent transaction and lock ordering
        across all database workers.
     b) Transactional Cache Versioning: `DJLounge` maintains a persistent `queue_version`
        column (integer, default 1) incremented transactionally with every queue mutation.
        Before performing any operation, `RoomStateManager` verifies the in-memory
        `queue_version`, `current_track_id`, `is_locked`, and `status` against the locked
        lounge record. If divergence is detected (e.g. state was mutated by another worker
        or node restart), the in-memory cache is automatically re-hydrated from PostgreSQL.
     c) Read Consistency: Read-only calls (`get_or_hydrate_room`) check `queue_version`
        and lounge attributes against the database without acquiring exclusive write locks.
     d) Deployment Limitations: Cross-worker WebSocket notifications require an external
        message broker (e.g., Redis pub/sub) in horizontal multi-worker clusters (Module 8).
        Without a pub/sub layer, workers detect external mutations lazily upon their next
        database read or write operation.

3. Transactional Consistency & Rollback:
   - Before applying any in-memory mutation and database changes, `RoomStateManager` takes
     an atomic in-memory snapshot (`state.clone()`).
   - If the database transaction fails (integrity constraint, deadlock, or connection error),
     the database is rolled back AND the in-memory room state is immediately restored to the
     pre-mutation snapshot, ensuring zero divergence between memory and database across both
     Now Playing and Next Up zones.
"""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from fastapi import status
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AppException
from app.core.logging import get_logger
from app.db.base import utc_now
from app.models.lounge import DJLounge
from app.models.queue import QueueEntry, QueueEvent
from app.models.track import Track
from app.schemas.queue import (
    NowPlayingTrackSchema,
    QueueItemSchema,
    QueueResponse,
    SoundclashStageSchema,
    ThreeZoneQueueStateSchema,
    format_iso_utc,
)
from app.schemas.track import _format_bpm_value

logger = get_logger("services.room_state_manager")

# Canonical sparse integer increment (REQ-054)
DEFAULT_SPARSE_STEP = 1000
MIN_SPARSE_HEAD = 1


# ==============================================================================
# In-Memory State Data Structures
# ==============================================================================

@dataclass
class QueueTrackEntry:
    """An individual queued track in the Next Up zone."""

    queue_entry_id: str
    track_id: str
    title: str
    artist: str
    duration_seconds: int
    audio_asset_url: str
    bpm: float | int
    camelot_key: str
    energy: float
    valence: float
    position: int
    vote_count: int
    added_by_user_id: str
    insertion_seq: int  # Immutable per-room insertion order for deterministic tie-breaking
    created_at: datetime  # Immutable timestamp

    def clone(self) -> QueueTrackEntry:
        return copy.copy(self)

    def to_schema(self) -> QueueItemSchema:
        return QueueItemSchema(
            queueEntryId=self.queue_entry_id,
            trackId=self.track_id,
            title=self.title,
            artist=self.artist,
            bpm=_format_bpm_value(self.bpm),
            camelotKey=self.camelot_key,
            energy=round(float(self.energy), 4),
            valence=round(float(self.valence), 4),
            durationSeconds=self.duration_seconds,
            audioAssetUrl=self.audio_asset_url,
            position=self.position,
            voteCount=self.vote_count,
            addedByUserId=self.added_by_user_id,
            insertionSeq=self.insertion_seq,
            createdAt=format_iso_utc(self.created_at) or "",
        )


@dataclass
class NowPlayingTrack:
    """Track currently active in the Now Playing zone."""

    track_id: str
    title: str
    artist: str
    duration_seconds: int
    audio_asset_url: str
    bpm: float | int
    camelot_key: str
    energy: float
    valence: float
    started_at: Optional[datetime] = None

    def clone(self) -> NowPlayingTrack:
        return copy.copy(self)

    def to_schema(self) -> NowPlayingTrackSchema:
        return NowPlayingTrackSchema(
            trackId=self.track_id,
            title=self.title,
            artist=self.artist,
            durationSeconds=self.duration_seconds,
            audioAssetUrl=self.audio_asset_url,
            bpm=_format_bpm_value(self.bpm),
            camelotKey=self.camelot_key,
            energy=round(float(self.energy), 4),
            valence=round(float(self.valence), 4),
            startedAt=format_iso_utc(self.started_at),
        )


@dataclass
class SoundclashStageState:
    """Soundclash Stage state (Module 7: structural placeholder only; battle logic in Module 12)."""

    status: str = "idle"
    candidate_track_ids: List[str] = field(default_factory=list)
    active_battle_id: Optional[str] = None

    def clone(self) -> SoundclashStageState:
        return SoundclashStageState(
            status=self.status,
            candidate_track_ids=list(self.candidate_track_ids),
            active_battle_id=self.active_battle_id,
        )

    def to_schema(self) -> SoundclashStageSchema:
        return SoundclashStageSchema(
            status=self.status,
            candidateTrackIds=list(self.candidate_track_ids),
            activeBattleId=self.active_battle_id,
        )


@dataclass
class RoomState:
    """Authoritative 3-zone room state maintained in memory per lounge (REQ-020, REQ-055)."""

    lounge_id: str
    status: str
    is_locked: bool = False
    queue_version: int = 1
    now_playing: Optional[NowPlayingTrack] = None
    next_up: List[QueueTrackEntry] = field(default_factory=list)
    soundclash_stage: SoundclashStageState = field(default_factory=SoundclashStageState)
    version: int = 1
    next_insertion_seq: int = 1
    updated_at: datetime = field(default_factory=utc_now)
    last_db_count: int = 0

    def clone(self) -> RoomState:
        """Deep clone state for transactional snapshotting and rollback."""
        return RoomState(
            lounge_id=self.lounge_id,
            status=self.status,
            is_locked=self.is_locked,
            queue_version=self.queue_version,
            now_playing=self.now_playing.clone() if self.now_playing else None,
            next_up=[entry.clone() for entry in self.next_up],
            soundclash_stage=self.soundclash_stage.clone(),
            version=self.version,
            next_insertion_seq=self.next_insertion_seq,
            updated_at=self.updated_at,
            last_db_count=self.last_db_count,
        )

    def to_schema(self) -> ThreeZoneQueueStateSchema:
        return ThreeZoneQueueStateSchema(
            loungeId=self.lounge_id,
            status=self.status,
            nowPlaying=self.now_playing.to_schema() if self.now_playing else None,
            nextUp=[entry.to_schema() for entry in self.next_up],
            soundclashStage=self.soundclash_stage.to_schema(),
            version=self.queue_version,
            updatedAt=format_iso_utc(self.updated_at) or "",
        )

    def to_queue_response(self) -> QueueResponse:
        return QueueResponse(
            loungeId=self.lounge_id,
            queue=[entry.to_schema() for entry in self.next_up],
            nowPlaying=self.now_playing.to_schema() if self.now_playing else None,
            version=self.queue_version,
        )


# ==============================================================================
# Room State Manager
# ==============================================================================

class RoomStateManager:
    """Singleton service managing in-memory 3-zone queue states and database synchronization."""

    def __init__(self) -> None:
        self._rooms: Dict[str, RoomState] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    def get_room_lock(self, lounge_id: str) -> asyncio.Lock:
        """Retrieve or create an in-process asyncio.Lock for the specified lounge."""
        lock = self._locks.get(lounge_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[lounge_id] = lock
        return lock

    def clear_cache_for_testing(self) -> None:
        """Test helper to clear all in-memory room states and locks."""
        self._rooms.clear()
        self._locks.clear()

    # --------------------------------------------------------------------------
    # Deterministic Sorting & Sparse Positioning Algorithms
    # --------------------------------------------------------------------------

    @staticmethod
    def sort_next_up_deterministically(entries: List[QueueTrackEntry]) -> List[QueueTrackEntry]:
        """Sort Next Up queue deterministically: vote_count DESC, then immutable insertion_seq ASC.

        Preserves original insertion order independently of mutable sparse positions (REQ-021, REQ-027).
        """
        return sorted(
            entries,
            key=lambda e: (-e.vote_count, e.insertion_seq, e.created_at, e.queue_entry_id),
        )

    @staticmethod
    def re_space_positions(entries: List[QueueTrackEntry]) -> None:
        """Atomic re-spacing pass: assigns canonical sparse positions (1000, 2000, 3000...)
        preserving the current resolved relative ordering (REQ-054).
        """
        for idx, entry in enumerate(entries):
            entry.position = (idx + 1) * DEFAULT_SPARSE_STEP

    # --------------------------------------------------------------------------
    # Hydration from Database
    # --------------------------------------------------------------------------

    async def get_or_hydrate_room(
        self,
        db: AsyncSession,
        lounge_id: str,
        force_refresh: bool = False,
    ) -> RoomState:
        """Hydrate or retrieve the authoritative RoomState for a lounge.

        Verifies persistent queue_version and state freshness to avoid stale cache.
        """
        async with self.get_room_lock(lounge_id):
            room, _ = await self._get_or_hydrate_room_unlocked(
                db=db,
                lounge_id=lounge_id,
                force_refresh=force_refresh,
            )
            return room

    async def _get_or_hydrate_room_unlocked(
        self,
        db: AsyncSession,
        lounge_id: str,
        force_refresh: bool = False,
        locked_lounge: Optional[DJLounge] = None,
    ) -> tuple[RoomState, DJLounge]:
        """Internal unlocked hydration method (must be called with lounge lock held)."""
        existing = self._rooms.get(lounge_id)

        # Retrieve lounge record if not already loaded with row lock
        if locked_lounge is not None:
            lounge = locked_lounge
        else:
            lounge_stmt = (
                select(DJLounge)
                .where(DJLounge.id == lounge_id)
                .options(
                    selectinload(DJLounge.current_track),
                    selectinload(DJLounge.queue_entries).selectinload(QueueEntry.track),
                )
            )
            lounge = (await db.execute(lounge_stmt)).scalar_one_or_none()
            if lounge is None:
                raise AppException(
                    message=f"DJ Lounge '{lounge_id}' was not found.",
                    status_code=status.HTTP_404_NOT_FOUND,
                    code="LOUNGE_NOT_FOUND",
                )

        current_qv = getattr(lounge, "queue_version", 1) or 1
        now_playing_id = existing.now_playing.track_id if (existing and existing.now_playing) else None

        # Check for DB divergence / stale cache if not force_refresh
        if existing is not None and not force_refresh:
            is_fresh = (
                existing.queue_version == current_qv
                and now_playing_id == lounge.current_track_id
                and existing.is_locked == bool(lounge.is_locked)
                and existing.status == lounge.status
            )
            if is_fresh:
                return existing, lounge

        # Fresh hydration from database
        # Ensure relationships are loaded if locked_lounge was provided without options
        if not hasattr(lounge, "queue_entries") or lounge.queue_entries is None:
            reload_stmt = (
                select(DJLounge)
                .where(DJLounge.id == lounge_id)
                .options(
                    selectinload(DJLounge.current_track),
                    selectinload(DJLounge.queue_entries).selectinload(QueueEntry.track),
                )
            )
            lounge = (await db.execute(reload_stmt)).scalar_one()

        # Build Now Playing
        now_playing: Optional[NowPlayingTrack] = None
        if lounge.current_track:
            t = lounge.current_track
            now_playing = NowPlayingTrack(
                track_id=t.id,
                title=t.title,
                artist=t.artist,
                duration_seconds=t.duration_seconds,
                audio_asset_url=t.audio_asset_url,
                bpm=t.bpm,
                camelot_key=t.camelot_key,
                energy=t.energy,
                valence=t.valence,
                started_at=lounge.created_at,
            )

        # Reconstruct immutable insertion sequence based on (created_at, id)
        # independently of mutable sparse positions (Audit Fix 2)
        raw_entries = list(lounge.queue_entries or [])
        chronological_entries = sorted(raw_entries, key=lambda q: (q.created_at, q.id))
        insertion_seq_map = {qe.id: idx + 1 for idx, qe in enumerate(chronological_entries)}

        # Build Next Up list ordered by current sparse position
        positional_entries = sorted(
            raw_entries,
            key=lambda q: (q.position, q.created_at, q.id),
        )

        next_up: List[QueueTrackEntry] = []
        for qe in positional_entries:
            if not qe.track:
                continue
            tr = qe.track
            next_up.append(
                QueueTrackEntry(
                    queue_entry_id=qe.id,
                    track_id=tr.id,
                    title=tr.title,
                    artist=tr.artist,
                    duration_seconds=tr.duration_seconds,
                    audio_asset_url=tr.audio_asset_url,
                    bpm=tr.bpm,
                    camelot_key=tr.camelot_key,
                    energy=tr.energy,
                    valence=tr.valence,
                    position=qe.position,
                    vote_count=qe.vote_count,
                    added_by_user_id=qe.added_by_user_id,
                    insertion_seq=insertion_seq_map[qe.id],
                    created_at=qe.created_at,
                )
            )

        state = RoomState(
            lounge_id=lounge.id,
            status=lounge.status,
            is_locked=bool(lounge.is_locked),
            queue_version=current_qv,
            now_playing=now_playing,
            next_up=next_up,
            soundclash_stage=SoundclashStageState(),
            version=current_qv,
            next_insertion_seq=len(raw_entries) + 1,
            updated_at=utc_now(),
            last_db_count=len(next_up),
        )
        self._rooms[lounge_id] = state
        return state, lounge

    # --------------------------------------------------------------------------
    # Public Queue State Mutations (with Row Locks & Snapshot Rollback)
    # --------------------------------------------------------------------------

    async def add_track(
        self,
        db: AsyncSession,
        lounge_id: str,
        track_id: str,
        user_id: str,
    ) -> RoomState:
        """Add a track to the Next Up queue with sparse position assignment (REQ-020, REQ-054)."""
        async with self.get_room_lock(lounge_id):
            # Acquire PostgreSQL row lock on DJLounge
            lounge_stmt = (
                select(DJLounge)
                .where(DJLounge.id == lounge_id)
                .with_for_update()
                .options(
                    selectinload(DJLounge.current_track),
                    selectinload(DJLounge.queue_entries).selectinload(QueueEntry.track),
                )
            )
            lounge = (await db.execute(lounge_stmt)).scalar_one_or_none()
            if lounge is None:
                raise AppException(
                    message=f"DJ Lounge '{lounge_id}' was not found.",
                    status_code=status.HTTP_404_NOT_FOUND,
                    code="LOUNGE_NOT_FOUND",
                )

            room, lounge = await self._get_or_hydrate_room_unlocked(
                db=db,
                lounge_id=lounge_id,
                locked_lounge=lounge,
            )

            if room.status == "ended":
                raise AppException(
                    message="Cannot add track to an ended DJ Lounge.",
                    status_code=status.HTTP_409_CONFLICT,
                    code="LOUNGE_ENDED",
                )

            # Audit Fix 4: Reject track additions to locked lounges
            if room.is_locked:
                raise AppException(
                    message="DJ Lounge is locked by Host. Queue modifications are restricted.",
                    status_code=status.HTTP_403_FORBIDDEN,
                    code="ROOM_LOCKED",
                )

            # Check duplicate in Now Playing or Next Up
            if room.now_playing and room.now_playing.track_id == track_id:
                raise AppException(
                    message=f"Track '{track_id}' is currently playing.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="DUPLICATE_TRACK",
                )

            for item in room.next_up:
                if item.track_id == track_id:
                    raise AppException(
                        message=f"Track '{track_id}' is already in the Next Up queue.",
                        status_code=status.HTTP_400_BAD_REQUEST,
                        code="DUPLICATE_TRACK",
                    )

            # Validate track exists in catalog
            track_stmt = select(Track).where(Track.id == track_id)
            track = (await db.execute(track_stmt)).scalar_one_or_none()
            if track is None:
                raise AppException(
                    message=f"Track '{track_id}' does not exist in catalog.",
                    status_code=status.HTTP_404_NOT_FOUND,
                    code="TRACK_NOT_FOUND",
                )

            # Snapshot for rollback
            snapshot = room.clone()
            prev_lounge_version = lounge.queue_version

            # Determine new sparse position
            if not room.next_up:
                new_position = DEFAULT_SPARSE_STEP
            else:
                last_pos = max(item.position for item in room.next_up)
                new_position = last_pos + DEFAULT_SPARSE_STEP

            entry_id = f"qe_{uuid.uuid4().hex[:10]}"
            now_dt = utc_now()
            seq = room.next_insertion_seq
            room.next_insertion_seq += 1

            new_entry = QueueTrackEntry(
                queue_entry_id=entry_id,
                track_id=track.id,
                title=track.title,
                artist=track.artist,
                duration_seconds=track.duration_seconds,
                audio_asset_url=track.audio_asset_url,
                bpm=track.bpm,
                camelot_key=track.camelot_key,
                energy=track.energy,
                valence=track.valence,
                position=new_position,
                vote_count=0,
                added_by_user_id=user_id,
                insertion_seq=seq,
                created_at=now_dt,
            )

            # Apply memory update and increment queue_version
            lounge.queue_version = prev_lounge_version + 1
            room.next_up.append(new_entry)
            room.queue_version = lounge.queue_version
            room.version = lounge.queue_version
            room.updated_at = now_dt
            room.last_db_count = len(room.next_up)

            # Persist to database
            try:
                db_entry = QueueEntry(
                    id=entry_id,
                    lounge_id=lounge_id,
                    track_id=track.id,
                    position=new_position,
                    vote_count=0,
                    added_by_user_id=user_id,
                    created_at=now_dt,
                )
                db.add(db_entry)

                # Record queue event
                db_event = QueueEvent(
                    id=f"qev_{uuid.uuid4().hex[:10]}",
                    lounge_id=lounge_id,
                    user_id=user_id,
                    event_type="vote",
                    payload={"action": "add", "trackId": track.id, "position": new_position},
                    ts=int(now_dt.timestamp() * 1000),
                    created_at=now_dt,
                )
                db.add(db_event)
                await db.commit()
            except Exception as exc:
                await db.rollback()
                self._rooms[lounge_id] = snapshot
                lounge.queue_version = prev_lounge_version
                logger.error("Failed to persist added track to queue", exc_info=exc)
                raise AppException(
                    message="Failed to persist queue update.",
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    code="PERSISTENCE_FAILED",
                ) from exc

            return room

    async def vote_track(
        self,
        db: AsyncSession,
        lounge_id: str,
        track_id: str,
        user_id: str,
    ) -> RoomState:
        """Upvote a track in Next Up and deterministically re-sort the queue (REQ-021, REQ-027)."""
        async with self.get_room_lock(lounge_id):
            # Acquire PostgreSQL row lock on DJLounge
            lounge_stmt = (
                select(DJLounge)
                .where(DJLounge.id == lounge_id)
                .with_for_update()
                .options(
                    selectinload(DJLounge.current_track),
                    selectinload(DJLounge.queue_entries).selectinload(QueueEntry.track),
                )
            )
            lounge = (await db.execute(lounge_stmt)).scalar_one_or_none()
            if lounge is None:
                raise AppException(
                    message=f"DJ Lounge '{lounge_id}' was not found.",
                    status_code=status.HTTP_404_NOT_FOUND,
                    code="LOUNGE_NOT_FOUND",
                )

            room, lounge = await self._get_or_hydrate_room_unlocked(
                db=db,
                lounge_id=lounge_id,
                locked_lounge=lounge,
            )

            if room.status == "ended":
                raise AppException(
                    message="Cannot vote in an ended DJ Lounge.",
                    status_code=status.HTTP_409_CONFLICT,
                    code="LOUNGE_ENDED",
                )

            # Locate target track
            old_idx = -1
            target_entry: Optional[QueueTrackEntry] = None
            for idx, entry in enumerate(room.next_up):
                if entry.track_id == track_id:
                    old_idx = idx
                    target_entry = entry
                    break

            if target_entry is None or old_idx == -1:
                raise AppException(
                    message=f"Track '{track_id}' is not in the Next Up queue.",
                    status_code=status.HTTP_404_NOT_FOUND,
                    code="TRACK_NOT_IN_QUEUE",
                )

            snapshot = room.clone()
            prev_lounge_version = lounge.queue_version

            # Track old state to detect changed entries for minimal DB updates (Audit Fix 6)
            old_state_map = {e.queue_entry_id: (e.vote_count, e.position) for e in room.next_up}

            target_entry.vote_count += 1

            # Deterministic re-sort by vote_count DESC, then immutable insertion_seq ASC
            new_sorted = self.sort_next_up_deterministically(room.next_up)
            new_idx = new_sorted.index(target_entry)

            if new_idx == old_idx:
                # Ordinary upvote where relative order does not change: position untouched
                room.next_up = new_sorted
            else:
                # Track moved in relative ordering: compute sparse position or re-space only if needed
                remaining = [e for e in new_sorted if e.queue_entry_id != target_entry.queue_entry_id]
                if not remaining:
                    target_entry.position = DEFAULT_SPARSE_STEP
                    room.next_up = [target_entry]
                elif new_idx == 0:
                    head_pos = remaining[0].position
                    if head_pos <= MIN_SPARSE_HEAD:
                        room.next_up = new_sorted
                        self.re_space_positions(room.next_up)
                    else:
                        target_entry.position = head_pos // 2
                        room.next_up = new_sorted
                elif new_idx == len(remaining):
                    last_pos = remaining[-1].position
                    target_entry.position = last_pos + DEFAULT_SPARSE_STEP
                    room.next_up = new_sorted
                else:
                    prev_pos = remaining[new_idx - 1].position
                    next_pos = remaining[new_idx].position
                    gap = next_pos - prev_pos
                    if gap <= 1:
                        room.next_up = new_sorted
                        self.re_space_positions(room.next_up)
                    else:
                        target_entry.position = prev_pos + (gap // 2)
                        room.next_up = new_sorted

            now_dt = utc_now()
            lounge.queue_version = prev_lounge_version + 1
            room.queue_version = lounge.queue_version
            room.version = lounge.queue_version
            room.updated_at = now_dt

            # Persist ONLY entries that actually changed (Audit Fix 6)
            try:
                for entry in room.next_up:
                    prev_v, prev_p = old_state_map[entry.queue_entry_id]
                    if entry.vote_count != prev_v or entry.position != prev_p:
                        await db.execute(
                            update(QueueEntry)
                            .where(QueueEntry.id == entry.queue_entry_id)
                            .values(vote_count=entry.vote_count, position=entry.position)
                        )

                db_event = QueueEvent(
                    id=f"qev_{uuid.uuid4().hex[:10]}",
                    lounge_id=lounge_id,
                    user_id=user_id,
                    event_type="vote",
                    payload={"action": "upvote", "trackId": track_id, "voteCount": target_entry.vote_count},
                    ts=int(now_dt.timestamp() * 1000),
                    created_at=now_dt,
                )
                db.add(db_event)
                await db.commit()
            except Exception as exc:
                await db.rollback()
                self._rooms[lounge_id] = snapshot
                lounge.queue_version = prev_lounge_version
                logger.error("Failed to persist vote to queue", exc_info=exc)
                raise AppException(
                    message="Failed to persist queue vote.",
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    code="PERSISTENCE_FAILED",
                ) from exc

            return room

    async def promote_track(
        self,
        db: AsyncSession,
        lounge_id: str,
        track_id: str,
        user_id: str,
        target_position: Optional[int] = None,
    ) -> RoomState:
        """Promote a track to head (or target slot) using sparse midpoint calculation (REQ-054)."""
        async with self.get_room_lock(lounge_id):
            # Acquire PostgreSQL row lock on DJLounge
            lounge_stmt = (
                select(DJLounge)
                .where(DJLounge.id == lounge_id)
                .with_for_update()
                .options(
                    selectinload(DJLounge.current_track),
                    selectinload(DJLounge.queue_entries).selectinload(QueueEntry.track),
                )
            )
            lounge = (await db.execute(lounge_stmt)).scalar_one_or_none()
            if lounge is None:
                raise AppException(
                    message=f"DJ Lounge '{lounge_id}' was not found.",
                    status_code=status.HTTP_404_NOT_FOUND,
                    code="LOUNGE_NOT_FOUND",
                )

            room, lounge = await self._get_or_hydrate_room_unlocked(
                db=db,
                lounge_id=lounge_id,
                locked_lounge=lounge,
            )

            if room.status == "ended":
                raise AppException(
                    message="Cannot promote track in an ended DJ Lounge.",
                    status_code=status.HTTP_409_CONFLICT,
                    code="LOUNGE_ENDED",
                )

            if target_position is not None and target_position <= 0:
                raise AppException(
                    message="Target position must be a positive integer.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="INVALID_TARGET_POSITION",
                )

            # Find track in queue
            idx = -1
            for i, entry in enumerate(room.next_up):
                if entry.track_id == track_id:
                    idx = i
                    break

            if idx == -1:
                raise AppException(
                    message=f"Track '{track_id}' is not in the Next Up queue.",
                    status_code=status.HTTP_404_NOT_FOUND,
                    code="TRACK_NOT_IN_QUEUE",
                )

            snapshot = room.clone()
            prev_lounge_version = lounge.queue_version
            old_positions = {e.queue_entry_id: e.position for e in room.next_up}

            target_entry = room.next_up.pop(idx)

            # Calculate sparse position (Audit Fix 5)
            if not room.next_up:
                target_entry.position = DEFAULT_SPARSE_STEP
                room.next_up.append(target_entry)
            elif target_position is None or target_position <= room.next_up[0].position:
                # Promote to head
                head_pos = room.next_up[0].position
                if head_pos <= MIN_SPARSE_HEAD:
                    # Integer capacity exhausted at head: insert and re-space
                    room.next_up.insert(0, target_entry)
                    self.re_space_positions(room.next_up)
                else:
                    target_entry.position = head_pos // 2
                    room.next_up.insert(0, target_entry)
            else:
                # Find slot for target_position
                insert_idx = len(room.next_up)
                for i, existing in enumerate(room.next_up):
                    if existing.position >= target_position:
                        insert_idx = i
                        break

                if insert_idx == len(room.next_up):
                    last_pos = room.next_up[-1].position
                    target_entry.position = last_pos + DEFAULT_SPARSE_STEP
                    room.next_up.append(target_entry)
                elif insert_idx == 0:
                    head_pos = room.next_up[0].position
                    if head_pos <= MIN_SPARSE_HEAD:
                        room.next_up.insert(0, target_entry)
                        self.re_space_positions(room.next_up)
                    else:
                        target_entry.position = head_pos // 2
                        room.next_up.insert(0, target_entry)
                else:
                    prev_pos = room.next_up[insert_idx - 1].position
                    next_pos = room.next_up[insert_idx].position
                    gap = next_pos - prev_pos
                    if gap <= 1:
                        # Collision / midpoint capacity exhausted: re-space
                        room.next_up.insert(insert_idx, target_entry)
                        self.re_space_positions(room.next_up)
                    else:
                        target_entry.position = prev_pos + (gap // 2)
                        room.next_up.insert(insert_idx, target_entry)

            now_dt = utc_now()
            lounge.queue_version = prev_lounge_version + 1
            room.queue_version = lounge.queue_version
            room.version = lounge.queue_version
            room.updated_at = now_dt

            # Persist positions only for changed entries
            try:
                for entry in room.next_up:
                    prev_p = old_positions.get(entry.queue_entry_id)
                    if prev_p != entry.position:
                        await db.execute(
                            update(QueueEntry)
                            .where(QueueEntry.id == entry.queue_entry_id)
                            .values(position=entry.position)
                        )

                db_event = QueueEvent(
                    id=f"qev_{uuid.uuid4().hex[:10]}",
                    lounge_id=lounge_id,
                    user_id=user_id,
                    event_type="vote",
                    payload={"action": "promote", "trackId": track_id, "position": target_entry.position},
                    ts=int(now_dt.timestamp() * 1000),
                    created_at=now_dt,
                )
                db.add(db_event)
                await db.commit()
            except Exception as exc:
                await db.rollback()
                self._rooms[lounge_id] = snapshot
                lounge.queue_version = prev_lounge_version
                logger.error("Failed to persist promoted track to queue", exc_info=exc)
                raise AppException(
                    message="Failed to persist track promotion.",
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    code="PERSISTENCE_FAILED",
                ) from exc

            return room

    async def remove_track(
        self,
        db: AsyncSession,
        lounge_id: str,
        track_id: str,
        user_id: str,
    ) -> RoomState:
        """Remove a track from Next Up without renumbering remaining tracks (REQ-054)."""
        async with self.get_room_lock(lounge_id):
            # Acquire PostgreSQL row lock on DJLounge
            lounge_stmt = (
                select(DJLounge)
                .where(DJLounge.id == lounge_id)
                .with_for_update()
                .options(
                    selectinload(DJLounge.current_track),
                    selectinload(DJLounge.queue_entries).selectinload(QueueEntry.track),
                )
            )
            lounge = (await db.execute(lounge_stmt)).scalar_one_or_none()
            if lounge is None:
                raise AppException(
                    message=f"DJ Lounge '{lounge_id}' was not found.",
                    status_code=status.HTTP_404_NOT_FOUND,
                    code="LOUNGE_NOT_FOUND",
                )

            room, lounge = await self._get_or_hydrate_room_unlocked(
                db=db,
                lounge_id=lounge_id,
                locked_lounge=lounge,
            )

            if room.status == "ended":
                raise AppException(
                    message="Cannot remove track from an ended DJ Lounge.",
                    status_code=status.HTTP_409_CONFLICT,
                    code="LOUNGE_ENDED",
                )

            idx = -1
            target_entry: Optional[QueueTrackEntry] = None
            for i, entry in enumerate(room.next_up):
                if entry.track_id == track_id:
                    idx = i
                    target_entry = entry
                    break

            if idx == -1 or target_entry is None:
                raise AppException(
                    message=f"Track '{track_id}' is not in the Next Up queue.",
                    status_code=status.HTTP_404_NOT_FOUND,
                    code="TRACK_NOT_IN_QUEUE",
                )

            snapshot = room.clone()
            prev_lounge_version = lounge.queue_version

            room.next_up.pop(idx)
            now_dt = utc_now()
            lounge.queue_version = prev_lounge_version + 1
            room.queue_version = lounge.queue_version
            room.version = lounge.queue_version
            room.updated_at = now_dt
            room.last_db_count = len(room.next_up)

            try:
                await db.execute(
                    delete(QueueEntry).where(QueueEntry.id == target_entry.queue_entry_id)
                )

                db_event = QueueEvent(
                    id=f"qev_{uuid.uuid4().hex[:10]}",
                    lounge_id=lounge_id,
                    user_id=user_id,
                    event_type="vote",
                    payload={"action": "remove", "trackId": track_id},
                    ts=int(now_dt.timestamp() * 1000),
                    created_at=now_dt,
                )
                db.add(db_event)
                await db.commit()
            except Exception as exc:
                await db.rollback()
                self._rooms[lounge_id] = snapshot
                lounge.queue_version = prev_lounge_version
                logger.error("Failed to persist track removal", exc_info=exc)
                raise AppException(
                    message="Failed to persist track removal.",
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    code="PERSISTENCE_FAILED",
                ) from exc

            return room

    async def force_segue(
        self,
        db: AsyncSession,
        lounge_id: str,
        user_id: str,
        target_track_id: Optional[str] = None,
    ) -> RoomState:
        """Advance queue: pop head of Next Up (or explicit target track) into Now Playing (REQ-015, REQ-020)."""
        async with self.get_room_lock(lounge_id):
            # Acquire PostgreSQL row lock on DJLounge
            lounge_stmt = (
                select(DJLounge)
                .where(DJLounge.id == lounge_id)
                .with_for_update()
                .options(
                    selectinload(DJLounge.current_track),
                    selectinload(DJLounge.queue_entries).selectinload(QueueEntry.track),
                )
            )
            lounge = (await db.execute(lounge_stmt)).scalar_one_or_none()
            if lounge is None:
                raise AppException(
                    message=f"DJ Lounge '{lounge_id}' was not found.",
                    status_code=status.HTTP_404_NOT_FOUND,
                    code="LOUNGE_NOT_FOUND",
                )

            room, lounge = await self._get_or_hydrate_room_unlocked(
                db=db,
                lounge_id=lounge_id,
                locked_lounge=lounge,
            )

            if room.status == "ended":
                raise AppException(
                    message="Cannot force segue in an ended DJ Lounge.",
                    status_code=status.HTTP_409_CONFLICT,
                    code="LOUNGE_ENDED",
                )

            if not room.next_up:
                raise AppException(
                    message="Cannot force segue: Next Up queue is empty.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="CANNOT_FORCE_SEGUE_EMPTY_QUEUE",
                )

            # Locate selected track: head of queue or explicit target
            target_idx = 0
            if target_track_id is not None:
                found = False
                for i, entry in enumerate(room.next_up):
                    if entry.track_id == target_track_id:
                        target_idx = i
                        found = True
                        break
                if not found:
                    raise AppException(
                        message=f"Explicit target track '{target_track_id}' is not in the Next Up queue.",
                        status_code=status.HTTP_404_NOT_FOUND,
                        code="TRACK_NOT_IN_QUEUE",
                    )

            snapshot = room.clone()
            prev_lounge_version = lounge.queue_version

            next_track = room.next_up.pop(target_idx)
            now_dt = utc_now()

            # Assign to Now Playing zone
            room.now_playing = NowPlayingTrack(
                track_id=next_track.track_id,
                title=next_track.title,
                artist=next_track.artist,
                duration_seconds=next_track.duration_seconds,
                audio_asset_url=next_track.audio_asset_url,
                bpm=next_track.bpm,
                camelot_key=next_track.camelot_key,
                energy=next_track.energy,
                valence=next_track.valence,
                started_at=now_dt,
            )
            lounge.queue_version = prev_lounge_version + 1
            lounge.current_track_id = next_track.track_id
            room.queue_version = lounge.queue_version
            room.version = lounge.queue_version
            room.updated_at = now_dt
            room.last_db_count = len(room.next_up)

            try:
                # Update lounge's current track and queue version
                await db.execute(
                    update(DJLounge)
                    .where(DJLounge.id == lounge_id)
                    .values(
                        current_track_id=next_track.track_id,
                        queue_version=lounge.queue_version,
                    )
                )

                # Delete popped entry from queue_entries
                await db.execute(
                    delete(QueueEntry).where(QueueEntry.id == next_track.queue_entry_id)
                )

                # Record segue event
                db_event = QueueEvent(
                    id=f"qev_{uuid.uuid4().hex[:10]}",
                    lounge_id=lounge_id,
                    user_id=user_id,
                    event_type="segue",
                    payload={
                        "action": "force_segue",
                        "selectedTrackId": next_track.track_id,
                        "title": next_track.title,
                    },
                    ts=int(now_dt.timestamp() * 1000),
                    created_at=now_dt,
                )
                db.add(db_event)
                await db.commit()
            except Exception as exc:
                await db.rollback()
                self._rooms[lounge_id] = snapshot
                lounge.queue_version = prev_lounge_version
                logger.error("Failed to persist force segue", exc_info=exc)
                raise AppException(
                    message="Failed to persist force segue.",
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    code="PERSISTENCE_FAILED",
                ) from exc

            return room


# Singleton instance
room_state_manager = RoomStateManager()
