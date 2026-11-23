"""DJ Lounge lifecycle, invite-code joining, capacity enforcement, and Host moderation service (Module 5/6 — PRD Section 7.3.1, REQ-007, REQ-015, REQ-016, REQ-019, REQ-025, REQ-057, REQ-076)."""

import asyncio
from datetime import datetime, timezone
import re
import secrets
import time
from typing import Any, Dict, List
import uuid

from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AppException
from app.core.logging import get_logger
from app.db.base import utc_now
from app.models.lounge import DJLounge
from app.models.queue import QueueEntry, QueueEvent
from app.models.track import Track
from app.models.user import User
from app.schemas.lounge import (
    LoungeCreateRequest,
    LoungeCreateResponse,
    LoungeDetailResponse,
    LoungeEndResponse,
    LoungeEndSummarySchema,
    LoungeJoinResponse,
    LoungeListResponse,
    LoungeModerationRequest,
    LoungeModerationResponse,
    LoungeModerationStateSchema,
    LoungeNowPlayingSchema,
    LoungeParticipantSchema,
    LoungeQueueItemSchema,
    LoungeSettingsSchema,
    LoungeSnapshotSchema,
    LoungeStartResponse,
    LoungeSummaryResponse,
    format_iso_utc,
)
from app.schemas.track import _format_bpm_value

logger = get_logger("services.lounge")

_INVITE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_INVITE_CODE_REGEX = re.compile(r"^SP-[A-Z0-9]{4,10}$")
VALID_LOUNGE_STATUSES = frozenset({"scheduled", "live", "ended"})

# Per-lounge asyncio locks keyed by lounge_id (REQ-076 multi-room concurrency)
_LOUNGE_LOCKS: Dict[str, asyncio.Lock] = {}
_LAST_MONOTONIC_TS_MS: int = 0


def _get_lounge_lock(lounge_id: str) -> asyncio.Lock:
    lock = _LOUNGE_LOCKS.get(lounge_id)
    if lock is None:
        lock = asyncio.Lock()
        _LOUNGE_LOCKS[lounge_id] = lock
    return lock


def _next_event_ts_ms() -> int:
    """Return a strictly increasing epoch-millisecond timestamp for deterministic event ordering."""
    global _LAST_MONOTONIC_TS_MS
    now_ms = int(time.time() * 1000)
    if now_ms <= _LAST_MONOTONIC_TS_MS:
        now_ms = _LAST_MONOTONIC_TS_MS + 1
    _LAST_MONOTONIC_TS_MS = now_ms
    return now_ms


def normalize_and_validate_invite_code(invite_code: str) -> str:
    """Normalize and validate an invite code format (e.g. 'SP-9X2L')."""
    if not isinstance(invite_code, str):
        raise AppException(
            message="Invite code must be a valid string.",
            status_code=status.HTTP_400_BAD_REQUEST,
            code="INVALID_INVITE_CODE",
        )
    normalized = invite_code.strip().upper()
    if not _INVITE_CODE_REGEX.match(normalized):
        raise AppException(
            message="Invalid invite code format. Expected format like 'SP-9X2L'.",
            status_code=status.HTTP_400_BAD_REQUEST,
            code="INVALID_INVITE_CODE",
        )
    return normalized


async def _generate_unique_invite_code(db: AsyncSession) -> str:
    """Generate a collision-free 'SP-XXXX' invite code against dj_lounges."""
    for attempt in range(12):
        code_len = 4 if attempt < 8 else 6
        suffix = "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(code_len))
        candidate = f"SP-{suffix}"
        existing = (
            await db.execute(
                select(DJLounge.id).where(DJLounge.invite_code == candidate)
            )
        ).scalar_one_or_none()
        if existing is None:
            return candidate
    return f"SP-{uuid.uuid4().hex[:6].upper()}"


