"""In-Memory Room-Scoped WebSocket Connection Registry & Grace Window Manager (Module 8 — REQ-060, REQ-062, REQ-071, REQ-076, REQ-078)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import time
from typing import Any, Dict, List, Optional, Set

from starlette.websockets import WebSocket, WebSocketDisconnect

from app.core.logging import get_logger
from app.schemas.websocket import make_ws_envelope

logger = get_logger("websocket.connection_manager")

DEFAULT_RECONNECT_GRACE_SECONDS = 30.0


@dataclass
class ConnectedUserSession:
    """Represents an authenticated participant session with one or more open sockets (tabs)."""

    user_id: str
    display_name: str
    role: str
    lounge_id: str
    sockets: Set[WebSocket] = field(default_factory=set)
    connected_at: float = field(default_factory=time.time)


class WebSocketConnectionManager:
    """Room-scoped WebSocket connection registry with 30s reconnect grace management (REQ-076, REQ-078)."""

    def __init__(self, reconnect_grace_seconds: float = DEFAULT_RECONNECT_GRACE_SECONDS) -> None:
        self.reconnect_grace_seconds = reconnect_grace_seconds
        # _rooms[lounge_id][user_id] -> ConnectedUserSession
        self._rooms: Dict[str, Dict[str, ConnectedUserSession]] = {}
        # _grace_timers[lounge_id][user_id] -> asyncio.Task
        self._grace_timers: Dict[str, Dict[str, asyncio.Task]] = {}
        # _typing_users[lounge_id] -> Set[user_id]
        self._typing_users: Dict[str, Set[str]] = {}

    def get_room_user_sessions(self, lounge_id: str) -> Dict[str, ConnectedUserSession]:
        """Return the dictionary of active user sessions in the given lounge."""
        return self._rooms.get(lounge_id, {})

    def is_user_connected(self, lounge_id: str, user_id: str) -> bool:
        """Check if a user currently has at least one active socket connected in the lounge."""
        room = self._rooms.get(lounge_id)
        if not room:
            return False
        session = room.get(user_id)
        return bool(session and session.sockets)

    def is_user_in_grace(self, lounge_id: str, user_id: str) -> bool:
        """Check if a user is currently within their disconnect grace window."""
        return user_id in self._grace_timers.get(lounge_id, {})

    def get_active_participant_count(self, lounge_id: str) -> int:
        """Compute the total count of active and grace-window participants for a lounge."""
        connected_users = set(self._rooms.get(lounge_id, {}).keys())
        grace_users = set(self._grace_timers.get(lounge_id, {}).keys())
        return len(connected_users | grace_users)

    def get_active_user_ids(self, lounge_id: str) -> Set[str]:
        """Return set of active user IDs (including grace window) for a lounge."""
        connected_users = set(self._rooms.get(lounge_id, {}).keys())
        grace_users = set(self._grace_timers.get(lounge_id, {}).keys())
        return connected_users | grace_users

    def get_host_status(self, lounge_id: str, host_user_id: str) -> str:
        """Return 'connected' or 'host_disconnected' based on host socket presence."""
        if self.is_user_connected(lounge_id, host_user_id):
            return "connected"
        return "host_disconnected"

    async def connect(
        self,
        lounge_id: str,
        user_id: str,
        display_name: str,
        role: str,
        websocket: WebSocket,
    ) -> bool:
        """Register a new WebSocket connection for a participant.

        Returns:
            is_reconnect (bool): True if reconnected within the grace window or
            adding a secondary socket, suppressing duplicate 'joined' presence broadcasts.
        """
        if lounge_id not in self._rooms:
            self._rooms[lounge_id] = {}

        is_reconnect = False

        # 1. Check if user is in grace window (reconnecting after drop)
        grace_room = self._grace_timers.get(lounge_id, {})
        if user_id in grace_room:
            grace_task = grace_room.pop(user_id)
            if not grace_task.done():
                grace_task.cancel()
            is_reconnect = True
            logger.info(
                f"User '{user_id}' reconnected within grace window to lounge '{lounge_id}'",
                extra={
                    "event": "websocket.reconnect.grace_restored",
                    "payload": {"loungeId": lounge_id, "userId": user_id},
                },
            )

        room = self._rooms[lounge_id]
        if user_id in room:
            # User already has an existing active connection (e.g. another tab)
            session = room[user_id]
            session.sockets.add(websocket)
            session.display_name = display_name
            session.role = role
            is_reconnect = True
        else:
            # New active session
            session = ConnectedUserSession(
                user_id=user_id,
                display_name=display_name,
                role=role,
                lounge_id=lounge_id,
                sockets={websocket},
            )
            room[user_id] = session

        logger.info(
            f"WebSocket registered for user '{user_id}' in lounge '{lounge_id}' (is_reconnect={is_reconnect})",
            extra={
                "event": "websocket.connected",
                "payload": {
                    "loungeId": lounge_id,
                    "userId": user_id,
                    "isReconnect": is_reconnect,
                    "socketCount": len(session.sockets),
                },
            },
        )
        return is_reconnect

    async def disconnect(
        self,
        lounge_id: str,
        user_id: str,
        websocket: WebSocket,
    ) -> None:
        """Unregister a WebSocket connection and start the 30s reconnect grace timer if all sockets closed."""
        room = self._rooms.get(lounge_id, {})
        session = room.get(user_id)
        if not session:
            return

        session.sockets.discard(websocket)

        # If user still has another active tab/socket open, do not enter grace window
        if session.sockets:
            logger.info(
                f"Secondary socket closed for user '{user_id}' in lounge '{lounge_id}' ({len(session.sockets)} remaining)",
                extra={
                    "event": "websocket.socket_closed",
                    "payload": {
                        "loungeId": lounge_id,
                        "userId": user_id,
                        "remainingSockets": len(session.sockets),
                    },
                },
            )
            return

        # Last socket disconnected: remove from active room and start grace timer
        room.pop(user_id, None)
        if not room:
            self._rooms.pop(lounge_id, None)

        display_name = session.display_name

        # Start 30-second grace window task (REQ-078)
        if lounge_id not in self._grace_timers:
            self._grace_timers[lounge_id] = {}

        # Cancel any pre-existing timer for safety
        existing_timer = self._grace_timers[lounge_id].pop(user_id, None)
        if existing_timer and not existing_timer.done():
            existing_timer.cancel()

        grace_task = asyncio.create_task(
            self._grace_window_countdown(
                lounge_id=lounge_id,
                user_id=user_id,
                display_name=display_name,
                delay_seconds=self.reconnect_grace_seconds,
            )
        )
        self._grace_timers[lounge_id][user_id] = grace_task

        logger.info(
            f"User '{user_id}' entered {self.reconnect_grace_seconds}s reconnect grace window for lounge '{lounge_id}'",
            extra={
                "event": "websocket.disconnect.grace_started",
                "payload": {
                    "loungeId": lounge_id,
                    "userId": user_id,
                    "graceSeconds": self.reconnect_grace_seconds,
                },
            },
        )

    async def _grace_window_countdown(
        self,
        lounge_id: str,
        user_id: str,
        display_name: str,
        delay_seconds: float,
    ) -> None:
        """Countdown coroutine that broadcasts presence 'left' event if the grace window expires without reconnect."""
        try:
            await asyncio.sleep(delay_seconds)
            # Grace window expired: cleanup timer and broadcast left event
            grace_room = self._grace_timers.get(lounge_id, {})
            grace_room.pop(user_id, None)
            if not grace_room and lounge_id in self._grace_timers:
                self._grace_timers.pop(lounge_id, None)

            active_count = self.get_active_participant_count(lounge_id)
            logger.info(
                f"Reconnect grace window expired for user '{user_id}' in lounge '{lounge_id}'",
                extra={
                    "event": "websocket.grace_expired",
                    "payload": {
                        "loungeId": lounge_id,
                        "userId": user_id,
                        "activeCount": active_count,
                    },
                },
            )

            leave_envelope = make_ws_envelope(
                event="lounge.presence.update",
                lounge_id=lounge_id,
                payload={
                    "participantId": user_id,
                    "displayName": display_name,
                    "action": "left",
                    "participantCount": active_count,
                },
            )
            await self.broadcast(lounge_id, leave_envelope)
        except asyncio.CancelledError:
            # Reconnected before grace timer elapsed: intentional suppression of 'left' broadcast
            pass

    async def expire_grace_period_now(self, lounge_id: str, user_id: str, display_name: Optional[str] = None) -> None:
        """Immediately expire a pending reconnect grace period and broadcast presence 'left' (REQ-078)."""
        grace_room = self._grace_timers.get(lounge_id, {})
        timer = grace_room.pop(user_id, None)
        if timer and not timer.done():
            timer.cancel()
        if not grace_room and lounge_id in self._grace_timers:
            self._grace_timers.pop(lounge_id, None)

        active_count = self.get_active_participant_count(lounge_id)
        leave_envelope = make_ws_envelope(
            event="lounge.presence.update",
            lounge_id=lounge_id,
            payload={
                "participantId": user_id,
                "displayName": display_name or f"User {user_id}",
                "action": "left",
                "participantCount": active_count,
            },
        )
        await self.broadcast(lounge_id, leave_envelope)

    async def broadcast(
        self,
        lounge_id: str,
        message: Dict[str, Any],
        exclude_ws: Optional[WebSocket] = None,
    ) -> None:
        """Broadcast a WebSocket envelope to all connected sockets in a specific lounge (REQ-076)."""
        room = self._rooms.get(lounge_id)
        if not room:
            return

        dead_sockets: List[tuple[str, WebSocket]] = []

        for user_id, session in list(room.items()):
            for ws in list(session.sockets):
                if exclude_ws is not None and ws is exclude_ws:
                    continue
                try:
                    await ws.send_json(message)
                except (WebSocketDisconnect, RuntimeError) as exc:
                    logger.debug(
                        f"Failed to send to socket in lounge '{lounge_id}' for user '{user_id}': {exc}"
                    )
                    dead_sockets.append((user_id, ws))

        # Cleanup any dead sockets discovered during broadcast
        for uid, dead_ws in dead_sockets:
            if uid in room:
                room[uid].sockets.discard(dead_ws)
                if not room[uid].sockets:
                    room.pop(uid, None)

    async def send_personal(
        self,
        websocket: WebSocket,
        message: Dict[str, Any],
    ) -> None:
        """Send a WebSocket envelope directly to a single socket client."""
        await websocket.send_json(message)

    def set_user_typing(self, lounge_id: str, user_id: str, is_typing: bool) -> bool:
        """Update in-memory typing state for a user in a lounge with deduplication.

        Returns:
            bool: True if the state changed (i.e. broadcast should be sent), False if unchanged (throttled).
        """
        if lounge_id not in self._typing_users:
            self._typing_users[lounge_id] = set()

        current = user_id in self._typing_users[lounge_id]
        if is_typing == current:
            return False  # State unchanged; deduplicated

        if is_typing:
            self._typing_users[lounge_id].add(user_id)
        else:
            self._typing_users[lounge_id].discard(user_id)
        return True

    def is_user_typing(self, lounge_id: str, user_id: str) -> bool:
        """Check if a user is currently marked as typing in a lounge."""
        return user_id in self._typing_users.get(lounge_id, set())

    def clear_user_typing(self, lounge_id: str, user_id: str) -> bool:
        """Clear typing state for a user (e.g. on disconnect or message sent). Returns True if user was typing."""
        room_typing = self._typing_users.get(lounge_id)
        if room_typing and user_id in room_typing:
            room_typing.discard(user_id)
            return True
        return False

    def clear_for_testing(self) -> None:
        """Helper to cancel all active grace tasks and reset all room state during test teardown."""
        for lounge_id, timers in list(self._grace_timers.items()):
            for uid, task in list(timers.items()):
                if not task.done():
                    task.cancel()
        self._grace_timers.clear()
        self._rooms.clear()
        self._typing_users.clear()


# Global singleton connection manager
connection_manager = WebSocketConnectionManager()
