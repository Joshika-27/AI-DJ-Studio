"""Track catalog, mandatory acoustic profile (PRD Section 7.3.2), and Admin upload API endpoints (Module 4 — Remediated M4-02, M4-04)."""

import uuid

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, require_admin
from app.core.exceptions import AppException
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.track import Track, TrackTimelineEvent, VALID_CAMELOT_KEYS
from app.models.user import User
from app.schemas.track import (
    AdminTrackUploadRequest,
    TrackAcousticProfileResponse,
    TrackCatalogItemResponse,
    TrackCatalogListResponse,
)

logger = get_logger("api.tracks")

router = APIRouter(tags=["Tracks & Acoustic Profiles"])


@router.get(
    "/tracks",
    response_model=TrackCatalogListResponse,
    status_code=status.HTTP_200_OK,
    summary="List catalog tracks with acoustic feature vectors and RAG timelines",
)
async def list_tracks(
    camelotKey: str | None = Query(
        default=None,
        description="Optional filter by 24-key Camelot wheel code (1A-12A, 1B-12B)",
    ),
    minBpm: float | None = Query(
        default=None, gt=0, description="Optional minimum BPM filter"
    ),
    maxBpm: float | None = Query(
        default=None, gt=0, description="Optional maximum BPM filter"
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TrackCatalogListResponse:
    """Return catalog tracks ordered by ID, with optional Camelot key and BPM range filtering (M4-04)."""
    if minBpm is not None and maxBpm is not None and float(minBpm) > float(maxBpm):
        raise AppException(
            message=f"minBpm ({minBpm}) cannot be greater than maxBpm ({maxBpm}).",
            status_code=status.HTTP_400_BAD_REQUEST,
            code="INVALID_BPM_RANGE",
        )

    stmt = (
        select(Track)
        .options(selectinload(Track.timeline_events))
        .order_by(Track.id.asc())
    )

    if camelotKey is not None:
        normalized_key = camelotKey.strip().upper()
        if normalized_key not in VALID_CAMELOT_KEYS:
            raise AppException(
                message=f"Invalid camelotKey filter '{camelotKey}'.",
                status_code=status.HTTP_400_BAD_REQUEST,
                code="INVALID_CAMELOT_KEY",
            )
        stmt = stmt.where(Track.camelot_key == normalized_key)

    if minBpm is not None:
        stmt = stmt.where(Track.bpm >= float(minBpm))

    if maxBpm is not None:
        stmt = stmt.where(Track.bpm <= float(maxBpm))

    tracks = (await db.execute(stmt)).scalars().all()
    serialized = [TrackCatalogItemResponse.from_orm_track(t) for t in tracks]
    return TrackCatalogListResponse(tracks=serialized, total=len(serialized))


@router.get(
    "/tracks/{trackId}/acoustic-profile",
    response_model=TrackAcousticProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a track's full acoustic feature vector and RAG metadata timeline (PRD Section 7.3.2)",
)
async def get_track_acoustic_profile(
    trackId: str = Path(..., min_length=1, max_length=64),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TrackAcousticProfileResponse:
    """Mandatory PRD Section 7.3.2 endpoint returning acoustic vector and RAG metadata timeline (REQ-058)."""
    stmt = (
        select(Track)
        .where(Track.id == trackId)
        .options(selectinload(Track.timeline_events))
    )
    track = (await db.execute(stmt)).scalar_one_or_none()
    if track is None:
        raise AppException(
            message=f"Track '{trackId}' was not found.",
            status_code=status.HTTP_404_NOT_FOUND,
            code="TRACK_NOT_FOUND",
        )

    return TrackAcousticProfileResponse.from_orm_track(track)


@router.post(
    "/admin/tracks",
    response_model=TrackCatalogItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and validate a track's acoustic feature profile and RAG metadata timeline (Admin only)",
)
async def admin_upload_track(
    payload: AdminTrackUploadRequest,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> TrackCatalogItemResponse:
    """Create a new track with validated acoustic features and RAG timeline atomically (REQ-046, REQ-049, M4-02)."""
    track_id = payload.trackId or f"t_{uuid.uuid4().hex[:8]}"

    existing_track = (
        await db.execute(select(Track).where(Track.id == track_id))
    ).scalar_one_or_none()
    if existing_track is not None:
        raise AppException(
            message=f"A track with trackId '{track_id}' already exists.",
            status_code=status.HTTP_409_CONFLICT,
            code="TRACK_ALREADY_EXISTS",
        )

    # M4-02: Pre-check any explicitly supplied timeline event IDs against the database before writing
    explicit_event_ids = [evt.id for evt in payload.timeline if evt.id is not None]
    if explicit_event_ids:
        existing_evt_ids = (
            (
                await db.execute(
                    select(TrackTimelineEvent.id).where(
                        TrackTimelineEvent.id.in_(explicit_event_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        if existing_evt_ids:
            raise AppException(
                message="One or more timeline event IDs already exist.",
                status_code=status.HTTP_409_CONFLICT,
                code="TIMELINE_EVENT_ALREADY_EXISTS",
            )

    try:
        new_track = Track(
            id=track_id,
            title=payload.title,
            artist=payload.artist,
            duration_seconds=payload.durationSeconds,
            audio_asset_url=payload.audioAssetUrl,
            bpm=float(payload.acoustic.bpm),
            camelot_key=payload.acoustic.camelotKey,
            energy=float(payload.acoustic.energy),
            valence=float(payload.acoustic.valence),
        )
        db.add(new_track)

        for idx, evt in enumerate(payload.timeline):
            evt_id = evt.id or f"evt_{track_id}_{idx + 1}_{uuid.uuid4().hex[:6]}"
            timeline_row = TrackTimelineEvent(
                id=evt_id,
                track_id=new_track.id,
                ts_seconds=evt.ts,
                event_type=evt.type,
                text_body=evt.text,
                payload={"text": evt.text},
            )
            db.add(timeline_row)

        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning(
            "Database integrity conflict during admin track upload",
            extra={
                "event": "admin.track.upload_conflict",
                "payload": {"adminUserId": admin_user.id, "trackId": track_id},
            },
        )
        raise AppException(
            message="Track or timeline event identifier conflict.",
            status_code=status.HTTP_409_CONFLICT,
            code="RESOURCE_CONFLICT",
        ) from exc

    reloaded_stmt = (
        select(Track)
        .where(Track.id == track_id)
        .options(selectinload(Track.timeline_events))
    )
    persisted_track = (await db.execute(reloaded_stmt)).scalar_one()

    logger.info(
        "Admin uploaded new track acoustic profile",
        extra={
            "event": "admin.track.uploaded",
            "payload": {
                "adminUserId": admin_user.id,
                "trackId": persisted_track.id,
                "bpm": persisted_track.bpm,
                "camelotKey": persisted_track.camelot_key,
                "timelineCount": len(persisted_track.timeline_events),
            },
        },
    )

    return TrackCatalogItemResponse.from_orm_track(persisted_track)