async def _reconstruct_lounge_membership(
    db: AsyncSession,
    lounge: DJLounge,
) -> Dict[str, Any]:
    """Reconstruct authoritative participant membership and moderation state from presence events in queue_events."""
    events_stmt = (
        select(QueueEvent)
        .where(
            QueueEvent.lounge_id == lounge.id,
            QueueEvent.event_type == "presence",
        )
        .order_by(QueueEvent.ts.asc(), QueueEvent.created_at.asc())
    )
    events = (await db.execute(events_stmt)).scalars().all()

    default_joined_iso = format_iso_utc(lounge.created_at) or format_iso_utc(utc_now())
    active_joined_at: Dict[str, str] = {
        lounge.host_user_id: str(default_joined_iso),
    }
    muted_user_ids: set[str] = set()
    removed_user_ids: set[str] = set()
    participant_roles: Dict[str, str] = {
        lounge.host_user_id: "host",
    }

    for evt in events:
        payload = evt.payload if isinstance(evt.payload, dict) else {}
        action = str(payload.get("action", "")).lower()
        mod_action = str(payload.get("moderationAction", "")).lower()
        pid = str(
            payload.get("participantId")
            or payload.get("targetUserId")
            or evt.user_id
            or ""
        )
        evt_iso = format_iso_utc(evt.created_at) or str(default_joined_iso)

        if action == "joined" and pid:
            active_joined_at.setdefault(pid, evt_iso)
            removed_user_ids.discard(pid)
            role_in_evt = payload.get("role")
            if isinstance(role_in_evt, str) and role_in_evt:
                participant_roles.setdefault(pid, role_in_evt)
        elif action == "left" and pid:
            active_joined_at.pop(pid, None)
            if mod_action == "remove":
                removed_user_ids.add(pid)

        if mod_action == "mute" and pid:
            muted_user_ids.add(pid)
        elif mod_action == "unmute" and pid:
            muted_user_ids.discard(pid)
        elif mod_action == "remove" and pid:
            active_joined_at.pop(pid, None)
            removed_user_ids.add(pid)
        elif mod_action == "set_role" and pid:
            new_role = payload.get("newRole")
            if isinstance(new_role, str) and new_role:
                participant_roles[pid] = new_role

    # Ensure current owning Host is always marked as host and active unless room has no host
    participant_roles[lounge.host_user_id] = "host"
    active_joined_at.setdefault(lounge.host_user_id, str(default_joined_iso))
    removed_user_ids.discard(lounge.host_user_id)
    muted_user_ids.discard(lounge.host_user_id)

    all_user_ids = set(active_joined_at.keys()) | {lounge.host_user_id}
    users_stmt = select(User).where(User.id.in_(list(all_user_ids)))
    users_by_id: Dict[str, User] = {
        u.id: u for u in (await db.execute(users_stmt)).scalars().all()
    }

    participants_list: List[LoungeParticipantSchema] = []
    for uid, joined_iso in active_joined_at.items():
        user_obj = users_by_id.get(uid)
        display_name = user_obj.display_name if user_obj else f"User {uid}"
        default_role = user_obj.role if user_obj else "listener"
        eff_role = participant_roles.get(uid, default_role)
        if uid == lounge.host_user_id:
            eff_role = "host"
        elif eff_role == "host":
            eff_role = "co_dj"
        participant_roles[uid] = eff_role
        participants_list.append(
            LoungeParticipantSchema(
                userId=uid,
                displayName=display_name,
                role=eff_role,
                isMuted=(uid in muted_user_ids),
                joinedAt=joined_iso,
            )
        )

    host_user = users_by_id.get(lounge.host_user_id)
    host_display_name = (
        host_user.display_name if host_user else f"Host {lounge.host_user_id}"
    )

    moderation_state = LoungeModerationStateSchema(
        isLocked=bool(lounge.is_locked),
        hostUserId=lounge.host_user_id,
        mutedUserIds=sorted(muted_user_ids),
        removedUserIds=sorted(removed_user_ids),
        participantRoles=dict(participant_roles),
    )

    return {
        "activeJoinedAt": active_joined_at,
        "participants": participants_list,
        "mutedUserIds": muted_user_ids,
        "removedUserIds": removed_user_ids,
        "participantRoles": participant_roles,
        "moderationState": moderation_state,
        "hostDisplayName": host_display_name,
    }


def _build_settings_schema(lounge: DJLounge) -> LoungeSettingsSchema:
    return LoungeSettingsSchema(
        maxParticipants=lounge.max_participants,
        bpmTolerancePct=_format_bpm_value(lounge.bpm_tolerance_pct),
        soundclashEnabled=bool(lounge.soundclash_enabled),
        aiVerbosity=lounge.ai_verbosity,
    )


def _build_now_playing_schema(track: Track | None) -> LoungeNowPlayingSchema | None:
    if track is None:
        return None
    return LoungeNowPlayingSchema(
        trackId=track.id,
        title=track.title,
        artist=track.artist,
        durationSeconds=track.duration_seconds,
        audioAssetUrl=track.audio_asset_url,
        bpm=_format_bpm_value(track.bpm),
        camelotKey=track.camelot_key,
        energy=round(float(track.energy), 4),
        valence=round(float(track.valence), 4),
    )


