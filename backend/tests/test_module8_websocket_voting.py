"""Module 8 Phase 2B Integration Tests: Real-Time WebSocket Voting.

Tests:
1. Successful WebSocket vote updates queue vote tally and sparse ordering.
2. Broadcast behavior: exactly one authoritative lounge.queue.update broadcast with reason='vote'.
3. Cross-lounge isolation: voting with mismatched envelope loungeId is rejected (FORBIDDEN).
4. Authorization & moderation: client-supplied extra fields rejected, kicked users rejected.
5. Payload validation: missing trackId, malformed trackId, missing loungeId, unsupported events.
6. Domain errors: track not in queue, ended lounge, persistence failure (zero phantom broadcasts).
7. Connection stability: connection remains usable after recoverable error, follow-up vote succeeds.
8. Duplicate broadcast protection: ping probe verifies strictly one update is emitted.
"""

from __future__ import annotations

from typing import Tuple
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.core.exceptions import AppException
from app.db.session import (
    configure_database,
    create_session_maker,
    drop_db,
    init_db,
)
from app.main import create_app
from app.schemas.websocket import (
    LoungeErrorPayload,
    LoungeQueueUpdatePayload,
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
        APP_NAME="AI DJ Studio WS Voting Test",
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


def _add_track(client: TestClient, lounge_id: str, token: str, track_id: str) -> None:
    res = client.post(
        f"/api/v1/lounges/{lounge_id}/queue",
        json={"trackId": track_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201, res.text


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
# Phase 8.2B Focused Tests
# ==============================================================================

def test_1_successful_websocket_vote_updates_queue_and_broadcasts(client: TestClient):
    """Test 1: Authenticated participant sends lounge.queue.vote; queue updates and broadcasts to room."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)
    _add_track(client, lounge_id, host_token, "t_2002")

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws_host:
        _ = ws_host.receive_json()  # host snapshot

        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws_listener:
            _ = ws_host.receive_json()  # host receives listener joined presence
            _ = ws_listener.receive_json()  # listener snapshot

            # Listener casts an upvote via WebSocket
            ws_listener.send_json({
                "event": "lounge.queue.vote",
                "loungeId": lounge_id,
                "payload": {"trackId": "t_2002"},
                "ts": 12345678,
            })

            # Both listener and host receive authoritative lounge.queue.update broadcast
            listener_update = ws_listener.receive_json()
            host_update = ws_host.receive_json()

            # Verify envelope and payload schema
            env_host = WebSocketEnvelopeSchema(**host_update)
            assert env_host.event == "lounge.queue.update"
            assert env_host.loungeId == lounge_id

            payload_host = LoungeQueueUpdatePayload(**env_host.payload)
            assert payload_host.reason == "vote"
            assert payload_host.version == 3
            assert len(payload_host.queue) == 1
            assert payload_host.queue[0].trackId == "t_2002"
            assert payload_host.queue[0].voteCount == 1

            env_listener = WebSocketEnvelopeSchema(**listener_update)
            assert env_listener.event == "lounge.queue.update"
            payload_listener = LoungeQueueUpdatePayload(**env_listener.payload)
            assert payload_listener.version == 3
            assert payload_listener.queue[0].voteCount == 1

            # Verify exactly-once broadcast delivery
            _assert_exactly_one_broadcast(ws_host, lounge_id, probe_ts=1001)
            _assert_exactly_one_broadcast(ws_listener, lounge_id, probe_ts=1002)


def test_2_multi_vote_reordering_broadcast(client: TestClient):
    """Test 2: WebSocket vote causes deterministic queue re-ranking and broadcasts new order."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)
    _add_track(client, lounge_id, host_token, "t_2002")
    _add_track(client, lounge_id, host_token, "t_2003")

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws:
        _ = ws.receive_json()  # snapshot

        # Listener votes for the second track t_2003 (currently trailing t_2002)
        ws.send_json({
            "event": "lounge.queue.vote",
            "loungeId": lounge_id,
            "payload": {"trackId": "t_2003"},
            "ts": 222222,
        })

        update = ws.receive_json()
        payload = LoungeQueueUpdatePayload(**update["payload"])
        assert payload.reason == "vote"
        assert len(payload.queue) == 2
        # t_2003 must now lead head of queue with 1 vote
        assert payload.queue[0].trackId == "t_2003"
        assert payload.queue[0].voteCount == 1
        assert payload.queue[1].trackId == "t_2002"
        assert payload.queue[1].voteCount == 0

        _assert_exactly_one_broadcast(ws, lounge_id, probe_ts=2001)


def test_3_cross_lounge_isolation_prevents_voting(client: TestClient):
    """Test 3: Voting with a mismatched envelope loungeId is rejected (FORBIDDEN) without DB mutation."""
    host_token = _login_as(client, "host@spotify-intern.local")
    admin_token = _login_as(client, "admin@spotify-intern.local")
    lounge_a, _ = _create_lounge(client, host_token, seed_track="t_2001")
    lounge_b, _ = _create_lounge(client, admin_token, seed_track="t_2004")
    _add_track(client, lounge_b, admin_token, "t_2002")

    with client.websocket_connect(f"/ws/lounges/{lounge_a}?token={host_token}") as ws_a:
        _ = ws_a.receive_json()  # snapshot A

        with client.websocket_connect(f"/ws/lounges/{lounge_b}?token={admin_token}") as ws_b:
            _ = ws_b.receive_json()  # snapshot B

            # Host A attempts to vote on Lounge B from Lounge A's WebSocket channel
            ws_a.send_json({
                "event": "lounge.queue.vote",
                "loungeId": lounge_b,
                "payload": {"trackId": "t_2002"},
                "ts": 333333,
            })

            # Host A receives lounge.error FORBIDDEN
            err_msg = ws_a.receive_json()
            assert err_msg["event"] == "lounge.error"
            assert err_msg["loungeId"] == lounge_a
            error_payload = LoungeErrorPayload(**err_msg["payload"])
            assert error_payload.code == "FORBIDDEN"

            # Lounge B verifies no broadcast was leaked or triggered
            _assert_exactly_one_broadcast(ws_b, lounge_b, probe_ts=3001)


def test_4_authentication_and_authorization_checks(client: TestClient):
    """Test 4: Client-supplied user ID is rejected (extra forbidden fields) and removed user cannot vote."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    listener_id = _get_user_id(client, listener_token)
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)
    _add_track(client, lounge_id, host_token, "t_2002")

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws:
        _ = ws.receive_json()  # snapshot

        # Attempt to spoof identity by passing userId/role in payload
        ws.send_json({
            "event": "lounge.queue.vote",
            "loungeId": lounge_id,
            "payload": {"trackId": "t_2002", "userId": "u_spoofed_admin", "role": "admin"},
            "ts": 444444,
        })
        err_spoof = ws.receive_json()
        assert err_spoof["event"] == "lounge.error"
        assert err_spoof["payload"]["code"] == "INVALID_PAYLOAD"

        # Host kicks listener via REST moderation
        kick_res = client.post(
            f"/api/v1/lounges/{lounge_id}/moderation",
            json={"action": "remove", "targetUserId": listener_id},
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert kick_res.status_code == 200

        # Kicked listener attempts a valid vote format
        ws.send_json({
            "event": "lounge.queue.vote",
            "loungeId": lounge_id,
            "payload": {"trackId": "t_2002"},
            "ts": 444445,
        })
        err_kicked = ws.receive_json()
        assert err_kicked["event"] == "lounge.error"
        assert err_kicked["payload"]["code"] == "USER_REMOVED"


def test_5_payload_validation_and_unsupported_events(client: TestClient):
    """Test 5: Payload validation rejects missing trackId, empty trackId, missing loungeId, and unsupported events."""
    host_token = _login_as(client, "host@spotify-intern.local")
    lounge_id, _ = _create_lounge(client, host_token, seed_track="t_2001")
    _add_track(client, lounge_id, host_token, "t_2002")

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws:
        _ = ws.receive_json()  # snapshot

        # 1. Missing trackId
        ws.send_json({
            "event": "lounge.queue.vote",
            "loungeId": lounge_id,
            "payload": {},
            "ts": 55501,
        })
        err_1 = ws.receive_json()
        assert err_1["event"] == "lounge.error"
        assert err_1["payload"]["code"] == "INVALID_PAYLOAD"

        # 2. Empty trackId
        ws.send_json({
            "event": "lounge.queue.vote",
            "loungeId": lounge_id,
            "payload": {"trackId": ""},
            "ts": 55502,
        })
        err_2 = ws.receive_json()
        assert err_2["event"] == "lounge.error"
        assert err_2["payload"]["code"] == "INVALID_PAYLOAD"

        # 3. Malformed trackId (whitespace / traversal)
        ws.send_json({
            "event": "lounge.queue.vote",
            "loungeId": lounge_id,
            "payload": {"trackId": "track / evil"},
            "ts": 55503,
        })
        err_3 = ws.receive_json()
        assert err_3["event"] == "lounge.error"
        assert err_3["payload"]["code"] == "INVALID_PAYLOAD"

        # 4. Missing envelope loungeId
        ws.send_json({
            "event": "lounge.queue.vote",
            "payload": {"trackId": "t_2002"},
            "ts": 55504,
        })
        err_4 = ws.receive_json()
        assert err_4["event"] == "lounge.error"
        assert err_4["payload"]["code"] == "INVALID_PAYLOAD"

        # 5. Unsupported event name
        ws.send_json({
            "event": "lounge.unsupported.action",
            "loungeId": lounge_id,
            "payload": {"foo": "bar"},
            "ts": 55505,
        })
        err_5 = ws.receive_json()
        assert err_5["event"] == "lounge.error"
        assert err_5["payload"]["code"] == "UNSUPPORTED_EVENT"


def test_6_domain_errors_and_persistence_failure(client: TestClient):
    """Test 6: Domain errors (track not in queue, ended lounge, persistence failure) send sanitized error and zero broadcasts."""
    host_token = _login_as(client, "host@spotify-intern.local")
    lounge_id, _ = _create_lounge(client, host_token, seed_track="t_2001")
    _add_track(client, lounge_id, host_token, "t_2002")

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws:
        _ = ws.receive_json()  # snapshot

        # 1. Track not in Next Up queue
        ws.send_json({
            "event": "lounge.queue.vote",
            "loungeId": lounge_id,
            "payload": {"trackId": "t_2009_not_queued"},
            "ts": 66601,
        })
        err_not_queued = ws.receive_json()
        assert err_not_queued["event"] == "lounge.error"
        assert err_not_queued["payload"]["code"] == "TRACK_NOT_IN_QUEUE"

        # Ping probe confirms no queue broadcast was emitted
        _assert_exactly_one_broadcast(ws, lounge_id, probe_ts=6001)

        # 2. Persistence failure simulation
        with patch.object(
            room_state_manager,
            "vote_track",
            side_effect=AppException(
                message="Simulated database failure during vote",
                status_code=500,
                code="PERSISTENCE_FAILED",
            ),
        ):
            ws.send_json({
                "event": "lounge.queue.vote",
                "loungeId": lounge_id,
                "payload": {"trackId": "t_2002"},
                "ts": 66602,
            })
            err_persist = ws.receive_json()
            assert err_persist["event"] == "lounge.error"
            assert err_persist["payload"]["code"] == "PERSISTENCE_FAILED"

        # Ping probe confirms zero broadcast emitted
        _assert_exactly_one_broadcast(ws, lounge_id, probe_ts=6002)

    # 3. Ended lounge
    end_res = client.post(
        f"/api/v1/lounges/{lounge_id}/end",
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert end_res.status_code == 200

    # Create new lounge and simulate ended state within active session
    lounge_id_2, _ = _create_lounge(client, host_token, seed_track="t_2001")
    _add_track(client, lounge_id_2, host_token, "t_2002")

    with client.websocket_connect(f"/ws/lounges/{lounge_id_2}?token={host_token}") as ws2:
        _ = ws2.receive_json()  # snapshot

        # Host ends lounge 2 via REST while socket is connected
        client.post(
            f"/api/v1/lounges/{lounge_id_2}/end",
            headers={"Authorization": f"Bearer {host_token}"},
        )

        ws2.send_json({
            "event": "lounge.queue.vote",
            "loungeId": lounge_id_2,
            "payload": {"trackId": "t_2002"},
            "ts": 66603,
        })
        err_ended = ws2.receive_json()
        assert err_ended["event"] == "lounge.error"
        assert err_ended["payload"]["code"] == "LOUNGE_ENDED"

        _assert_exactly_one_broadcast(ws2, lounge_id_2, probe_ts=6003)


def test_7_connection_stability_after_recoverable_errors(client: TestClient):
    """Test 7: WebSocket connection remains usable after recoverable errors; follow-up vote succeeds."""
    host_token = _login_as(client, "host@spotify-intern.local")
    lounge_id, _ = _create_lounge(client, host_token, seed_track="t_2001")
    _add_track(client, lounge_id, host_token, "t_2002")

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws:
        _ = ws.receive_json()  # snapshot

        # Send invalid payload
        ws.send_json({
            "event": "lounge.queue.vote",
            "loungeId": lounge_id,
            "payload": {"trackId": ""},
            "ts": 77701,
        })
        err_msg = ws.receive_json()
        assert err_msg["event"] == "lounge.error"
        assert err_msg["payload"]["code"] == "INVALID_PAYLOAD"

        # Connection is still alive: immediately send a valid vote on t_2002
        ws.send_json({
            "event": "lounge.queue.vote",
            "loungeId": lounge_id,
            "payload": {"trackId": "t_2002"},
            "ts": 77702,
        })
        update_msg = ws.receive_json()
        assert update_msg["event"] == "lounge.queue.update"
        assert update_msg["payload"]["version"] == 3
        assert update_msg["payload"]["queue"][0]["voteCount"] == 1

        # Verify ping/pong continues to operate normally
        _assert_exactly_one_broadcast(ws, lounge_id, probe_ts=7001)


def test_8_duplicate_broadcast_protection(client: TestClient):
    """Test 8: Successful WebSocket vote produces exactly one queue update broadcast without duplicates."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)
    _add_track(client, lounge_id, host_token, "t_2002")

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws:
        _ = ws.receive_json()  # snapshot

        ws.send_json({
            "event": "lounge.queue.vote",
            "loungeId": lounge_id,
            "payload": {"trackId": "t_2002"},
            "ts": 88801,
        })

        # Exactly one queue update is read
        update = ws.receive_json()
        assert update["event"] == "lounge.queue.update"

        # Probe immediately: if a duplicate queue update was emitted, receive_json will pop it instead of pong
        _assert_exactly_one_broadcast(ws, lounge_id, probe_ts=8001)
