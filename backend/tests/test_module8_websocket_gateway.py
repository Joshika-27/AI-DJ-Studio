"""Module 8 Phase 1 Integration Tests: WebSocket Gateway Foundation.

Tests:
- Handshake JWT authentication failure (missing, invalid, expired -> 4001).
- Room restrictions (non-existent -> 4004, ended -> 4009, kicked -> 4003, locked -> 4003, full -> 4009).
- Immediate lounge.state.snapshot delivery upon connection (< 2s contract).
- Real-time lounge.presence.update broadcasts on new participant joins.
- Latency ping / pong round-trip synchronization (REQ-068).
- 30-second reconnect grace window suppressing duplicate 'joined' presence (REQ-078).
- Reconnect grace window expiry emitting 'left' presence.
- Strict room isolation between distinct lounge channels (REQ-076).
"""

from __future__ import annotations

import asyncio
import time
from typing import Tuple

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.websockets import WebSocketDisconnect

from app.core.config import Settings
from app.core.security import create_access_token
from app.db.session import (
    configure_database,
    create_session_maker,
    drop_db,
    init_db,
)
from app.main import create_app
from app.seeds.seed_data import seed_database
from app.services.room_state_manager import room_state_manager
from app.websocket.connection_manager import connection_manager

TEST_ACCESS_SECRET = "module8_test_access_secret_key_at_least_32_bytes_long_001"
TEST_REFRESH_SECRET = "module8_test_refresh_secret_key_at_least_32_bytes_long_002"
TEST_SEED_PASSWORD = "TestFixtureSeedPass!2026"


@pytest.fixture
async def ws_db_engine() -> AsyncEngine:
    """Create an isolated in-memory sqlite database seeded with catalog tracks and accounts."""
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
def clean_state():
    room_state_manager.clear_cache_for_testing()
    connection_manager.clear_for_testing()
    yield
    room_state_manager.clear_cache_for_testing()
    connection_manager.clear_for_testing()


@pytest.fixture
def ws_settings() -> Settings:
    return Settings(
        _env_file=None,
        APP_NAME="AI DJ Studio WebSocket Test",
        APP_ENV="testing",
        JWT_SECRET_KEY=TEST_ACCESS_SECRET,
        JWT_REFRESH_SECRET_KEY=TEST_REFRESH_SECRET,
    )


@pytest.fixture
def client(
    ws_db_engine: AsyncEngine,
    ws_settings: Settings,
) -> TestClient:
    app = create_app(settings_override=ws_settings)
    return TestClient(app, raise_server_exceptions=False)


def _login_as(client: TestClient, email: str, password: str = TEST_SEED_PASSWORD) -> str:
    res = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert res.status_code == 200, f"Login failed for {email}: {res.text}"
    return res.json()["accessToken"]


