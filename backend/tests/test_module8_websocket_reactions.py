"""Module 8 Phase 3.3 Integration Tests: Real-Time Emoji Reactions (PRD Section 5, REQ-060, REQ-070, REQ-076).

Tests:
1. Reaction-add broadcast (canonical envelope and typed payload).
2. Reaction-remove broadcast (removes active reaction).
3. Reaction-toggle behavior (alternates add/remove seamlessly).
4. Server-derived identity and client spoof rejection (extra fields forbidden).
5. Cross-lounge isolation (mismatched envelope loungeId returns FORBIDDEN).
6. Target message validation (non-existent message and cross-lounge message rejected).
7. Lounge membership and authorization (muted user, kicked user, and ended lounge).
8. Payload schema validation (malformed emoji, HTML/XSS, empty fields, bad action).
9. Zero database persistence (reactions create strictly zero ChatMessage or QueueEvent records).
10. Recoverable error handling, deduplication, and event alias support (lounge.chat.react).
"""

from __future__ import annotations

import asyncio
from typing import Tuple

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
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
from app.models.queue import QueueEvent
from app.schemas.websocket import (
    LoungeChatReactionBroadcastPayload,
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
        APP_NAME="AI DJ Studio WS Reaction Test",
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
# TESTS
# ==============================================================================


def test_1_reaction_add_broadcast(client: TestClient):
    """Test 1: Listener reacts to a chat message with an emoji; room receives broadcast."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    listener_id = _get_user_id(client, listener_token)
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws_host:
        _ = ws_host.receive_json()  # host snapshot

        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws_listener:
            _ = ws_listener.receive_json()  # listener snapshot
            _ = ws_host.receive_json()      # presence update for listener join

            # Host posts a message
            ws_host.send_json({
                "event": "lounge.chat.message",
                "loungeId": lounge_id,
                "payload": {"body": "Welcome to the sunset lounge!"},
                "ts": 100001,
            })
            msg_host = ws_host.receive_json()
            msg_listener = ws_listener.receive_json()
            assert msg_host["event"] == "lounge.chat.message"
            message_id = msg_host["payload"]["messageId"]

            # Listener reacts with fire emoji
            ws_listener.send_json({
                "event": "lounge.chat.reaction",
                "loungeId": lounge_id,
                "payload": {
                    "messageId": message_id,
                    "emoji": "🔥",
                    "action": "add",
                },
                "ts": 100002,
            })

            # Both host and listener receive canonical broadcast
            reaction_listener = ws_listener.receive_json()
            reaction_host = ws_host.receive_json()

            for envelope in (reaction_listener, reaction_host):
                # Verify canonical envelope structure
                parsed_env = WebSocketEnvelopeSchema(**envelope)
                assert parsed_env.event == "lounge.chat.reaction"
                assert parsed_env.loungeId == lounge_id
                assert isinstance(parsed_env.ts, int)

                # Verify payload
                parsed_payload = LoungeChatReactionBroadcastPayload(**envelope["payload"])
                assert parsed_payload.messageId == message_id
                assert parsed_payload.userId == listener_id
                assert parsed_payload.displayName is not None
                assert parsed_payload.emoji == "🔥"
                assert parsed_payload.action == "add"

            _assert_exactly_one_broadcast(ws_host, lounge_id, probe_ts=1001)
            _assert_exactly_one_broadcast(ws_listener, lounge_id, probe_ts=1002)


def test_2_reaction_remove_broadcast(client: TestClient):
    """Test 2: Listener removes a previously added reaction; room receives remove broadcast."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    listener_id = _get_user_id(client, listener_token)
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws_host:
        _ = ws_host.receive_json()

        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws_listener:
            _ = ws_listener.receive_json()
            _ = ws_host.receive_json()

            # Host posts a message
            ws_host.send_json({
                "event": "lounge.chat.message",
                "loungeId": lounge_id,
                "payload": {"body": "Rate this track!"},
                "ts": 200001,
            })
            msg = ws_host.receive_json()
            _ = ws_listener.receive_json()
            message_id = msg["payload"]["messageId"]

            # 1. Add reaction ❤️
            ws_listener.send_json({
                "event": "lounge.chat.reaction",
                "loungeId": lounge_id,
                "payload": {
                    "messageId": message_id,
                    "emoji": "❤️",
                    "action": "add",
                },
                "ts": 200002,
            })
            _ = ws_listener.receive_json()
            _ = ws_host.receive_json()

            # 2. Remove reaction ❤️
            ws_listener.send_json({
                "event": "lounge.chat.reaction",
                "loungeId": lounge_id,
                "payload": {
                    "messageId": message_id,
                    "emoji": "❤️",
                    "action": "remove",
                },
                "ts": 200003,
            })
            rx_listener = ws_listener.receive_json()
            rx_host = ws_host.receive_json()

            for envelope in (rx_listener, rx_host):
                parsed_payload = LoungeChatReactionBroadcastPayload(**envelope["payload"])
                assert parsed_payload.messageId == message_id
                assert parsed_payload.userId == listener_id
                assert parsed_payload.emoji == "❤️"
                assert parsed_payload.action == "remove"

            _assert_exactly_one_broadcast(ws_host, lounge_id, probe_ts=2001)


def test_3_reaction_toggle_behavior(client: TestClient):
    """Test 3: Toggle action seamlessly alternates between add and remove."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws_host:
        _ = ws_host.receive_json()

        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws_listener:
            _ = ws_listener.receive_json()
            _ = ws_host.receive_json()

            ws_host.send_json({
                "event": "lounge.chat.message",
                "loungeId": lounge_id,
                "payload": {"body": "Drop some claps!"},
                "ts": 300001,
            })
            msg = ws_host.receive_json()
            _ = ws_listener.receive_json()
            message_id = msg["payload"]["messageId"]

            # Toggle 1 -> adds reaction
            ws_listener.send_json({
                "event": "lounge.chat.reaction",
                "loungeId": lounge_id,
                "payload": {
                    "messageId": message_id,
                    "emoji": "👏",
                    "action": "toggle",
                },
                "ts": 300002,
            })
            rx1 = ws_listener.receive_json()
            _ = ws_host.receive_json()
            assert rx1["payload"]["action"] == "add"

            # Toggle 2 -> removes reaction
            ws_listener.send_json({
                "event": "lounge.chat.reaction",
                "loungeId": lounge_id,
                "payload": {
                    "messageId": message_id,
                    "emoji": "👏",
                    "action": "toggle",
                },
                "ts": 300003,
            })
            rx2 = ws_listener.receive_json()
            _ = ws_host.receive_json()
            assert rx2["payload"]["action"] == "remove"

            # Toggle 3 -> adds reaction again
            ws_listener.send_json({
                "event": "lounge.chat.reaction",
                "loungeId": lounge_id,
                "payload": {
                    "messageId": message_id,
                    "emoji": "👏",
                    "action": "toggle",
                },
                "ts": 300004,
            })
            rx3 = ws_listener.receive_json()
            _ = ws_host.receive_json()
            assert rx3["payload"]["action"] == "add"


def test_4_server_derived_identity_and_spoof_rejection(client: TestClient):
    """Test 4: Server strictly ignores/rejects client-supplied identity fields in payload."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws:
        _ = ws.receive_json()  # snapshot

        # Attempt to spoof userId, displayName, and role
        ws.send_json({
            "event": "lounge.chat.reaction",
            "loungeId": lounge_id,
            "payload": {
                "messageId": "msg_fake123",
                "emoji": "🔥",
                "userId": "u_fake_admin",
                "displayName": "Spoofed Admin",
                "role": "admin",
            },
            "ts": 400001,
        })
        err_msg = ws.receive_json()
        assert err_msg["event"] == "lounge.error"
        assert err_msg["payload"]["code"] == "INVALID_PAYLOAD"
        LoungeErrorPayload(**err_msg["payload"])

        # Socket remains connected and functional
        _assert_exactly_one_broadcast(ws, lounge_id, probe_ts=4001)


def test_5_cross_lounge_isolation(client: TestClient):
    """Test 5: Reaction event with mismatched envelope loungeId is rejected with FORBIDDEN."""
    host_token = _login_as(client, "host@spotify-intern.local")
    admin_token = _login_as(client, "admin@spotify-intern.local")
    lounge_a, _ = _create_lounge(client, host_token, seed_track="t_2001")
    lounge_b, _ = _create_lounge(client, admin_token, seed_track="t_2004")

    with client.websocket_connect(f"/ws/lounges/{lounge_a}?token={host_token}") as ws_a:
        _ = ws_a.receive_json()  # snapshot A

        with client.websocket_connect(f"/ws/lounges/{lounge_b}?token={admin_token}") as ws_b:
            _ = ws_b.receive_json()  # snapshot B

            # Host A attempts to send reaction to Lounge B
            ws_a.send_json({
                "event": "lounge.chat.reaction",
                "loungeId": lounge_b,
                "payload": {"messageId": "msg_test", "emoji": "🔥"},
                "ts": 500001,
            })

            err_msg = ws_a.receive_json()
            assert err_msg["event"] == "lounge.error"
            assert err_msg["loungeId"] == lounge_a
            assert err_msg["payload"]["code"] == "FORBIDDEN"

            # Lounge B received no cross-room traffic
            _assert_exactly_one_broadcast(ws_b, lounge_b, probe_ts=5001)


def test_6_target_message_validation(client: TestClient):
    """Test 6: Rejects non-existent messages and messages belonging to other lounges."""
    host_token = _login_as(client, "host@spotify-intern.local")
    admin_token = _login_as(client, "admin@spotify-intern.local")
    lounge_a, _ = _create_lounge(client, host_token, seed_track="t_2001")
    lounge_b, _ = _create_lounge(client, admin_token, seed_track="t_2004")

    with client.websocket_connect(f"/ws/lounges/{lounge_a}?token={host_token}") as ws_a:
        _ = ws_a.receive_json()

        # 6a: Non-existent message
        ws_a.send_json({
            "event": "lounge.chat.reaction",
            "loungeId": lounge_a,
            "payload": {"messageId": "msg_nonexistent_999", "emoji": "🔥"},
            "ts": 600001,
        })
        err_nonexistent = ws_a.receive_json()
        assert err_nonexistent["event"] == "lounge.error"
        assert err_nonexistent["payload"]["code"] == "MESSAGE_NOT_FOUND"

        # Host sends a valid message in Lounge A
        ws_a.send_json({
            "event": "lounge.chat.message",
            "loungeId": lounge_a,
            "payload": {"body": "Message in Lounge A"},
            "ts": 600002,
        })
        msg_a = ws_a.receive_json()
        message_id_a = msg_a["payload"]["messageId"]

        # 6b: Client connects to Lounge B and tries to react to Lounge A's message
        with client.websocket_connect(f"/ws/lounges/{lounge_b}?token={admin_token}") as ws_b:
            _ = ws_b.receive_json()

            ws_b.send_json({
                "event": "lounge.chat.reaction",
                "loungeId": lounge_b,
                "payload": {"messageId": message_id_a, "emoji": "🔥"},
                "ts": 600003,
            })
            err_wrong_lounge = ws_b.receive_json()
            assert err_wrong_lounge["event"] == "lounge.error"
            assert err_wrong_lounge["payload"]["code"] == "MESSAGE_NOT_FOUND"


def test_7_membership_and_authorization_checks(client: TestClient):
    """Test 7: Kicked users, muted users, and ended lounges cannot react to chat messages."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    listener_id = _get_user_id(client, listener_token)
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws_host:
        _ = ws_host.receive_json()

        # Host posts a message
        ws_host.send_json({
            "event": "lounge.chat.message",
            "loungeId": lounge_id,
            "payload": {"body": "Host announcement"},
            "ts": 700001,
        })
        msg = ws_host.receive_json()
        message_id = msg["payload"]["messageId"]

        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws_listener:
            _ = ws_listener.receive_json()
            _ = ws_host.receive_json()

            # 1. Host mutes listener
            mute_res = client.post(
                f"/api/v1/lounges/{lounge_id}/moderation",
                json={"action": "mute", "targetUserId": listener_id},
                headers={"Authorization": f"Bearer {host_token}"},
            )
            assert mute_res.status_code == 200

            # Muted listener attempts reaction -> USER_MUTED
            ws_listener.send_json({
                "event": "lounge.chat.reaction",
                "loungeId": lounge_id,
                "payload": {"messageId": message_id, "emoji": "🔥"},
                "ts": 700002,
            })
            err_muted = ws_listener.receive_json()
            assert err_muted["event"] == "lounge.error"
            assert err_muted["payload"]["code"] == "USER_MUTED"

            # Host unmutes listener
            unmute_res = client.post(
                f"/api/v1/lounges/{lounge_id}/moderation",
                json={"action": "unmute", "targetUserId": listener_id},
                headers={"Authorization": f"Bearer {host_token}"},
            )
            assert unmute_res.status_code == 200

            # Listener can now react
            ws_listener.send_json({
                "event": "lounge.chat.reaction",
                "loungeId": lounge_id,
                "payload": {"messageId": message_id, "emoji": "🔥"},
                "ts": 700003,
            })
            rx_ok = ws_listener.receive_json()
            _ = ws_host.receive_json()
            assert rx_ok["event"] == "lounge.chat.reaction"

            # 2. Host kicks listener
            kick_res = client.post(
                f"/api/v1/lounges/{lounge_id}/moderation",
                json={"action": "remove", "targetUserId": listener_id},
                headers={"Authorization": f"Bearer {host_token}"},
            )
            assert kick_res.status_code == 200

            # Kicked listener attempts reaction -> USER_REMOVED
            ws_listener.send_json({
                "event": "lounge.chat.reaction",
                "loungeId": lounge_id,
                "payload": {"messageId": message_id, "emoji": "🔥"},
                "ts": 700004,
            })
            err_kicked = ws_listener.receive_json()
            assert err_kicked["event"] == "lounge.error"
            assert err_kicked["payload"]["code"] == "USER_REMOVED"

    # 3. Ended lounge
    lounge_id_2, _ = _create_lounge(client, host_token, seed_track="t_2001")
    with client.websocket_connect(f"/ws/lounges/{lounge_id_2}?token={host_token}") as ws2:
        _ = ws2.receive_json()  # snapshot

        ws2.send_json({
            "event": "lounge.chat.message",
            "loungeId": lounge_id_2,
            "payload": {"body": "Pre-end message"},
            "ts": 700005,
        })
        msg_end = ws2.receive_json()
        end_msg_id = msg_end["payload"]["messageId"]

        client.post(
            f"/api/v1/lounges/{lounge_id_2}/end",
            headers={"Authorization": f"Bearer {host_token}"},
        )

        ws2.send_json({
            "event": "lounge.chat.reaction",
            "loungeId": lounge_id_2,
            "payload": {"messageId": end_msg_id, "emoji": "🔥"},
            "ts": 700006,
        })
        err_ended = ws2.receive_json()
        assert err_ended["event"] == "lounge.error"
        assert err_ended["payload"]["code"] == "LOUNGE_ENDED"


def test_8_payload_schema_validation(client: TestClient):
    """Test 8: Rejects malformed emojis, empty messageId, missing loungeId, and XSS attempts."""
    host_token = _login_as(client, "host@spotify-intern.local")
    lounge_id, _ = _create_lounge(client, host_token, seed_track="t_2001")

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws:
        _ = ws.receive_json()

        # 8a: Missing envelope loungeId
        ws.send_json({
            "event": "lounge.chat.reaction",
            "payload": {"messageId": "msg_1", "emoji": "🔥"},
            "ts": 800001,
        })
        err_no_lid = ws.receive_json()
        assert err_no_lid["event"] == "lounge.error"
        assert err_no_lid["payload"]["code"] == "INVALID_PAYLOAD"

        # 8b: Empty messageId
        ws.send_json({
            "event": "lounge.chat.reaction",
            "loungeId": lounge_id,
            "payload": {"messageId": "", "emoji": "🔥"},
            "ts": 800002,
        })
        err_empty_msg = ws.receive_json()
        assert err_empty_msg["event"] == "lounge.error"
        assert err_empty_msg["payload"]["code"] == "INVALID_PAYLOAD"

        # 8c: Missing messageId
        ws.send_json({
            "event": "lounge.chat.reaction",
            "loungeId": lounge_id,
            "payload": {"emoji": "🔥"},
            "ts": 800003,
        })
        err_no_msg = ws.receive_json()
        assert err_no_msg["event"] == "lounge.error"
        assert err_no_msg["payload"]["code"] == "INVALID_PAYLOAD"

        # 8d: Empty emoji
        ws.send_json({
            "event": "lounge.chat.reaction",
            "loungeId": lounge_id,
            "payload": {"messageId": "msg_1", "emoji": "   "},
            "ts": 800004,
        })
        err_empty_emoji = ws.receive_json()
        assert err_empty_emoji["event"] == "lounge.error"
        assert err_empty_emoji["payload"]["code"] == "INVALID_PAYLOAD"

        # 8e: Non-emoji alphanumeric word ("like")
        ws.send_json({
            "event": "lounge.chat.reaction",
            "loungeId": lounge_id,
            "payload": {"messageId": "msg_1", "emoji": "like"},
            "ts": 800005,
        })
        err_word = ws.receive_json()
        assert err_word["event"] == "lounge.error"
        assert err_word["payload"]["code"] == "INVALID_PAYLOAD"

        # 8f: XSS injection attempt in emoji
        ws.send_json({
            "event": "lounge.chat.reaction",
            "loungeId": lounge_id,
            "payload": {"messageId": "msg_1", "emoji": "<script>alert(1)</script>"},
            "ts": 800006,
        })
        err_xss = ws.receive_json()
        assert err_xss["event"] == "lounge.error"
        assert err_xss["payload"]["code"] == "INVALID_PAYLOAD"

        # 8g: Oversized emoji sequence (> 16 chars)
        ws.send_json({
            "event": "lounge.chat.reaction",
            "loungeId": lounge_id,
            "payload": {"messageId": "msg_1", "emoji": "🔥" * 20},
            "ts": 800007,
        })
        err_oversized = ws.receive_json()
        assert err_oversized["event"] == "lounge.error"
        assert err_oversized["payload"]["code"] == "INVALID_PAYLOAD"

        # 8h: Invalid action
        ws.send_json({
            "event": "lounge.chat.reaction",
            "loungeId": lounge_id,
            "payload": {"messageId": "msg_1", "emoji": "🔥", "action": "explode"},
            "ts": 800008,
        })
        err_bad_action = ws.receive_json()
        assert err_bad_action["event"] == "lounge.error"
        assert err_bad_action["payload"]["code"] == "INVALID_PAYLOAD"


def test_9_zero_database_persistence(client: TestClient, ws_db_engine: AsyncEngine):
    """Test 9: Reaction events create strictly zero ChatMessage or QueueEvent database records."""
    host_token = _login_as(client, "host@spotify-intern.local")
    lounge_id, _ = _create_lounge(client, host_token, seed_track="t_2001")

    session_maker = create_session_maker(ws_db_engine)

    async def get_row_counts():
        async with session_maker() as session:
            msg_count = (await session.execute(select(func.count(ChatMessage.id)))).scalar_one()
            qev_count = (await session.execute(select(func.count(QueueEvent.id)))).scalar_one()
            return msg_count, qev_count

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws:
        _ = ws.receive_json()  # snapshot

        # Host sends one chat message (this creates 1 ChatMessage row)
        ws.send_json({
            "event": "lounge.chat.message",
            "loungeId": lounge_id,
            "payload": {"body": "Persistence test message"},
            "ts": 900001,
        })
        msg = ws.receive_json()
        message_id = msg["payload"]["messageId"]

        # Capture row counts after message creation
        msg_before, qev_before = asyncio.run(get_row_counts())

        # Perform multiple reaction operations
        ws.send_json({
            "event": "lounge.chat.reaction",
            "loungeId": lounge_id,
            "payload": {"messageId": message_id, "emoji": "🔥", "action": "add"},
            "ts": 900002,
        })
        _ = ws.receive_json()

        ws.send_json({
            "event": "lounge.chat.reaction",
            "loungeId": lounge_id,
            "payload": {"messageId": message_id, "emoji": "❤️", "action": "toggle"},
            "ts": 900003,
        })
        _ = ws.receive_json()

        ws.send_json({
            "event": "lounge.chat.reaction",
            "loungeId": lounge_id,
            "payload": {"messageId": message_id, "emoji": "🔥", "action": "remove"},
            "ts": 900004,
        })
        _ = ws.receive_json()

    # Capture row counts after reaction operations
    msg_after, qev_after = asyncio.run(get_row_counts())
    assert msg_after == msg_before, "ChatMessage count must not change on reaction events"
    assert qev_after == qev_before, "QueueEvent count must not change on reaction events"


def test_10_recoverable_errors_deduplication_and_alias(client: TestClient):
    """Test 10: Connection survives invalid payloads, alias lounge.chat.react works, and duplicates are deduplicated."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws_host:
        _ = ws_host.receive_json()

        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws_listener:
            _ = ws_listener.receive_json()
            _ = ws_host.receive_json()

            ws_host.send_json({
                "event": "lounge.chat.message",
                "loungeId": lounge_id,
                "payload": {"body": "Alias and deduplication test"},
                "ts": 1000001,
            })
            msg = ws_host.receive_json()
            _ = ws_listener.receive_json()
            message_id = msg["payload"]["messageId"]

            # 1. Recoverable error: send invalid reaction
            ws_listener.send_json({
                "event": "lounge.chat.reaction",
                "loungeId": lounge_id,
                "payload": {"messageId": message_id, "emoji": "INVALID_EMOJI_STRING"},
                "ts": 1000002,
            })
            err_rx = ws_listener.receive_json()
            assert err_rx["event"] == "lounge.error"
            assert err_rx["payload"]["code"] == "INVALID_PAYLOAD"

            # 2. Event alias: lounge.chat.react succeeds immediately after error
            ws_listener.send_json({
                "event": "lounge.chat.react",
                "loungeId": lounge_id,
                "payload": {"messageId": message_id, "emoji": "⚡", "action": "add"},
                "ts": 1000003,
            })
            rx_alias_listener = ws_listener.receive_json()
            rx_alias_host = ws_host.receive_json()
            assert rx_alias_listener["event"] == "lounge.chat.reaction"
            assert rx_alias_listener["payload"]["emoji"] == "⚡"
            assert rx_alias_host["event"] == "lounge.chat.reaction"
            assert rx_alias_host["payload"]["emoji"] == "⚡"

            # 3. Deduplication: sending identical add again does NOT emit redundant broadcast
            ws_listener.send_json({
                "event": "lounge.chat.react",
                "loungeId": lounge_id,
                "payload": {"messageId": message_id, "emoji": "⚡", "action": "add"},
                "ts": 1000004,
            })

            # Assert via ping/pong that no duplicate reaction broadcast was queued
            _assert_exactly_one_broadcast(ws_listener, lounge_id, probe_ts=10001)
            _assert_exactly_one_broadcast(ws_host, lounge_id, probe_ts=10002)