async def _load_lounge_with_relations(
    db: AsyncSession,
    lounge_id: str,
    for_update: bool = False,
) -> DJLounge:
    stmt = (
        select(DJLounge)
        .where(DJLounge.id == lounge_id)
        .options(
            selectinload(DJLounge.current_track),
            selectinload(DJLounge.queue_entries).selectinload(QueueEntry.track),
        )
    )
    if for_update:
        stmt = stmt.with_for_update()
    lounge = (await db.execute(stmt)).scalar_one_or_none()
    if lounge is None:
        raise AppException(
            message=f"DJ Lounge '{lounge_id}' was not found.",
            status_code=status.HTTP_404_NOT_FOUND,
            code="LOUNGE_NOT_FOUND",
        )
    return lounge


def _build_queue_items(lounge: DJLounge) -> List[LoungeQueueItemSchema]:
    ordered = sorted(
        lounge.queue_entries or [],
        key=lambda q: (q.position, q.created_at, q.id),
    )
    items: List[LoungeQueueItemSchema] = []
    for entry in ordered:
        track = entry.track
        if track is None:
            continue
        items.append(
            LoungeQueueItemSchema(
                queueEntryId=entry.id,
                trackId=track.id,
                title=track.title,
                artist=track.artist,
                bpm=_format_bpm_value(track.bpm),
                camelotKey=track.camelot_key,
                energy=round(float(track.energy), 4),
                valence=round(float(track.valence), 4),
                position=entry.position,
                voteCount=entry.vote_count,
                addedByUserId=entry.added_by_user_id,
            )
        )
    return items


def _assert_host_of_lounge_or_admin(
    lounge: DJLounge,
    current_user: User,
    participant_roles: Dict[str, str],
) -> None:
    """Enforce Host-only (or Admin) permission for lifecycle and moderation operations (REQ-015, REQ-025, REQ-070).

    Enforcement is strictly lounge-scoped:
    - Platform admins (current_user.role == 'admin') may manage/moderate any lounge.
    - The actual host of this lounge (lounge.host_user_id == current_user.id) may manage/moderate it.
    - Global Hosts who are not the host of this specific lounge receive HTTP 403 Forbidden.
    """
    is_admin = current_user.role == "admin"
    is_lounge_host = lounge.host_user_id == current_user.id
    if not (is_admin or is_lounge_host):
        logger.warning(
            "Unauthorized lounge host/moderation action rejected",
            extra={
                "event": "lounge.rbac.denied",
                "payload": {
                    "loungeId": lounge.id,
                    "userId": current_user.id,
                    "userRole": current_user.role,
                    "hostUserId": lounge.host_user_id,
                },
            },
        )
        raise AppException(
            message="Insufficient permissions to access this resource.",
            status_code=status.HTTP_403_FORBIDDEN,
            code="FORBIDDEN",
        )


async def create_lounge(
    db: AsyncSession,
    host_user: User,
    payload: LoungeCreateRequest,
) -> LoungeCreateResponse:
    """Create a new DJ Lounge and return the exact PRD Section 7.3.1 response contract (REQ-019, REQ-057)."""
    seed_track = (
        await db.execute(select(Track).where(Track.id == payload.seedTrackId))
    ).scalar_one_or_none()
    if seed_track is None:
        raise AppException(
            message=f"Seed track '{payload.seedTrackId}' was not found in the catalog.",
            status_code=status.HTTP_404_NOT_FOUND,
            code="TRACK_NOT_FOUND",
        )

    for _ in range(5):
        invite_code = await _generate_unique_invite_code(db)
        lounge_id = f"lg_{uuid.uuid4().hex[:8]}"
        created_at = utc_now()

        new_lounge = DJLounge(
            id=lounge_id,
            invite_code=invite_code,
            host_user_id=host_user.id,
            vibe_profile=payload.vibeProfile,
            current_track_id=seed_track.id,
            status="scheduled",
            max_participants=int(payload.maxParticipants),
            bpm_tolerance_pct=float(payload.bpmTolerancePct),
            soundclash_enabled=bool(payload.soundclashEnabled),
            ai_verbosity=payload.aiVerbosity,
            peak_participants=1,
            is_locked=False,
            created_at=created_at,
        )
        db.add(new_lounge)

        host_join_event = QueueEvent(
            id=f"qev_{uuid.uuid4().hex[:10]}",
            lounge_id=lounge_id,
            user_id=host_user.id,
            event_type="presence",
            payload={
                "participantId": host_user.id,
                "action": "joined",
                "role": "host",
                "participantCount": 1,
            },
            ts=_next_event_ts_ms(),
            created_at=created_at,
        )
        db.add(host_join_event)

        try:
            await db.commit()
            await db.refresh(new_lounge)
            logger.info(
                "Created new DJ Lounge",
                extra={
                    "event": "lounge.created",
                    "payload": {
                        "loungeId": new_lounge.id,
                        "inviteCode": new_lounge.invite_code,
                        "hostUserId": host_user.id,
                        "seedTrackId": seed_track.id,
                        "maxParticipants": new_lounge.max_participants,
                    },
                },
            )
            return LoungeCreateResponse(
                loungeId=new_lounge.id,
                inviteCode=new_lounge.invite_code,
                status=new_lounge.status,
                hostUserId=new_lounge.host_user_id,
                createdAt=format_iso_utc(new_lounge.created_at) or "",
            )
        except IntegrityError:
            await db.rollback()
            continue

    raise AppException(
        message="Unable to allocate a unique lounge invite code. Please retry.",
        status_code=status.HTTP_409_CONFLICT,
        code="INVITE_CODE_COLLISION",
    )


