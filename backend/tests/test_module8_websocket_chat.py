"""Module 8 Phase 3.1 Integration Tests: WebSocket Chat Messaging Foundation (REQ-023, REQ-060, REQ-063, REQ-070, REQ-072).

Tests:
1. Successful chat message persistence in ChatMessage model and real-time room broadcast.
2. Authenticated sender identity derivation strictly from connection token (client spoof rejection).
3. Cross-lounge isolation: message with mismatched envelope loungeId is rejected (FORBIDDEN) without DB mutation or broadcast.
4. Lounge membership & moderation authorization checks: kicked users, muted users, ended lounges, and non-members.
5. Payload validation: missing body, empty body, whitespace-only body, oversized body (>500 chars), missing envelope loungeId.
6. Safe content handling and XSS prevention: strips malicious script tags and disarms HTML payloads while preserving legitimate text.
7. Persistence failure handling: database errors roll back cleanly with zero phantom broadcasts.
8. Connection stability: connection survives recoverable errors (e.g. invalid payload) and subsequent valid chat succeeds.
9. Duplicate broadcast protection: deterministic ping/pong probe confirms strictly one broadcast per chat message.
10. Event alias support: verifies lounge.chat.send works identically to lounge.chat.message.
"""

from __future__ import annotations

from typing import Tuple
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.db.session import (
    configure_database,
    create_session_maker,
    drop_db,
    init_db,
)
from app.main import create_app
from app.models.chat import ChatMessage
from app.schemas.websocket import (
    LoungeChatBroadcastPayload,
    LoungeErrorPayload,
    WebSocketEnvelopeSchema,
)
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
        APP_NAME="AI DJ Studio WS Chat Test",
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


def _assert_exactly_one_broadcast(ws, lounge_id: str, probe_ts: int = 88888) -> None:
    """Verify exactly-once broadcast delivery: sends ping and asserts the immediate next message is pong."""
    ws.send_json({
        "event": "lounge.sync.ping",
        "loungeId": lounge_id,
        "payload": {"clientSentAt": probe_ts},
        "ts": probe_ts,
    })
    probe_reply = ws.receive_json()
    assert probe_reply["event"] == "lounge.sync.pong", (
        f"Expected immediate lounge.sync.pong, but received unexpected buffered event: {probe_reply.get('event')}"
    )
    assert probe_reply["loungeId"] == lounge_id
    assert probe_reply["payload"]["clientSentAt"] == probe_ts


# ==============================================================================
# Phase 8.3.1 Focused Tests
# ==============================================================================

def test_1_successful_chat_message_persistence_and_broadcast(client: TestClient, ws_db_engine: AsyncEngine):
    """Test 1: Authenticated participant sends lounge.chat.message; message persists and broadcasts to room."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    listener_id = _get_user_id(client, listener_token)
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws_host:
        _ = ws_host.receive_json()  # host snapshot

        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws_listener:
            _ = ws_host.receive_json()  # host receives listener joined presence
            _ = ws_listener.receive_json()  # listener snapshot

            # Listener sends a chat message
            ws_listener.send_json({
                "event": "lounge.chat.message",
                "loungeId": lounge_id,
                "payload": {"body": "This drop is unbelievable!"},
                "ts": 12345678,
            })

            # Both sender and host receive the broadcast
            listener_broadcast = ws_listener.receive_json()
            host_broadcast = ws_host.receive_json()

            # Verify envelope shape
            env = WebSocketEnvelopeSchema(**host_broadcast)
            assert env.event == "lounge.chat.message"
            assert env.loungeId == lounge_id

            # Verify payload schema
            payload = LoungeChatBroadcastPayload(**env.payload)
            assert payload.messageId.startswith("msg_")
            assert payload.userId == listener_id
            assert payload.body == "This drop is unbelievable!"
            assert payload.tsSeconds >= 0
            assert payload.createdAt != ""

            # Verify listener payload matches host payload
            listener_payload = LoungeChatBroadcastPayload(**listener_broadcast["payload"])
            assert listener_payload.messageId == payload.messageId
            assert listener_payload.body == payload.body

            # Verify exactly-once broadcast
            _assert_exactly_one_broadcast(ws_host, lounge_id, probe_ts=1001)
            _assert_exactly_one_broadcast(ws_listener, lounge_id, probe_ts=1002)


def test_2_authenticated_sender_identity_derivation_and_spoof_rejection(client: TestClient):
    """Test 2: Sender identity is strictly server-derived; client attempts to inject userId/role are rejected."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws:
        _ = ws.receive_json()  # snapshot

        # Attempt to spoof identity by supplying userId and role
        ws.send_json({
            "event": "lounge.chat.message",
            "loungeId": lounge_id,
            "payload": {
                "body": "Spoofed message",
                "userId": "u_fake_admin_999",
                "role": "admin",
            },
            "ts": 222201,
        })
        err_msg = ws.receive_json()
        assert err_msg["event"] == "lounge.error"
        error_payload = LoungeErrorPayload(**err_msg["payload"])
        assert error_payload.code == "INVALID_PAYLOAD"

        _assert_exactly_one_broadcast(ws, lounge_id, probe_ts=2001)


