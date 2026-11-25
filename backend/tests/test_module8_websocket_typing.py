"""Module 8 Phase 3.2 Integration Tests: Real-Time Typing Indicators (PRD Section 5, REQ-060, REQ-070, REQ-076).

Tests:
1. Typing-start and typing-stop broadcasts (canonical envelope and typed payload).
2. Server-derived identity and client spoof rejection (extra fields forbidden).
3. Cross-lounge isolation: typing event with mismatched loungeId is rejected (FORBIDDEN).
4. Lounge membership and authorization: kicked, muted, and ended lounge checks.
5. Payload validation: missing fields, malformed types, missing envelope loungeId.
6. Zero database persistence: strictly no ChatMessage or QueueEvent records created.
7. Disconnect cleanup: closing socket while typing automatically broadcasts typing-stop.
8. State-based deduplication and throttling: duplicate typing-start/stop events do not spam broadcasts.
9. Recoverable errors: socket survives invalid payloads and subsequent valid typing event succeeds.
10. Event alias support: verifies lounge.chat.typing_start and lounge.chat.typing_stop events.
"""

from __future__ import annotations

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
    LoungeChatTypingBroadcastPayload,
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
        APP_NAME="AI DJ Studio WS Typing Test",
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
# Phase 8.3.2 Focused Tests
# ==============================================================================

def test_1_typing_start_and_stop_broadcast(client: TestClient):
    """Test 1: Participant sends typing-start and typing-stop; room receives authoritative lounge.chat.typing broadcasts."""
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

            # 1. Listener sends typing-start
            ws_listener.send_json({
                "event": "lounge.chat.typing",
                "loungeId": lounge_id,
                "payload": {"isTyping": True},
                "ts": 12345601,
            })

            # Both host and listener receive broadcast
            listener_start = ws_listener.receive_json()
            host_start = ws_host.receive_json()

            env_start = WebSocketEnvelopeSchema(**host_start)
            assert env_start.event == "lounge.chat.typing"
            assert env_start.loungeId == lounge_id

            payload_start = LoungeChatTypingBroadcastPayload(**env_start.payload)
            assert payload_start.userId == listener_id
            assert payload_start.isTyping is True
            assert payload_start.action == "started"
            assert payload_start.displayName is not None

            assert listener_start["payload"]["isTyping"] is True

            # Exactly one broadcast emitted
            _assert_exactly_one_broadcast(ws_host, lounge_id, probe_ts=1001)
            _assert_exactly_one_broadcast(ws_listener, lounge_id, probe_ts=1002)

            # 2. Listener sends typing-stop
            ws_listener.send_json({
                "event": "lounge.chat.typing",
                "loungeId": lounge_id,
                "payload": {"isTyping": False},
                "ts": 12345602,
            })

            listener_stop = ws_listener.receive_json()
            host_stop = ws_host.receive_json()

            env_stop = WebSocketEnvelopeSchema(**host_stop)
            assert env_stop.event == "lounge.chat.typing"

            payload_stop = LoungeChatTypingBroadcastPayload(**env_stop.payload)
            assert payload_stop.userId == listener_id
            assert payload_stop.isTyping is False
            assert payload_stop.action == "stopped"

            assert listener_stop["payload"]["isTyping"] is False

            _assert_exactly_one_broadcast(ws_host, lounge_id, probe_ts=1003)
            _assert_exactly_one_broadcast(ws_listener, lounge_id, probe_ts=1004)