async def list_lounges(
    db: AsyncSession,
    status_filter: str | None = None,
) -> LoungeListResponse:
    """List DJ Lounges ordered by creation time descending with batch-fetched presence and user metadata (REQ-019, M5-04)."""
    stmt = (
        select(DJLounge)
        .options(selectinload(DJLounge.current_track))
        .order_by(DJLounge.created_at.desc(), DJLounge.id.asc())
    )
    if status_filter is not None:
        normalized_status = status_filter.strip().lower()
        if normalized_status not in VALID_LOUNGE_STATUSES:
            raise AppException(
                message=f"Invalid status filter '{status_filter}'. Allowed statuses: {sorted(VALID_LOUNGE_STATUSES)}.",
                status_code=status.HTTP_400_BAD_REQUEST,
                code="INVALID_LOUNGE_STATUS",
            )
        stmt = stmt.where(DJLounge.status == normalized_status)

    lounges = (await db.execute(stmt)).scalars().all()
    if not lounges:
        return LoungeListResponse(lounges=[], total=0)

    lounge_ids = [l.id for l in lounges]
    host_user_ids = {l.host_user_id for l in lounges}

    # Batch Query 1: Fetch presence events across all listed lounges in a single query
    events_stmt = (
        select(QueueEvent)
        .where(
            QueueEvent.lounge_id.in_(lounge_ids),
            QueueEvent.event_type == "presence",
        )
        .order_by(QueueEvent.ts.asc(), QueueEvent.created_at.asc())
    )
    all_events = (await db.execute(events_stmt)).scalars().all()

    events_by_lounge: Dict[str, List[QueueEvent]] = {lid: [] for lid in lounge_ids}
    all_user_ids = set(host_user_ids)
    for evt in all_events:
        events_by_lounge.setdefault(evt.lounge_id, []).append(evt)
        payload = evt.payload if isinstance(evt.payload, dict) else {}
        pid = str(
            payload.get("participantId")
            or payload.get("targetUserId")
            or evt.user_id
            or ""
        )
        if pid:
            all_user_ids.add(pid)

    # Batch Query 2: Fetch all hosts and participants in a single query
    users_stmt = select(User).where(User.id.in_(list(all_user_ids)))
    users_by_id: Dict[str, User] = {
        u.id: u for u in (await db.execute(users_stmt)).scalars().all()
    }

    summaries: List[LoungeSummaryResponse] = []
    for lounge in lounges:
        lounge_events = events_by_lounge.get(lounge.id, [])
        default_joined_iso = format_iso_utc(lounge.created_at) or format_iso_utc(utc_now())
        active_joined_at: Dict[str, str] = {
            lounge.host_user_id: str(default_joined_iso),
        }
        for evt in lounge_events:
            payload = evt.payload if isinstance(evt.payload, dict) else {}
            action = str(payload.get("action", "")).lower()
            mod_action = str(payload.get("moderationAction", "")).lower()
            pid = str(
                payload.get("participantId")
                or payload.get("targetUserId")
                or evt.user_id
                or ""
            )
            evt_iso = format_iso_utc(evt.created_at) or str(default_joined_iso)

            if action == "joined" and pid:
                active_joined_at.setdefault(pid, evt_iso)
            elif action == "left" and pid:
                active_joined_at.pop(pid, None)
            if mod_action == "remove" and pid:
                active_joined_at.pop(pid, None)

        active_joined_at.setdefault(lounge.host_user_id, str(default_joined_iso))

        host_user = users_by_id.get(lounge.host_user_id)
        host_display_name = (
            host_user.display_name if host_user else f"Host {lounge.host_user_id}"
        )
        curr_track = lounge.current_track

        summaries.append(
            LoungeSummaryResponse(
                loungeId=lounge.id,
                inviteCode=lounge.invite_code,
                status=lounge.status,
                hostUserId=lounge.host_user_id,
                hostDisplayName=host_display_name,
                vibeProfile=lounge.vibe_profile,
                currentTrackId=lounge.current_track_id,
                nowPlayingTitle=curr_track.title if curr_track else None,
                nowPlayingArtist=curr_track.artist if curr_track else None,
                maxParticipants=lounge.max_participants,
                participantCount=len(active_joined_at),
                peakParticipants=lounge.peak_participants,
                isLocked=bool(lounge.is_locked),
                settings=_build_settings_schema(lounge),
                createdAt=format_iso_utc(lounge.created_at) or "",
                endedAt=format_iso_utc(lounge.ended_at),
            )
        )

    return LoungeListResponse(lounges=summaries, total=len(summaries))