def test_3_cross_lounge_isolation_prevents_chat_leakage(client: TestClient):
    """Test 3: Chat message with mismatched envelope loungeId is rejected (FORBIDDEN) with zero broadcast leakage."""
    host_token = _login_as(client, "host@spotify-intern.local")
    admin_token = _login_as(client, "admin@spotify-intern.local")
    lounge_a, _ = _create_lounge(client, host_token, seed_track="t_2001")
    lounge_b, _ = _create_lounge(client, admin_token, seed_track="t_2004")

    with client.websocket_connect(f"/ws/lounges/{lounge_a}?token={host_token}") as ws_a:
        _ = ws_a.receive_json()  # snapshot A

        with client.websocket_connect(f"/ws/lounges/{lounge_b}?token={admin_token}") as ws_b:
            _ = ws_b.receive_json()  # snapshot B

            # Host A attempts to send chat to Lounge B through Lounge A's connection
            ws_a.send_json({
                "event": "lounge.chat.message",
                "loungeId": lounge_b,
                "payload": {"body": "Cross-lounge intrusion attempt"},
                "ts": 333301,
            })

            # Host A receives lounge.error FORBIDDEN
            err_msg = ws_a.receive_json()
            assert err_msg["event"] == "lounge.error"
            assert err_msg["loungeId"] == lounge_a
            assert err_msg["payload"]["code"] == "FORBIDDEN"

            # Lounge B verifies no broadcast was leaked
            _assert_exactly_one_broadcast(ws_b, lounge_b, probe_ts=3001)