def test_2_server_derived_identity_and_spoof_rejection(client: TestClient):
    """Test 2: Server ignores/rejects client-supplied identity; spoofing userId/role returns INVALID_PAYLOAD."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws:
        _ = ws.receive_json()  # snapshot

        # Attempt to spoof userId and role
        ws.send_json({
            "event": "lounge.chat.typing",
            "loungeId": lounge_id,
            "payload": {
                "isTyping": True,
                "userId": "u_fake_admin",
                "role": "admin",
            },
            "ts": 222201,
        })
        err_msg = ws.receive_json()
        assert err_msg["event"] == "lounge.error"
        assert err_msg["payload"]["code"] == "INVALID_PAYLOAD"

        _assert_exactly_one_broadcast(ws, lounge_id, probe_ts=2001)


def test_3_cross_lounge_isolation(client: TestClient):
    """Test 3: Typing event with mismatched loungeId is rejected (FORBIDDEN) without leaking to target lounge."""
    host_token = _login_as(client, "host@spotify-intern.local")
    admin_token = _login_as(client, "admin@spotify-intern.local")
    lounge_a, _ = _create_lounge(client, host_token, seed_track="t_2001")
    lounge_b, _ = _create_lounge(client, admin_token, seed_track="t_2004")

    with client.websocket_connect(f"/ws/lounges/{lounge_a}?token={host_token}") as ws_a:
        _ = ws_a.receive_json()  # snapshot A

        with client.websocket_connect(f"/ws/lounges/{lounge_b}?token={admin_token}") as ws_b:
            _ = ws_b.receive_json()  # snapshot B

            # Host A attempts to send typing event to Lounge B
            ws_a.send_json({
                "event": "lounge.chat.typing",
                "loungeId": lounge_b,
                "payload": {"isTyping": True},
                "ts": 333301,
            })

            err_msg = ws_a.receive_json()
            assert err_msg["event"] == "lounge.error"
            assert err_msg["loungeId"] == lounge_a
            assert err_msg["payload"]["code"] == "FORBIDDEN"

            # Lounge B verifies no broadcast leaked
            _assert_exactly_one_broadcast(ws_b, lounge_b, probe_ts=3001)


def test_4_membership_and_authorization_checks(client: TestClient):
    """Test 4: Kicked users, muted users, and ended lounges cannot send typing events."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    listener_id = _get_user_id(client, listener_token)
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws:
        _ = ws.receive_json()  # snapshot

        # 1. Host mutes listener
        mute_res = client.post(
            f"/api/v1/lounges/{lounge_id}/moderation",
            json={"action": "mute", "targetUserId": listener_id},
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert mute_res.status_code == 200

        # Muted listener attempts typing event
        ws.send_json({
            "event": "lounge.chat.typing",
            "loungeId": lounge_id,
            "payload": {"isTyping": True},
            "ts": 444401,
        })
        err_muted = ws.receive_json()
        assert err_muted["event"] == "lounge.error"
        assert err_muted["payload"]["code"] == "USER_MUTED"

        # 2. Host kicks listener
        kick_res = client.post(
            f"/api/v1/lounges/{lounge_id}/moderation",
            json={"action": "remove", "targetUserId": listener_id},
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert kick_res.status_code == 200

        # Kicked listener attempts typing event
        ws.send_json({
            "event": "lounge.chat.typing",
            "loungeId": lounge_id,
            "payload": {"isTyping": True},
            "ts": 444402,
        })
        err_kicked = ws.receive_json()
        assert err_kicked["event"] == "lounge.error"
        assert err_kicked["payload"]["code"] == "USER_REMOVED"

    # 3. Ended lounge
    lounge_id_2, _ = _create_lounge(client, host_token, seed_track="t_2001")
    with client.websocket_connect(f"/ws/lounges/{lounge_id_2}?token={host_token}") as ws2:
        _ = ws2.receive_json()  # snapshot

        client.post(
            f"/api/v1/lounges/{lounge_id_2}/end",
            headers={"Authorization": f"Bearer {host_token}"},
        )

        ws2.send_json({
            "event": "lounge.chat.typing",
            "loungeId": lounge_id_2,
            "payload": {"isTyping": True},
            "ts": 444403,
        })
        err_ended = ws2.receive_json()
        assert err_ended["event"] == "lounge.error"
        assert err_ended["payload"]["code"] == "LOUNGE_ENDED"


def test_5_invalid_payloads(client: TestClient):
    """Test 5: Rejects empty payload, missing loungeId, and malformed types."""
    host_token = _login_as(client, "host@spotify-intern.local")
    lounge_id, _ = _create_lounge(client, host_token, seed_track="t_2001")

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws:
        _ = ws.receive_json()  # snapshot

        # 1. Generic lounge.chat.typing with empty payload
        ws.send_json({
            "event": "lounge.chat.typing",
            "loungeId": lounge_id,
            "payload": {},
            "ts": 55501,
        })
        err_empty = ws.receive_json()
        assert err_empty["event"] == "lounge.error"
        assert err_empty["payload"]["code"] == "INVALID_PAYLOAD"

        # 2. Missing envelope loungeId
        ws.send_json({
            "event": "lounge.chat.typing",
            "payload": {"isTyping": True},
            "ts": 55502,
        })
        err_missing_lid = ws.receive_json()
        assert err_missing_lid["event"] == "lounge.error"
        assert err_missing_lid["payload"]["code"] == "INVALID_PAYLOAD"

        # 3. Non-boolean isTyping
        ws.send_json({
            "event": "lounge.chat.typing",
            "loungeId": lounge_id,
            "payload": {"isTyping": "yes"},
            "ts": 55503,
        })
        err_bad_type = ws.receive_json()
        assert err_bad_type["event"] == "lounge.error"
        assert err_bad_type["payload"]["code"] == "INVALID_PAYLOAD"


def test_6_no_database_persistence(client: TestClient, ws_db_engine: AsyncEngine):
    """Test 6: Typing events create strictly zero ChatMessage or QueueEvent database records."""
    host_token = _login_as(client, "host@spotify-intern.local")
    lounge_id, _ = _create_lounge(client, host_token, seed_track="t_2001")

    session_maker = create_session_maker(ws_db_engine)

    # Count rows before
    async def get_row_counts():
        async with session_maker() as session:
            msg_count = (await session.execute(select(func.count(ChatMessage.id)))).scalar_one()
            qev_count = (await session.execute(select(func.count(QueueEvent.id)))).scalar_one()
            return msg_count, qev_count

    msg_before, qev_before = pytest.importorskip("asyncio").run(get_row_counts())

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws:
        _ = ws.receive_json()  # snapshot

        # Send typing-start
        ws.send_json({
            "event": "lounge.chat.typing",
            "loungeId": lounge_id,
            "payload": {"isTyping": True},
            "ts": 66601,
        })
        _ = ws.receive_json()  # typing-start broadcast

        # Send typing-stop
        ws.send_json({
            "event": "lounge.chat.typing",
            "loungeId": lounge_id,
            "payload": {"isTyping": False},
            "ts": 66602,
        })
        _ = ws.receive_json()  # typing-stop broadcast

    msg_after, qev_after = pytest.importorskip("asyncio").run(get_row_counts())
    assert msg_after == msg_before, "ChatMessage count must not change on typing events"
    assert qev_after == qev_before, "QueueEvent count must not change on typing events"


def test_7_disconnect_cleanup(client: TestClient):
    """Test 7: Closing socket while typing automatically broadcasts typing-stop to remaining participants."""
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

            # Listener starts typing
            ws_listener.send_json({
                "event": "lounge.chat.typing",
                "loungeId": lounge_id,
                "payload": {"isTyping": True},
                "ts": 77701,
            })
            _ = ws_listener.receive_json()
            host_typing = ws_host.receive_json()
            assert host_typing["event"] == "lounge.chat.typing"
            assert host_typing["payload"]["isTyping"] is True
            assert host_typing["payload"]["userId"] == listener_id

            # Listener disconnects without explicitly sending typing-stop
        # Listener socket block exits here (disconnecting)

        # Host receives automatic typing-stop cleanup broadcast
        cleanup_broadcast = ws_host.receive_json()
        assert cleanup_broadcast["event"] == "lounge.chat.typing"
        assert cleanup_broadcast["payload"]["userId"] == listener_id
        assert cleanup_broadcast["payload"]["isTyping"] is False
        assert cleanup_broadcast["payload"]["action"] == "stopped"

        # Verify listener is no longer marked as typing in connection manager
        assert not connection_manager.is_user_typing(lounge_id, listener_id)


def test_8_duplicate_and_throttle_behavior(client: TestClient):
    """Test 8: Consecutive identical typing-start or typing-stop events are throttled and do not spam broadcasts."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws_host:
        _ = ws_host.receive_json()  # host snapshot

        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws_listener:
            _ = ws_host.receive_json()  # joined
            _ = ws_listener.receive_json()  # snapshot

            # 1. First typing-start: broadcasts
            ws_listener.send_json({
                "event": "lounge.chat.typing",
                "loungeId": lounge_id,
                "payload": {"isTyping": True},
                "ts": 88801,
            })
            _ = ws_listener.receive_json()
            _ = ws_host.receive_json()

            # 2. Second typing-start (repeated keystroke): throttled/deduplicated!
            ws_listener.send_json({
                "event": "lounge.chat.typing",
                "loungeId": lounge_id,
                "payload": {"isTyping": True},
                "ts": 88802,
            })

            # Verify no second typing broadcast was buffered
            _assert_exactly_one_broadcast(ws_host, lounge_id, probe_ts=8001)
            _assert_exactly_one_broadcast(ws_listener, lounge_id, probe_ts=8002)

            # 3. First typing-stop: broadcasts
            ws_listener.send_json({
                "event": "lounge.chat.typing",
                "loungeId": lounge_id,
                "payload": {"isTyping": False},
                "ts": 88803,
            })
            _ = ws_listener.receive_json()
            _ = ws_host.receive_json()

            # 4. Second typing-stop: throttled/deduplicated!
            ws_listener.send_json({
                "event": "lounge.chat.typing",
                "loungeId": lounge_id,
                "payload": {"isTyping": False},
                "ts": 88804,
            })

            _assert_exactly_one_broadcast(ws_host, lounge_id, probe_ts=8003)
            _assert_exactly_one_broadcast(ws_listener, lounge_id, probe_ts=8004)


def test_9_recoverable_errors(client: TestClient):
    """Test 9: Invalid payload returns lounge.error without dropping connection; subsequent typing event succeeds."""
    host_token = _login_as(client, "host@spotify-intern.local")
    lounge_id, _ = _create_lounge(client, host_token, seed_track="t_2001")

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws:
        _ = ws.receive_json()  # snapshot

        # Send invalid payload
        ws.send_json({
            "event": "lounge.chat.typing",
            "loungeId": lounge_id,
            "payload": {"isTyping": "not_a_bool"},
            "ts": 99901,
        })
        err_msg = ws.receive_json()
        assert err_msg["event"] == "lounge.error"
        assert err_msg["payload"]["code"] == "INVALID_PAYLOAD"

        # Connection is healthy: immediately send valid typing event
        ws.send_json({
            "event": "lounge.chat.typing",
            "loungeId": lounge_id,
            "payload": {"isTyping": True},
            "ts": 99902,
        })
        typing_msg = ws.receive_json()
        assert typing_msg["event"] == "lounge.chat.typing"
        assert typing_msg["payload"]["isTyping"] is True

        _assert_exactly_one_broadcast(ws, lounge_id, probe_ts=9001)


def test_10_alias_events_typing_start_and_stop(client: TestClient):
    """Test 10: Supports typing_start and typing_stop event names with empty or action payloads."""
    host_token = _login_as(client, "host@spotify-intern.local")
    lounge_id, _ = _create_lounge(client, host_token, seed_track="t_2001")

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws:
        _ = ws.receive_json()  # snapshot

        # 1. Event: lounge.chat.typing_start
        ws.send_json({
            "event": "lounge.chat.typing_start",
            "loungeId": lounge_id,
            "payload": {},
            "ts": 101001,
        })
        start_msg = ws.receive_json()
        assert start_msg["event"] == "lounge.chat.typing"
        assert start_msg["payload"]["isTyping"] is True
        assert start_msg["payload"]["action"] == "started"

        # 2. Event: lounge.chat.typing_stop
        ws.send_json({
            "event": "lounge.chat.typing_stop",
            "loungeId": lounge_id,
            "payload": {},
            "ts": 101002,
        })
        stop_msg = ws.receive_json()
        assert stop_msg["event"] == "lounge.chat.typing"
        assert stop_msg["payload"]["isTyping"] is False
        assert stop_msg["payload"]["action"] == "stopped"

        _assert_exactly_one_broadcast(ws, lounge_id, probe_ts=10001)