async def get_lounge_detail(
    db: AsyncSession,
    lounge_id: str,
) -> LoungeDetailResponse:
    """Fetch complete current state for a single DJ Lounge."""
    lounge = await _load_lounge_with_relations(db, lounge_id)
    membership = await _reconstruct_lounge_membership(db, lounge)
    participants: List[LoungeParticipantSchema] = membership["participants"]

    return LoungeDetailResponse(
        loungeId=lounge.id,
        inviteCode=lounge.invite_code,
        status=lounge.status,
        hostUserId=lounge.host_user_id,
        hostDisplayName=membership["hostDisplayName"],
        vibeProfile=lounge.vibe_profile,
        currentTrackId=lounge.current_track_id,
        nowPlaying=_build_now_playing_schema(lounge.current_track),
        nextUp=_build_queue_items(lounge),
        soundclash={
            "active": False,
            "battleId": None,
            "candidates": [],
            "votes": {},
            "closesAt": None,
        },
        sentimentScore=0.5,
        participants=participants,
        participantCount=len(participants),
        peakParticipants=lounge.peak_participants,
        isLocked=bool(lounge.is_locked),
        moderationState=membership["moderationState"],
        settings=_build_settings_schema(lounge),
        createdAt=format_iso_utc(lounge.created_at) or "",
        endedAt=format_iso_utc(lounge.ended_at),
    )