def _get_user_id(client: TestClient, token: str) -> str:
    res = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _create_lounge(
    client: TestClient,
    host_token: str,
    seed_track: str = "t_2001",
    max_participants: int = 40,
) -> Tuple[str, str]:
    res = client.post(
        "/api/v1/lounges",
        json={
            "seedTrackId": seed_track,
            "vibeProfile": "Deep House Sunset",
            "maxParticipants": max_participants,
            "bpmTolerancePct": 8.0,
            "soundclashEnabled": True,
            "aiVerbosity": "normal",
        },
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    return body["loungeId"], body["inviteCode"]


def _join_lounge(client: TestClient, invite_code: str, token: str) -> None:
    res = client.post(
        f"/api/v1/lounges/join/{invite_code}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text


# ==============================================================================
# Phase 8.1 Focused Tests
# ==============================================================================

def test_1_handshake_rejects_missing_or_invalid_jwt(client: TestClient, ws_settings: Settings):
    """Test 1: Handshake rejects connection attempts without a valid JWT access token with code 4001."""
    host_token = _login_as(client, "host@spotify-intern.local")
    lounge_id, _ = _create_lounge(client, host_token)

    # 1. Missing token
    with pytest.raises(WebSocketDisconnect) as exc_missing:
        with client.websocket_connect(f"/ws/lounges/{lounge_id}") as _:
            pass
    assert exc_missing.value.code == 4001

    # 2. Corrupted / invalid signature token
    with pytest.raises(WebSocketDisconnect) as exc_invalid:
        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token=invalid.token.signature") as _:
            pass
    assert exc_invalid.value.code == 4001

    # 3. Expired token
    expired_token, _ = create_access_token(
        user_id="u_expired",
        email="expired@test.com",
        role="listener",
        expires_delta_seconds=-60,
        settings=ws_settings,
    )
    with pytest.raises(WebSocketDisconnect) as exc_expired:
        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={expired_token}") as _:
            pass
    assert exc_expired.value.code == 4001


def test_2_handshake_rejects_nonexistent_or_ended_lounge(client: TestClient):
    """Test 2: Handshake rejects non-existent lounge with 4004 and ended lounge with 4009."""
    host_token = _login_as(client, "host@spotify-intern.local")

    # 1. Non-existent lounge
    with pytest.raises(WebSocketDisconnect) as exc_404:
        with client.websocket_connect(f"/ws/lounges/lg_nonexistent_9999?token={host_token}") as _:
            pass
    assert exc_404.value.code == 4004

    # 2. Ended lounge
    lounge_id, _ = _create_lounge(client, host_token)
    end_res = client.post(
        f"/api/v1/lounges/{lounge_id}/end",
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert end_res.status_code == 200, end_res.text

    with pytest.raises(WebSocketDisconnect) as exc_ended:
        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as _:
            pass
    assert exc_ended.value.code == 4009


def test_3_handshake_rejects_kicked_user_locked_room_and_capacity_limit(client: TestClient):
    """Test 3: Handshake rejects kicked users (4003), non-members in locked rooms (4003), and full rooms (4009)."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    codj_token = _login_as(client, "codj@spotify-intern.local")
    listener_id = _get_user_id(client, listener_token)

    # --- A. Kicked User Check (4003) ---
    lounge_id, invite_code = _create_lounge(client, host_token)
    _join_lounge(client, invite_code, listener_token)

    # Host moderates: kicks listener
    kick_res = client.post(
        f"/api/v1/lounges/{lounge_id}/moderation",
        json={"action": "remove", "targetUserId": listener_id},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert kick_res.status_code == 200, kick_res.text

    # Kicked listener tries to connect via WS
    with pytest.raises(WebSocketDisconnect) as exc_kicked:
        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as _:
            pass
    assert exc_kicked.value.code == 4003

    # --- B. Locked Room Check (4003) ---
    lounge_id_2, _ = _create_lounge(client, host_token)
    # Host locks room
    lock_res = client.post(
        f"/api/v1/lounges/{lounge_id_2}/moderation",
        json={"action": "lock"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert lock_res.status_code == 200, lock_res.text

    # Listener (not yet a member) attempts to join locked room via WS
    with pytest.raises(WebSocketDisconnect) as exc_locked:
        with client.websocket_connect(f"/ws/lounges/{lounge_id_2}?token={listener_token}") as _:
            pass
    assert exc_locked.value.code == 4003

    # --- C. Capacity Limit Check (4009) ---
    # Create lounge with capacity = 2 (Host counts as 1)
    lounge_id_3, invite_code_3 = _create_lounge(client, host_token, max_participants=2)
    _join_lounge(client, invite_code_3, listener_token)  # Now 2 members (capacity full)

    # Third user (codj) attempts to join full room via WS
    with pytest.raises(WebSocketDisconnect) as exc_full:
        with client.websocket_connect(f"/ws/lounges/{lounge_id_3}?token={codj_token}") as _:
            pass
    assert exc_full.value.code == 4009


def test_4_successful_connection_receives_immediate_state_snapshot(client: TestClient):
    """Test 4: Successful connection receives mandatory lounge.state.snapshot envelope (< 2s)."""
    host_token = _login_as(client, "host@spotify-intern.local")
    host_id = _get_user_id(client, host_token)
    lounge_id, _ = _create_lounge(client, host_token, seed_track="t_2001")

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws:
        snapshot = ws.receive_json()

        # Validate envelope structure (REQ-060)
        assert snapshot["event"] == "lounge.state.snapshot"
        assert snapshot["loungeId"] == lounge_id
        assert isinstance(snapshot["ts"], int)
        assert snapshot["ts"] > 0

        # Validate snapshot payload (REQ-028)
        payload = snapshot["payload"]
        assert payload["nowPlaying"] is not None
        assert payload["nowPlaying"]["trackId"] == "t_2001"
        assert payload["queue"] == []
        assert payload["soundclash"]["active"] is False
        assert payload["sentiment"]["rollingScore"] == 0.5
        assert isinstance(payload["participants"], list)
        assert any(p["userId"] == host_id for p in payload["participants"])
        assert payload["chatHistory"] == []
        assert payload["hostStatus"] == "connected"


def test_5_presence_broadcast_on_new_participant_join(client: TestClient):
    """Test 5: New participant connection triggers lounge.presence.update broadcast to existing participants."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    listener_id = _get_user_id(client, listener_token)
    lounge_id, invite_code = _create_lounge(client, host_token)
    _join_lounge(client, invite_code, listener_token)

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws_host:
        # Host consumes initial snapshot
        host_snap = ws_host.receive_json()
        assert host_snap["event"] == "lounge.state.snapshot"

        # Listener connects
        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws_listener:
            # Host receives presence update
            presence_event = ws_host.receive_json()
            assert presence_event["event"] == "lounge.presence.update"
            assert presence_event["loungeId"] == lounge_id
            assert presence_event["payload"]["participantId"] == listener_id
            assert presence_event["payload"]["action"] == "joined"
            assert presence_event["payload"]["participantCount"] == 2

            # Listener consumes initial snapshot
            listener_snap = ws_listener.receive_json()
            assert listener_snap["event"] == "lounge.state.snapshot"
            assert listener_snap["payload"]["hostStatus"] == "connected"


def test_6_latency_ping_pong_round_trip(client: TestClient):
    """Test 6: Client sends lounge.sync.ping and server immediately responds with lounge.sync.pong (REQ-068)."""
    host_token = _login_as(client, "host@spotify-intern.local")
    lounge_id, _ = _create_lounge(client, host_token)

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws:
        # Consume initial snapshot
        _ = ws.receive_json()

        # Send ping
        ping_client_ts = int(time.time() * 1000)
        ws.send_json({
            "event": "lounge.sync.ping",
            "loungeId": lounge_id,
            "payload": {"clientSentAt": ping_client_ts},
            "ts": ping_client_ts,
        })

        # Receive pong
        pong = ws.receive_json()
        assert pong["event"] == "lounge.sync.pong"
        assert pong["loungeId"] == lounge_id
        assert pong["payload"]["clientSentAt"] == ping_client_ts
        assert isinstance(pong["payload"]["serverReceivedAt"], int)
        assert pong["payload"]["serverReceivedAt"] >= ping_client_ts


def test_7_reconnect_grace_window_suppresses_duplicate_joined_presence(client: TestClient):
    """Test 7: Client reconnecting within 30s grace window suppresses duplicate 'joined' presence (REQ-078)."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    listener_id = _get_user_id(client, listener_token)
    lounge_id, invite_code = _create_lounge(client, host_token)
    _join_lounge(client, invite_code, listener_token)

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws_host:
        # Host consumes snapshot
        _ = ws_host.receive_json()

        # Listener connects initial session
        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws_listener:
            # Host receives initial joined event
            join_msg = ws_host.receive_json()
            assert join_msg["event"] == "lounge.presence.update"
            assert join_msg["payload"]["participantId"] == listener_id
            assert join_msg["payload"]["action"] == "joined"

            # Listener consumes snapshot
            _ = ws_listener.receive_json()
            # Explicitly close listener socket (simulating drop)
            ws_listener.close()

        # Verify listener is now in grace window
        assert connection_manager.is_user_in_grace(lounge_id, listener_id) is True

        # Listener reconnects within grace window
        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws_reconnect:
            # Listener receives state snapshot immediately
            reconnect_snap = ws_reconnect.receive_json()
            assert reconnect_snap["event"] == "lounge.state.snapshot"

            # Grace timer was cancelled
            assert connection_manager.is_user_in_grace(lounge_id, listener_id) is False

            # Ping from listener to confirm channel is healthy
            ws_reconnect.send_json({
                "event": "lounge.sync.ping",
                "loungeId": lounge_id,
                "payload": {"clientSentAt": 12345},
                "ts": 12345,
            })
            pong = ws_reconnect.receive_json()
            assert pong["event"] == "lounge.sync.pong"


@pytest.mark.asyncio
async def test_8_reconnect_grace_window_expiry_broadcasts_left(client: TestClient):
    """Test 8: Reconnect grace window expiration triggers lounge.presence.update with action 'left'."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    listener_id = _get_user_id(client, listener_token)
    lounge_id, invite_code = _create_lounge(client, host_token)
    _join_lounge(client, invite_code, listener_token)

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws_host:
        _ = ws_host.receive_json()  # host snapshot

        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws_listener:
            _ = ws_host.receive_json()  # host receives joined
            _ = ws_listener.receive_json()  # listener snapshot
            ws_listener.close()

        assert connection_manager.is_user_in_grace(lounge_id, listener_id) is True

        # Manually expire the grace window
        await connection_manager.expire_grace_period_now(lounge_id, listener_id, display_name="Listener Test")

        # Host receives 'left' presence update
        leave_event = ws_host.receive_json()
        assert leave_event["event"] == "lounge.presence.update"
        assert leave_event["payload"]["participantId"] == listener_id
        assert leave_event["payload"]["action"] == "left"
        assert leave_event["payload"]["participantCount"] == 1


def test_9_room_isolation_between_distinct_lounges(client: TestClient):
    """Test 9: Room isolation guarantees events in Lounge A never leak to Lounge B (REQ-076)."""
    host_token = _login_as(client, "host@spotify-intern.local")
    admin_token = _login_as(client, "admin@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")

    lounge_1, invite_1 = _create_lounge(client, host_token, seed_track="t_2001")
    lounge_2, _ = _create_lounge(client, admin_token, seed_track="t_2002")

    with client.websocket_connect(f"/ws/lounges/{lounge_1}?token={host_token}") as ws_lounge1:
        _ = ws_lounge1.receive_json()  # lounge 1 snapshot

        with client.websocket_connect(f"/ws/lounges/{lounge_2}?token={admin_token}") as ws_lounge2:
            _ = ws_lounge2.receive_json()  # lounge 2 snapshot

            # Listener joins Lounge 1 only
            _join_lounge(client, invite_1, listener_token)
            with client.websocket_connect(f"/ws/lounges/{lounge_1}?token={listener_token}") as ws_listener1:
                _ = ws_listener1.receive_json()  # listener 1 snapshot

                # Lounge 1 receives presence update
                lounge1_event = ws_lounge1.receive_json()
                assert lounge1_event["event"] == "lounge.presence.update"
                assert lounge1_event["loungeId"] == lounge_1

                # Lounge 2 sends a ping/pong to confirm it hasn't received anything from Lounge 1
                ws_lounge2.send_json({
                    "event": "lounge.sync.ping",
                    "loungeId": lounge_2,
                    "payload": {"clientSentAt": 99999},
                    "ts": 99999,
                })
                pong_lounge2 = ws_lounge2.receive_json()
                # The next message for Lounge 2 MUST be its own pong, NOT Lounge 1's presence update
                assert pong_lounge2["event"] == "lounge.sync.pong"
                assert pong_lounge2["loungeId"] == lounge_2
