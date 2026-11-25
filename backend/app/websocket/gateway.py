"""WebSocket Gateway endpoint and request router (Module 8 Phase 1 — REQ-060..REQ-078)."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional
import uuid

from fastapi import APIRouter
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from starlette.websockets import WebSocket, WebSocketDisconnect

from app.core.config import Settings, get_settings
from app.core.exceptions import AppException
from app.core.logging import get_logger
from app.core.security import decode_access_token
from app.db.base import utc_now
from app.db.session import get_session_maker
from app.models.lounge import DJLounge
from app.models.queue import QueueEntry, QueueEvent
from app.models.user import User
from app.schemas.lounge import format_iso_utc
from app.schemas.queue import QueueVoteRequest
from app.schemas.websocket import LoungeQueueVotePayload, make_ws_envelope
from app.services.lounge_service import (
    _reconstruct_lounge_membership,
    vote_track_in_lounge_queue,
)
from app.services.room_state_manager import room_state_manager
from app.websocket.connection_manager import connection_manager

logger = get_logger("websocket.gateway")

router = APIRouter(tags=["WebSocket Gateway"])


@router.websocket("/ws/lounges/{loungeId}")
async def websocket_lounge_endpoint(websocket: WebSocket, loungeId: str) -> None:
    """Real-time room-scoped WebSocket connection for a DJ Lounge session (REQ-060, REQ-071).

    Handshake validation:
    - 4001: Missing, expired, or invalid JWT access token.
    - 4003: User removed/kicked or non-member entering locked room.
    - 4004: Lounge not found.
    - 4009: Lounge ended or lounge full.
    """
    # 1. Extract token from query params or Authorization header (REQ-071)
    token: Optional[str] = websocket.query_params.get("token")
    if not token:
        auth_header = websocket.headers.get("authorization") or websocket.headers.get("Authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()

    if not token:
        logger.warning("WebSocket handshake rejected: missing token", extra={"loungeId": loungeId})
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # 2. Decode and validate JWT
    settings: Settings = getattr(websocket.app.state, "settings", None) or get_settings()
    try:
        payload = decode_access_token(token, settings=settings)
    except AppException as exc:
        logger.warning(
            f"WebSocket handshake rejected: token decode error ({exc.code})",
            extra={"loungeId": loungeId, "code": exc.code},
        )
        await websocket.close(code=4001, reason="Unauthorized")
        return
    except Exception as exc:
        logger.warning(
            "WebSocket handshake rejected: unexpected token validation failure",
            extra={"loungeId": loungeId, "error": str(exc)},
        )
        await websocket.close(code=4001, reason="Unauthorized")
        return

    user_id = payload.get("sub")
    user_role = payload.get("role", "listener")
    if not user_id:
        logger.warning("WebSocket handshake rejected: token missing sub", extra={"loungeId": loungeId})
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # 3. Database validation: lounge existence, status, membership, moderation, capacity
    session_maker = get_session_maker()
    async with session_maker() as db:
        # Load lounge
        stmt = (
            select(DJLounge)
            .where(DJLounge.id == loungeId)
            .options(
                selectinload(DJLounge.current_track),
                selectinload(DJLounge.queue_entries).selectinload(QueueEntry.track),
            )
        )
        lounge = (await db.execute(stmt)).scalar_one_or_none()
        if lounge is None:
            logger.warning(
                f"WebSocket handshake rejected: lounge '{loungeId}' not found",
                extra={"loungeId": loungeId, "userId": user_id},
            )
            await websocket.close(code=4004, reason="Lounge Not Found")
            return

        # Check ended status
        if lounge.status == "ended":
            logger.warning(
                f"WebSocket handshake rejected: lounge '{loungeId}' has ended",
                extra={"loungeId": loungeId, "userId": user_id},
            )
            await websocket.close(code=4009, reason="Lounge Ended")
            return

        # Load user
        user_stmt = select(User).where(User.id == user_id)
        user_obj = (await db.execute(user_stmt)).scalar_one_or_none()
        if user_obj is None:
            logger.warning(
                f"WebSocket handshake rejected: user '{user_id}' not found in DB",
                extra={"loungeId": loungeId, "userId": user_id},
            )
            await websocket.close(code=4001, reason="User Not Found")
            return

        display_name = user_obj.display_name or f"User {user_id}"

        # Reconstruct membership and moderation state
        membership = await _reconstruct_lounge_membership(db, lounge)
        active_joined_at: Dict[str, str] = membership["activeJoinedAt"]
        removed_user_ids: set[str] = membership["removedUserIds"]
        participant_roles: Dict[str, str] = membership["participantRoles"]

        is_host = (user_id == lounge.host_user_id)
        is_admin = (user_role == "admin" or user_obj.role == "admin")
        already_member = (user_id in active_joined_at)

        # Kick check
        if user_id in removed_user_ids and not is_host and not is_admin:
            logger.warning(
                f"WebSocket handshake rejected: user '{user_id}' was kicked from lounge '{loungeId}'",
                extra={"loungeId": loungeId, "userId": user_id},
            )
            await websocket.close(code=4003, reason="Forbidden")
            return

        # Lock check
        if lounge.is_locked and not already_member and not is_host and not is_admin:
            logger.warning(
                f"WebSocket handshake rejected: lounge '{loungeId}' is locked",
                extra={"loungeId": loungeId, "userId": user_id},
            )
            await websocket.close(code=4003, reason="Room Locked")
            return

        # Capacity check
        if not already_member and not is_host and not is_admin:
            if len(active_joined_at) >= lounge.max_participants:
                logger.warning(
                    f"WebSocket handshake rejected: lounge '{loungeId}' is full",
                    extra={"loungeId": loungeId, "userId": user_id},
                )
                await websocket.close(code=4009, reason="Room Full")
                return

        # Persist presence join event if brand new member
        if not already_member:
            new_count = len(active_joined_at) + 1
            if new_count > lounge.peak_participants:
                lounge.peak_participants = new_count
            join_dt = utc_now()
            join_event = QueueEvent(
                id=f"qev_{uuid.uuid4().hex[:10]}",
                lounge_id=lounge.id,
                user_id=user_id,
                event_type="presence",
                payload={
                    "action": "joined",
                    "participantId": user_id,
                    "displayName": display_name,
                    "role": participant_roles.get(user_id, user_obj.role),
                    "participantCount": new_count,
                },
                ts=int(join_dt.timestamp() * 1000),
                created_at=join_dt,
            )
            db.add(join_event)
            await db.commit()
            active_joined_at[user_id] = format_iso_utc(join_dt) or ""
            if user_id not in participant_roles:
                participant_roles[user_id] = user_obj.role

        # Hydrate RoomState via authoritative RoomStateManager
        room_state = await room_state_manager.get_or_hydrate_room(db, lounge.id)

        # Resolve lounge-scoped effective role
        effective_role = participant_roles.get(user_id, user_obj.role)
        if is_host:
            effective_role = "host"

        # Precompute snapshot data
        now_playing_data = (
            room_state.now_playing.to_schema().model_dump()
            if room_state.now_playing
            else None
        )
        queue_data = [entry.to_schema().model_dump() for entry in room_state.next_up]

        soundclash_data = {
            "active": room_state.soundclash_stage.status == "active",
            "battleId": room_state.soundclash_stage.active_battle_id,
            "candidates": list(room_state.soundclash_stage.candidate_track_ids),
            "votes": {},
            "closesAt": None,
        }
        sentiment_data = {
            "rollingScore": 0.5,
            "windowSeconds": 60,
            "sampleSize": 0,
        }
        participants_data = [p.model_dump() for p in membership["participants"]]
        if not any(p.get("userId") == user_id for p in participants_data):
            participants_data.append(
                {
                    "userId": user_id,
                    "displayName": display_name,
                    "role": effective_role,
                    "isMuted": False,
                    "joinedAt": format_iso_utc(utc_now()) or "",
                }
            )

    # 4. Accept WebSocket connection
    await websocket.accept()

    # 5. Register in connection manager
    is_reconnect = await connection_manager.connect(
        lounge_id=loungeId,
        user_id=user_id,
        display_name=display_name,
        role=effective_role,
        websocket=websocket,
    )

    # 6. Deliver immediate mid-session snapshot to this client (REQ-028)
    host_status = connection_manager.get_host_status(loungeId, lounge.host_user_id)
    snapshot_msg = make_ws_envelope(
        event="lounge.state.snapshot",
        lounge_id=loungeId,
        payload={
            "nowPlaying": now_playing_data,
            "queue": queue_data,
            "soundclash": soundclash_data,
            "sentiment": sentiment_data,
            "participants": participants_data,
            "chatHistory": [],
            "hostStatus": host_status,
        },
    )
    await connection_manager.send_personal(websocket, snapshot_msg)

    # 7. Broadcast joined presence to other participants if NOT a reconnect or multi-tab (REQ-062, REQ-078)
    if not is_reconnect:
        participant_count = connection_manager.get_active_participant_count(loungeId)
        presence_msg = make_ws_envelope(
            event="lounge.presence.update",
            lounge_id=loungeId,
            payload={
                "participantId": user_id,
                "displayName": display_name,
                "action": "joined",
                "participantCount": participant_count,
            },
        )
        await connection_manager.broadcast(loungeId, presence_msg, exclude_ws=websocket)

    # 8. Receive loop for ping/pong (REQ-068) and live queue voting (REQ-061)
    try:
        while True:
            data = await websocket.receive_json()
            if not isinstance(data, dict):
                err_msg = make_ws_envelope(
                    event="lounge.error",
                    lounge_id=loungeId,
                    payload={"code": "INVALID_PAYLOAD", "message": "Expected JSON object."},
                )
                await connection_manager.send_personal(websocket, err_msg)
                continue

            event_name = data.get("event")
            payload = data.get("payload")
            envelope_lounge_id = data.get("loungeId")
            if not isinstance(payload, dict):
                payload = {}

            # Cross-lounge isolation check (REQ-076)
            if envelope_lounge_id and envelope_lounge_id != loungeId:
                err_msg = make_ws_envelope(
                    event="lounge.error",
                    lounge_id=loungeId,
                    payload={
                        "code": "FORBIDDEN",
                        "message": f"Cross-lounge operations are forbidden: '{envelope_lounge_id}' != '{loungeId}'.",
                    },
                )
                await connection_manager.send_personal(websocket, err_msg)
                continue

            if event_name == "lounge.sync.ping":
                client_sent_at = payload.get("clientSentAt")
                if client_sent_at is not None:
                    server_now_ms = int(time.time() * 1000)
                    pong_msg = make_ws_envelope(
                        event="lounge.sync.pong",
                        lounge_id=loungeId,
                        payload={
                            "clientSentAt": client_sent_at,
                            "serverReceivedAt": server_now_ms,
                        },
                    )
                    await connection_manager.send_personal(websocket, pong_msg)

            elif event_name == "lounge.queue.vote":
                # Validate envelope loungeId requirement (REQ-060, REQ-061)
                if not envelope_lounge_id:
                    err_msg = make_ws_envelope(
                        event="lounge.error",
                        lounge_id=loungeId,
                        payload={
                            "code": "INVALID_PAYLOAD",
                            "message": "Envelope 'loungeId' is required.",
                        },
                    )
                    await connection_manager.send_personal(websocket, err_msg)
                    continue

                # Validate vote payload schema (REQ-061)
                try:
                    vote_payload = LoungeQueueVotePayload(**payload)
                except (ValidationError, ValueError):
                    err_msg = make_ws_envelope(
                        event="lounge.error",
                        lounge_id=loungeId,
                        payload={
                            "code": "INVALID_PAYLOAD",
                            "message": "Malformed vote payload: trackId must be a valid non-empty identifier without extra fields.",
                        },
                    )
                    await connection_manager.send_personal(websocket, err_msg)
                    continue

                # Execute authoritative vote via existing service logic
                session_maker = get_session_maker()
                async with session_maker() as db:
                    user_stmt = select(User).where(User.id == user_id)
                    current_user = (await db.execute(user_stmt)).scalar_one_or_none()
                    if current_user is None:
                        err_msg = make_ws_envelope(
                            event="lounge.error",
                            lounge_id=loungeId,
                            payload={"code": "UNAUTHORIZED", "message": "User not found."},
                        )
                        await connection_manager.send_personal(websocket, err_msg)
                        continue

                    try:
                        await vote_track_in_lounge_queue(
                            db=db,
                            lounge_id=loungeId,
                            current_user=current_user,
                            payload=QueueVoteRequest(trackId=vote_payload.trackId),
                        )
                    except AppException as exc:
                        err_msg = make_ws_envelope(
                            event="lounge.error",
                            lounge_id=loungeId,
                            payload={"code": exc.code, "message": exc.message},
                        )
                        await connection_manager.send_personal(websocket, err_msg)
                    except Exception as exc:
                        logger.error(
                            f"Unexpected error processing WebSocket vote in lounge '{loungeId}': {exc}",
                            extra={"loungeId": loungeId, "userId": user_id, "error": str(exc)},
                        )
                        err_msg = make_ws_envelope(
                            event="lounge.error",
                            lounge_id=loungeId,
                            payload={"code": "INTERNAL_ERROR", "message": "Failed to process vote."},
                        )
                        await connection_manager.send_personal(websocket, err_msg)

            else:
                err_msg = make_ws_envelope(
                    event="lounge.error",
                    lounge_id=loungeId,
                    payload={
                        "code": "UNSUPPORTED_EVENT",
                        "message": f"Unsupported or unrecognized WebSocket event '{event_name}'.",
                    },
                )
                await connection_manager.send_personal(websocket, err_msg)
    except WebSocketDisconnect:
        logger.info(
            f"WebSocket disconnected for user '{user_id}' in lounge '{loungeId}'",
            extra={"loungeId": loungeId, "userId": user_id},
        )
    except Exception as exc:
        logger.warning(
            f"WebSocket exception for user '{user_id}' in lounge '{loungeId}': {exc}",
            extra={"loungeId": loungeId, "userId": user_id, "error": str(exc)},
        )
    finally:
        await connection_manager.disconnect(loungeId, user_id, websocket)