async def join_lounge_by_invite_code(
    db: AsyncSession,
    invite_code: str,
    user: User,
) -> LoungeJoinResponse:
    """Join a DJ Lounge by invite code with lock (403), kick (403), capacity (409), and duplicate-join protection (REQ-007, REQ-019, REQ-025)."""
    normalized_code = normalize_and_validate_invite_code(invite_code)

    stmt = (
        select(DJLounge)
        .where(DJLounge.invite_code == normalized_code)
        .options(
            selectinload(DJLounge.current_track),
            selectinload(DJLounge.queue_entries).selectinload(QueueEntry.track),
        )
    )
    lounge = (await db.execute(stmt)).scalar_one_or_none()
    if lounge is None:
        raise AppException(
            message=f"No DJ Lounge found for invite code '{normalized_code}'.",
            status_code=status.HTTP_404_NOT_FOUND,
            code="LOUNGE_NOT_FOUND",
        )

    lock = _get_lounge_lock(lounge.id)
    async with lock:
        stmt_locked = (
            select(DJLounge)
            .where(DJLounge.id == lounge.id)
            .options(
                selectinload(DJLounge.current_track),
                selectinload(DJLounge.queue_entries).selectinload(QueueEntry.track),
            )
            .with_for_update()
        )
        lounge = (await db.execute(stmt_locked)).scalar_one()
        if lounge.status == "ended":
            raise AppException(
                message="This DJ Lounge session has already ended.",
                status_code=status.HTTP_400_BAD_REQUEST,
                code="LOUNGE_ENDED",
            )

        membership = await _reconstruct_lounge_membership(db, lounge)
        active_joined_at: Dict[str, str] = membership["activeJoinedAt"]
        removed_user_ids: set[str] = membership["removedUserIds"]

        # 1. Removed/kicked users cannot rejoin unless they are the owning Host or Admin
        if (
            user.id in removed_user_ids
            and user.id != lounge.host_user_id
            and user.role != "admin"
        ):
            raise AppException(
                message="You have been removed from this DJ Lounge by the Host.",
                status_code=status.HTTP_403_FORBIDDEN,
                code="USER_REMOVED",
            )

        already_member = user.id in active_joined_at

        # 2. Locked lounge rejects new joiners with HTTP 403 Forbidden
        if (
            lounge.is_locked
            and not already_member
            and user.id != lounge.host_user_id
            and user.role != "admin"
        ):
            raise AppException(
                message="This DJ Lounge is currently locked by the Host.",
                status_code=status.HTTP_403_FORBIDDEN,
                code="LOUNGE_LOCKED",
            )

        # 3. Capacity enforcement up to max_participants (<= 50) -> HTTP 409 Conflict when full
        if not already_member:
            current_count = len(active_joined_at)
            if current_count >= lounge.max_participants:
                raise AppException(
                    message=f"DJ Lounge is full ({lounge.max_participants} max participants).",
                    status_code=status.HTTP_409_CONFLICT,
                    code="LOUNGE_FULL",
                )

            new_count = current_count + 1
            if new_count > lounge.peak_participants:
                lounge.peak_participants = new_count

            join_event = QueueEvent(
                id=f"qev_{uuid.uuid4().hex[:10]}",
                lounge_id=lounge.id,
                user_id=user.id,
                event_type="presence",
                payload={
                    "participantId": user.id,
                    "action": "joined",
                    "role": user.role,
                    "participantCount": new_count,
                },
                ts=_next_event_ts_ms(),
                created_at=utc_now(),
            )
            db.add(join_event)
            await db.commit()

        updated_lounge = await _load_lounge_with_relations(db, lounge.id)
        updated_membership = await _reconstruct_lounge_membership(
            db, updated_lounge
        )
        participants: List[LoungeParticipantSchema] = updated_membership[
            "participants"
        ]

        logger.info(
            "User joined DJ Lounge via invite code",
            extra={
                "event": "lounge.participant.joined",
                "payload": {
                    "loungeId": updated_lounge.id,
                    "userId": user.id,
                    "participantCount": len(participants),
                    "alreadyMember": already_member,
                },
            },
        )

        snapshot = LoungeSnapshotSchema(
            nowPlaying=_build_now_playing_schema(updated_lounge.current_track),
            nextUp=_build_queue_items(updated_lounge),
            soundclash={
                "active": False,
                "battleId": None,
                "candidates": [],
                "votes": {},
                "closesAt": None,
            },
            sentimentScore=0.5,
            participants=participants,
            settings=_build_settings_schema(updated_lounge),
            moderationState=updated_membership["moderationState"],
        )

        return LoungeJoinResponse(
            loungeId=updated_lounge.id,
            inviteCode=updated_lounge.invite_code,
            status=updated_lounge.status,
            wsUrl=f"/ws/lounges/{updated_lounge.id}",
            participantCount=len(participants),
            snapshot=snapshot,
        )


async def start_lounge(
    db: AsyncSession,
    lounge_id: str,
    current_user: User,
) -> LoungeStartResponse:
    """Transition a DJ Lounge from 'scheduled' to 'live' with row-level locking (Host/Admin only, REQ-015, M5-03)."""
    lock = _get_lounge_lock(lounge_id)
    async with lock:
        lounge = await _load_lounge_with_relations(db, lounge_id, for_update=True)
        membership = await _reconstruct_lounge_membership(db, lounge)
        _assert_host_of_lounge_or_admin(
            lounge, current_user, membership["participantRoles"]
        )

        if lounge.status == "ended":
            raise AppException(
                message="Cannot start a DJ Lounge that has already ended.",
                status_code=status.HTTP_400_BAD_REQUEST,
                code="LOUNGE_ALREADY_ENDED",
            )

        if lounge.status != "live":
            lounge.status = "live"
            start_event = QueueEvent(
                id=f"qev_{uuid.uuid4().hex[:10]}",
                lounge_id=lounge.id,
                user_id=current_user.id,
                event_type="lounge_started",
                payload={
                    "loungeId": lounge.id,
                    "hostUserId": current_user.id,
                    "currentTrackId": lounge.current_track_id,
                },
                ts=_next_event_ts_ms(),
                created_at=utc_now(),
            )
            db.add(start_event)
            await db.commit()
            await db.refresh(lounge)

        logger.info(
            "DJ Lounge started",
            extra={
                "event": "lounge.started",
                "payload": {
                    "loungeId": lounge.id,
                    "hostUserId": current_user.id,
                    "currentTrackId": lounge.current_track_id,
                },
            },
        )
        return LoungeStartResponse(
            loungeId=lounge.id,
            status=lounge.status,
            currentTrackId=lounge.current_track_id,
        )