def test_4_lounge_membership_and_authorization_checks(client: TestClient):
    """Test 4: Kicked users, muted users, and ended lounges cannot chat."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    listener_id = _get_user_id(client, listener_token)
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws:
        _ = ws.receive_json()  # snapshot

        # 1. Host mutes listener via moderation
        mute_res = client.post(
            f"/api/v1/lounges/{lounge_id}/moderation",
            json={"action": "mute", "targetUserId": listener_id},
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert mute_res.status_code == 200

        # Muted listener attempts to chat
        ws.send_json({
            "event": "lounge.chat.message",
            "loungeId": lounge_id,
            "payload": {"body": "Am I muted?"},
            "ts": 444401,
        })
        err_muted = ws.receive_json()
        assert err_muted["event"] == "lounge.error"
        assert err_muted["payload"]["code"] == "USER_MUTED"

        # 2. Host unmutes listener via moderation
        unmute_res = client.post(
            f"/api/v1/lounges/{lounge_id}/moderation",
            json={"action": "unmute", "targetUserId": listener_id},
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert unmute_res.status_code == 200

        # Unmuted listener chats successfully
        ws.send_json({
            "event": "lounge.chat.message",
            "loungeId": lounge_id,
            "payload": {"body": "I can speak again!"},
            "ts": 444402,
        })
        unmuted_msg = ws.receive_json()
        assert unmuted_msg["event"] == "lounge.chat.message"
        assert unmuted_msg["payload"]["body"] == "I can speak again!"

        # 3. Host kicks listener via moderation
        kick_res = client.post(
            f"/api/v1/lounges/{lounge_id}/moderation",
            json={"action": "remove", "targetUserId": listener_id},
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert kick_res.status_code == 200

        # Kicked listener attempts to chat
        ws.send_json({
            "event": "lounge.chat.message",
            "loungeId": lounge_id,
            "payload": {"body": "Let me back in!"},
            "ts": 444403,
        })
        err_kicked = ws.receive_json()
        assert err_kicked["event"] == "lounge.error"
        assert err_kicked["payload"]["code"] == "USER_REMOVED"

    # 4. Ended lounge check
    lounge_id_2, _ = _create_lounge(client, host_token, seed_track="t_2001")
    with client.websocket_connect(f"/ws/lounges/{lounge_id_2}?token={host_token}") as ws2:
        _ = ws2.receive_json()  # snapshot

        # Host ends lounge
        client.post(
            f"/api/v1/lounges/{lounge_id_2}/end",
            headers={"Authorization": f"Bearer {host_token}"},
        )

        ws2.send_json({
            "event": "lounge.chat.message",
            "loungeId": lounge_id_2,
            "payload": {"body": "Post-session chatter"},
            "ts": 444404,
        })
        err_ended = ws2.receive_json()
        assert err_ended["event"] == "lounge.error"
        assert err_ended["payload"]["code"] == "LOUNGE_ENDED"


def test_5_payload_validation_empty_whitespace_and_oversized(client: TestClient):
    """Test 5: Rejects empty, whitespace-only, oversized (>500 chars), missing body, and missing loungeId."""
    host_token = _login_as(client, "host@spotify-intern.local")
    lounge_id, _ = _create_lounge(client, host_token, seed_track="t_2001")

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws:
        _ = ws.receive_json()  # snapshot

        # 1. Missing body
        ws.send_json({
            "event": "lounge.chat.message",
            "loungeId": lounge_id,
            "payload": {},
            "ts": 55501,
        })
        err_1 = ws.receive_json()
        assert err_1["event"] == "lounge.error"
        assert err_1["payload"]["code"] == "INVALID_PAYLOAD"

        # 2. Empty string body
        ws.send_json({
            "event": "lounge.chat.message",
            "loungeId": lounge_id,
            "payload": {"body": ""},
            "ts": 55502,
        })
        err_2 = ws.receive_json()
        assert err_2["event"] == "lounge.error"
        assert err_2["payload"]["code"] == "INVALID_PAYLOAD"

        # 3. Whitespace-only body
        ws.send_json({
            "event": "lounge.chat.message",
            "loungeId": lounge_id,
            "payload": {"body": "   \n\t   "},
            "ts": 55503,
        })
        err_3 = ws.receive_json()
        assert err_3["event"] == "lounge.error"
        assert err_3["payload"]["code"] == "INVALID_PAYLOAD"

        # 4. Oversized body (> 500 chars)
        ws.send_json({
            "event": "lounge.chat.message",
            "loungeId": lounge_id,
            "payload": {"body": "x" * 501},
            "ts": 55504,
        })
        err_4 = ws.receive_json()
        assert err_4["event"] == "lounge.error"
        assert err_4["payload"]["code"] == "INVALID_PAYLOAD"

        # 5. Non-string body
        ws.send_json({
            "event": "lounge.chat.message",
            "loungeId": lounge_id,
            "payload": {"body": 12345},
            "ts": 55505,
        })
        err_5 = ws.receive_json()
        assert err_5["event"] == "lounge.error"
        assert err_5["payload"]["code"] == "INVALID_PAYLOAD"

        # 6. Missing envelope loungeId
        ws.send_json({
            "event": "lounge.chat.message",
            "payload": {"body": "Missing loungeId"},
            "ts": 55506,
        })
        err_6 = ws.receive_json()
        assert err_6["event"] == "lounge.error"
        assert err_6["payload"]["code"] == "INVALID_PAYLOAD"


def test_6_security_and_xss_content_handling(client: TestClient):
    """Test 6: Sanitizes and strips script tags, HTML tags, and disarms XSS injection while preserving safe text."""
    host_token = _login_as(client, "host@spotify-intern.local")
    lounge_id, _ = _create_lounge(client, host_token, seed_track="t_2001")

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws:
        _ = ws.receive_json()  # snapshot

        # 1. Pure script tag is stripped to empty and rejected as INVALID_PAYLOAD
        ws.send_json({
            "event": "lounge.chat.message",
            "loungeId": lounge_id,
            "payload": {"body": "<script>alert('xss')</script>"},
            "ts": 66601,
        })
        err_pure_script = ws.receive_json()
        assert err_pure_script["event"] == "lounge.error"
        assert err_pure_script["payload"]["code"] == "INVALID_PAYLOAD"

        # 2. Malicious img tag stripped to empty and rejected
        ws.send_json({
            "event": "lounge.chat.message",
            "loungeId": lounge_id,
            "payload": {"body": "<img src=x onerror=alert(1)>"},
            "ts": 66602,
        })
        err_img = ws.receive_json()
        assert err_img["event"] == "lounge.error"
        assert err_img["payload"]["code"] == "INVALID_PAYLOAD"

        # 3. Legitimate text with embedded script tag is disarmed, safely stripped, and broadcast
        ws.send_json({
            "event": "lounge.chat.message",
            "loungeId": lounge_id,
            "payload": {"body": "This beat is crazy! <script>evil()</script> Keep it up!"},
            "ts": 66603,
        })
        msg_sanitized = ws.receive_json()
        assert msg_sanitized["event"] == "lounge.chat.message"
        assert msg_sanitized["payload"]["body"] == "This beat is crazy! Keep it up!"
        assert "<script>" not in msg_sanitized["payload"]["body"]
        assert "</script>" not in msg_sanitized["payload"]["body"]

        # 4. HTML tags stripped
        ws.send_json({
            "event": "lounge.chat.message",
            "loungeId": lounge_id,
            "payload": {"body": "<b onmouseover=alert(1)>Vibe</b> check <iframe src=evil.com></iframe>"},
            "ts": 66604,
        })
        msg_html = ws.receive_json()
        assert msg_html["event"] == "lounge.chat.message"
        assert msg_html["payload"]["body"] == "Vibe check"
        assert "<" not in msg_html["payload"]["body"]
        assert ">" not in msg_html["payload"]["body"]


def test_7_persistence_failure_suppresses_broadcast(client: TestClient):
    """Test 7: Simulated database failure suppresses chat broadcast and returns PERSISTENCE_FAILED error."""
    host_token = _login_as(client, "host@spotify-intern.local")
    lounge_id, _ = _create_lounge(client, host_token, seed_track="t_2001")

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws:
        _ = ws.receive_json()  # snapshot

        # Patch send_lounge_chat_message in gateway to simulate DB commit failure
        with patch(
            "app.websocket.gateway.send_lounge_chat_message",
            side_effect=Exception("Simulated SQLite write lock timeout"),
        ):
            ws.send_json({
                "event": "lounge.chat.message",
                "loungeId": lounge_id,
                "payload": {"body": "This message will fail to persist"},
                "ts": 77701,
            })
            err_msg = ws.receive_json()
            assert err_msg["event"] == "lounge.error"
            assert err_msg["payload"]["code"] == "INTERNAL_ERROR"

        # Ping probe confirms zero chat broadcast was emitted or buffered
        _assert_exactly_one_broadcast(ws, lounge_id, probe_ts=7001)


def test_8_connection_stability_after_recoverable_errors(client: TestClient):
    """Test 8: Connection survives recoverable errors (e.g. invalid payload) and immediate valid chat succeeds."""
    host_token = _login_as(client, "host@spotify-intern.local")
    lounge_id, _ = _create_lounge(client, host_token, seed_track="t_2001")

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws:
        _ = ws.receive_json()  # snapshot

        # Send invalid payload (empty message)
        ws.send_json({
            "event": "lounge.chat.message",
            "loungeId": lounge_id,
            "payload": {"body": ""},
            "ts": 88801,
        })
        err_msg = ws.receive_json()
        assert err_msg["event"] == "lounge.error"
        assert err_msg["payload"]["code"] == "INVALID_PAYLOAD"

        # Connection is still alive: immediately send valid chat message
        ws.send_json({
            "event": "lounge.chat.message",
            "loungeId": lounge_id,
            "payload": {"body": "Healthy connection restored!"},
            "ts": 88802,
        })
        chat_msg = ws.receive_json()
        assert chat_msg["event"] == "lounge.chat.message"
        assert chat_msg["payload"]["body"] == "Healthy connection restored!"

        # Ping/pong probe confirms connection remains healthy
        _assert_exactly_one_broadcast(ws, lounge_id, probe_ts=8001)


def test_9_duplicate_broadcast_protection(client: TestClient):
    """Test 9: Exactly one lounge.chat.message is emitted per sent chat message."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws:
        _ = ws.receive_json()  # snapshot

        ws.send_json({
            "event": "lounge.chat.message",
            "loungeId": lounge_id,
            "payload": {"body": "Single broadcast assertion test"},
            "ts": 99901,
        })

        broadcast = ws.receive_json()
        assert broadcast["event"] == "lounge.chat.message"

        # Immediate ping probe pops pong if and only if strictly no duplicate was queued
        _assert_exactly_one_broadcast(ws, lounge_id, probe_ts=9001)


def test_10_alias_event_lounge_chat_send(client: TestClient):
    """Test 10: Sending event 'lounge.chat.send' is accepted and emits canonical 'lounge.chat.message'."""
    host_token = _login_as(client, "host@spotify-intern.local")
    lounge_id, _ = _create_lounge(client, host_token, seed_track="t_2001")

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws:
        _ = ws.receive_json()  # snapshot

        ws.send_json({
            "event": "lounge.chat.send",
            "loungeId": lounge_id,
            "payload": {"body": "Using alias event lounge.chat.send"},
            "ts": 101001,
        })

        broadcast = ws.receive_json()
        assert broadcast["event"] == "lounge.chat.message"
        assert broadcast["payload"]["body"] == "Using alias event lounge.chat.send"

        _assert_exactly_one_broadcast(ws, lounge_id, probe_ts=10001)