async def end_lounge(
    db: AsyncSession,
    lounge_id: str,
    current_user: User,
) -> LoungeEndResponse:
    """End a DJ Lounge session and persist summary timestamps and event log entry with row-level locking (Host/Admin only, REQ-015, M5-03)."""
    lock = _get_lounge_lock(lounge_id)
    async with lock:
        lounge = await _load_lounge_with_relations(db, lounge_id, for_update=True)
        membership = await _reconstruct_lounge_membership(db, lounge)
        _assert_host_of_lounge_or_admin(
            lounge, current_user, membership["participantRoles"]
        )

        if lounge.status != "ended" or lounge.ended_at is None:
            ended_at = utc_now()
            lounge.status = "ended"
            lounge.ended_at = ended_at

            end_event = QueueEvent(
                id=f"qev_{uuid.uuid4().hex[:10]}",
                lounge_id=lounge.id,
                user_id=current_user.id,
                event_type="lounge_ended",
                payload={
                    "loungeId": lounge.id,
                    "hostUserId": current_user.id,
                    "peakParticipants": lounge.peak_participants,
                },
                ts=_next_event_ts_ms(),
                created_at=ended_at,
            )
            db.add(end_event)
            await db.commit()
            await db.refresh(lounge)

        created_dt = lounge.created_at
        ended_dt = lounge.ended_at or utc_now()
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)
        if ended_dt.tzinfo is None:
            ended_dt = ended_dt.replace(tzinfo=timezone.utc)
        duration_sec = max(0, int((ended_dt - created_dt).total_seconds()))

        logger.info(
            "DJ Lounge ended",
            extra={
                "event": "lounge.ended",
                "payload": {
                    "loungeId": lounge.id,
                    "hostUserId": current_user.id,
                    "durationSeconds": duration_sec,
                    "peakParticipants": lounge.peak_participants,
                },
            },
        )
        return LoungeEndResponse(
            loungeId=lounge.id,
            status=lounge.status,
            endedAt=format_iso_utc(lounge.ended_at) or "",
            summary=LoungeEndSummarySchema(
                durationSeconds=duration_sec,
                peakConcurrentParticipants=lounge.peak_participants,
                totalQueueEntries=len(lounge.queue_entries or []),
            ),
        )


async def moderate_lounge(
    db: AsyncSession,
    lounge_id: str,
    current_user: User,
    payload: LoungeModerationRequest,
) -> LoungeModerationResponse:
    """Execute Host-only moderation actions with row-level locking: mute, unmute, remove, lock, unlock, set_role (REQ-025, REQ-070, M5-02, M5-03)."""
    lock = _get_lounge_lock(lounge_id)
    async with lock:
        lounge = await _load_lounge_with_relations(db, lounge_id, for_update=True)
        membership = await _reconstruct_lounge_membership(db, lounge)
        _assert_host_of_lounge_or_admin(
            lounge, current_user, membership["participantRoles"]
        )

        if lounge.status == "ended":
            raise AppException(
                message="Cannot moderate a DJ Lounge that has already ended.",
                status_code=status.HTTP_400_BAD_REQUEST,
                code="LOUNGE_ENDED",
            )

        action = payload.action
        target_user: User | None = None
        active_joined_at: Dict[str, str] = membership["activeJoinedAt"]

        if action in {"mute", "unmute", "remove", "set_role"}:
            if not payload.targetUserId:
                raise AppException(
                    message=f"targetUserId is required for '{action}' action.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="TARGET_USER_REQUIRED",
                )
            target_user = (
                await db.execute(
                    select(User).where(User.id == payload.targetUserId)
                )
            ).scalar_one_or_none()
            if target_user is None:
                raise AppException(
                    message=f"Target user '{payload.targetUserId}' was not found.",
                    status_code=status.HTTP_404_NOT_FOUND,
                    code="USER_NOT_FOUND",
                )

            # 1. Target user must be an active participant in this lounge
            if target_user.id not in active_joined_at:
                raise AppException(
                    message=f"Target user '{target_user.id}' is not an active participant in this DJ Lounge.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="PARTICIPANT_NOT_IN_LOUNGE",
                )

            # 2. Prohibit moderation of platform administrators
            if target_user.role == "admin":
                raise AppException(
                    message="Platform administrators cannot be moderated.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="CANNOT_MODERATE_ADMIN",
                )

            # 3. Prohibit muting or removing the owning Host
            if target_user.id == lounge.host_user_id and action in {"mute", "unmute", "remove"}:
                raise AppException(
                    message="Cannot moderate the owning Host of the DJ Lounge.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="CANNOT_MODERATE_HOST",
                )

        if action == "lock":
            new_lock_state = (
                bool(payload.locked) if payload.locked is not None else True
            )
            lounge.is_locked = new_lock_state
            db.add(
                QueueEvent(
                    id=f"qev_{uuid.uuid4().hex[:10]}",
                    lounge_id=lounge.id,
                    user_id=current_user.id,
                    event_type="presence",
                    payload={
                        "moderationAction": "lock" if new_lock_state else "unlock",
                        "isLocked": new_lock_state,
                        "moderatorId": current_user.id,
                    },
                    ts=_next_event_ts_ms(),
                    created_at=utc_now(),
                )
            )

        elif action == "unlock":
            lounge.is_locked = False
            db.add(
                QueueEvent(
                    id=f"qev_{uuid.uuid4().hex[:10]}",
                    lounge_id=lounge.id,
                    user_id=current_user.id,
                    event_type="presence",
                    payload={
                        "moderationAction": "unlock",
                        "isLocked": False,
                        "moderatorId": current_user.id,
                    },
                    ts=_next_event_ts_ms(),
                    created_at=utc_now(),
                )
            )

        elif action in {"mute", "unmute"}:
            assert target_user is not None
            db.add(
                QueueEvent(
                    id=f"qev_{uuid.uuid4().hex[:10]}",
                    lounge_id=lounge.id,
                    user_id=current_user.id,
                    event_type="presence",
                    payload={
                        "moderationAction": action,
                        "targetUserId": target_user.id,
                        "participantId": target_user.id,
                        "moderatorId": current_user.id,
                    },
                    ts=_next_event_ts_ms(),
                    created_at=utc_now(),
                )
            )

        elif action == "remove":
            assert target_user is not None
            was_active = target_user.id in active_joined_at
            new_count = max(
                1, len(active_joined_at) - (1 if was_active else 0)
            )
            db.add(
                QueueEvent(
                    id=f"qev_{uuid.uuid4().hex[:10]}",
                    lounge_id=lounge.id,
                    user_id=current_user.id,
                    event_type="presence",
                    payload={
                        "action": "left" if was_active else "moderated",
                        "moderationAction": "remove",
                        "targetUserId": target_user.id,
                        "participantId": target_user.id,
                        "participantCount": new_count,
                        "moderatorId": current_user.id,
                    },
                    ts=_next_event_ts_ms(),
                    created_at=utc_now(),
                )
            )

        elif action == "set_role":
            assert target_user is not None
            if not payload.newRole:
                raise AppException(
                    message="newRole is required for set_role action.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="NEW_ROLE_REQUIRED",
                )
            new_role = payload.newRole.strip().lower()
            if new_role not in {"host", "co_dj", "listener"}:
                raise AppException(
                    message=f"Invalid lounge role '{payload.newRole}'. Allowed roles: ['host', 'co_dj', 'listener'].",
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="INVALID_ROLE",
                )

            # Transfer lounge host ownership if promoted to host
            if new_role == "host":
                lounge.host_user_id = target_user.id

            # NEVER modify global target_user.role!
            # Role is recorded in the lounge presence event stream and scoped strictly to this lounge.
            db.add(
                QueueEvent(
                    id=f"qev_{uuid.uuid4().hex[:10]}",
                    lounge_id=lounge.id,
                    user_id=current_user.id,
                    event_type="presence",
                    payload={
                        "action": "role_updated",
                        "moderationAction": "set_role",
                        "targetUserId": target_user.id,
                        "participantId": target_user.id,
                        "newRole": new_role,
                        "role": new_role,
                        "participantCount": len(active_joined_at),
                        "moderatorId": current_user.id,
                    },
                    ts=_next_event_ts_ms(),
                    created_at=utc_now(),
                )
            )

        await db.commit()
        await db.refresh(lounge)
        updated_membership = await _reconstruct_lounge_membership(db, lounge)

        logger.info(
            "Executed DJ Lounge moderation action",
            extra={
                "event": "lounge.moderation.executed",
                "payload": {
                    "loungeId": lounge.id,
                    "moderatorId": current_user.id,
                    "action": action,
                    "targetUserId": payload.targetUserId,
                    "newRole": payload.newRole,
                },
            },
        )
        return LoungeModerationResponse(
            success=True,
            loungeId=lounge.id,
            action=action,
            moderationState=updated_membership["moderationState"],
        )
