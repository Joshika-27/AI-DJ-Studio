"""Module 8 Phase 2A Integration Tests: Centralized Queue Broadcast Integration.

Tests:
- REST track addition broadcasts lounge.queue.update (reason='add', version incremented).
- REST track voting broadcasts lounge.queue.update (reason='vote', voteCount and sparse position updated).
- REST host/co-dj override promote broadcasts lounge.queue.update (reason='override').
- REST host/co-dj override remove broadcasts lounge.queue.update (reason='override').
- REST host/co-dj override force_segue broadcasts lounge.queue.update (reason='segue').
- Strict cross-lounge isolation: queue mutations in Lounge A never broadcast to Lounge B (REQ-076).
- Persistence failure suppresses queue broadcast (zero phantom broadcasts).
- Broadcast failure does not fail or rollback committed database mutations.
- Multi-client broadcast delivery: all connected sockets in room receive identical updates.
- Payload validation against LoungeQueueUpdatePayload and WebSocketEnvelopeSchema.
"""

from __future__ import annotations

from typing import Tuple
from unittest.mock import AsyncMock, patch

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
        APP_NAME="AI DJ Studio Queue Broadcast Test",
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
# Phase 8.2A Focused Tests
# ==============================================================================

def test_1_rest_add_track_broadcasts_queue_update(client: TestClient):
    """Test 1: REST track addition broadcasts lounge.queue.update with reason 'add' and valid schema."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws:
        # Initial state snapshot
        snap = ws.receive_json()
        assert snap["event"] == "lounge.state.snapshot"
        assert snap["payload"]["queue"] == []

        # Listener adds track t_2002 via REST
        add_res = client.post(
            f"/api/v1/lounges/{lounge_id}/queue",
            json={"trackId": "t_2002"},
            headers={"Authorization": f"Bearer {listener_token}"},
        )
        assert add_res.status_code == 201, add_res.text
        add_body = add_res.json()
        assert add_body["version"] == 2
        assert len(add_body["queue"]) == 1

        # Host WebSocket receives lounge.queue.update
        update_msg = ws.receive_json()

        # Validate canonical envelope (REQ-060)
        env = WebSocketEnvelopeSchema(**update_msg)
        assert env.event == "lounge.queue.update"
        assert env.loungeId == lounge_id

        # Validate payload schema (REQ-061)
        payload = LoungeQueueUpdatePayload(**env.payload)
        assert payload.reason == "add"
        assert payload.version == 2
        assert payload.nowPlaying is not None
        assert payload.nowPlaying.trackId == "t_2001"
        assert len(payload.queue) == 1
        assert payload.queue[0].trackId == "t_2002"
        assert payload.queue[0].voteCount == 0
        assert payload.queue[0].position == 1000

        # Exactly-once assertion (ping probe)
        _assert_exactly_one_broadcast(ws, lounge_id, probe_ts=10001)


def test_2_rest_vote_track_broadcasts_queue_update(client: TestClient):
    """Test 2: REST voting broadcasts lounge.queue.update with reason 'vote' and updated voteCount."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    # Add track t_2002
    add_res = client.post(
        f"/api/v1/lounges/{lounge_id}/queue",
        json={"trackId": "t_2002"},
        headers={"Authorization": f"Bearer {listener_token}"},
    )
    assert add_res.status_code == 201

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws:
        snap = ws.receive_json()
        assert snap["event"] == "lounge.state.snapshot"
        assert len(snap["payload"]["queue"]) == 1

        # Listener votes for track t_2002 via REST
        vote_res = client.post(
            f"/api/v1/lounges/{lounge_id}/queue/vote",
            json={"trackId": "t_2002"},
            headers={"Authorization": f"Bearer {listener_token}"},
        )
        assert vote_res.status_code == 200, vote_res.text
        vote_body = vote_res.json()
        assert vote_body["version"] == 3
        assert vote_body["queue"][0]["voteCount"] == 1

        # Host WebSocket receives lounge.queue.update
        update_msg = ws.receive_json()
        env = WebSocketEnvelopeSchema(**update_msg)
        assert env.event == "lounge.queue.update"

        payload = LoungeQueueUpdatePayload(**env.payload)
        assert payload.reason == "vote"
        assert payload.version == 3
        assert len(payload.queue) == 1
        assert payload.queue[0].trackId == "t_2002"
        assert payload.queue[0].voteCount == 1

        # Exactly-once assertion (ping probe)
        _assert_exactly_one_broadcast(ws, lounge_id, probe_ts=20002)


def test_3_rest_override_promote_broadcasts_queue_update(client: TestClient):
    """Test 3: REST override promote broadcasts lounge.queue.update with reason 'override'."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    # Add two tracks
    client.post(
        f"/api/v1/lounges/{lounge_id}/queue",
        json={"trackId": "t_2002"},
        headers={"Authorization": f"Bearer {listener_token}"},
    )
    client.post(
        f"/api/v1/lounges/{lounge_id}/queue",
        json={"trackId": "t_2003"},
        headers={"Authorization": f"Bearer {listener_token}"},
    )

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws:
        _ = ws.receive_json()  # snapshot

        # Host promotes t_2003 to targetPosition=500.0 (ahead of t_2002)
        override_res = client.post(
            f"/api/v1/lounges/{lounge_id}/queue/override",
            json={"action": "promote", "trackId": "t_2003", "targetPosition": 500.0},
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert override_res.status_code == 200, override_res.text

        update_msg = ws.receive_json()
        env = WebSocketEnvelopeSchema(**update_msg)
        assert env.event == "lounge.queue.update"

        payload = LoungeQueueUpdatePayload(**env.payload)
        assert payload.reason == "override"
        assert len(payload.queue) == 2
        assert payload.queue[0].trackId == "t_2003"
        assert payload.queue[1].trackId == "t_2002"

        # Exactly-once assertion (ping probe)
        _assert_exactly_one_broadcast(ws, lounge_id, probe_ts=30003)


def test_4_rest_override_remove_broadcasts_queue_update(client: TestClient):
    """Test 4: REST override remove broadcasts lounge.queue.update with reason 'override'."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    # Add two tracks
    client.post(
        f"/api/v1/lounges/{lounge_id}/queue",
        json={"trackId": "t_2002"},
        headers={"Authorization": f"Bearer {listener_token}"},
    )
    client.post(
        f"/api/v1/lounges/{lounge_id}/queue",
        json={"trackId": "t_2003"},
        headers={"Authorization": f"Bearer {listener_token}"},
    )

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws:
        _ = ws.receive_json()  # snapshot

        # Host removes t_2002
        override_res = client.post(
            f"/api/v1/lounges/{lounge_id}/queue/override",
            json={"action": "remove", "trackId": "t_2002"},
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert override_res.status_code == 200, override_res.text

        update_msg = ws.receive_json()
        env = WebSocketEnvelopeSchema(**update_msg)
        assert env.event == "lounge.queue.update"

        payload = LoungeQueueUpdatePayload(**env.payload)
        assert payload.reason == "override"
        assert len(payload.queue) == 1
        assert payload.queue[0].trackId == "t_2003"

        # Exactly-once assertion (ping probe)
        _assert_exactly_one_broadcast(ws, lounge_id, probe_ts=40004)


def test_5_rest_override_force_segue_broadcasts_queue_update(client: TestClient):
    """Test 5: REST override force_segue broadcasts lounge.queue.update with reason 'segue'."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    # Add track t_2002
    client.post(
        f"/api/v1/lounges/{lounge_id}/queue",
        json={"trackId": "t_2002"},
        headers={"Authorization": f"Bearer {listener_token}"},
    )

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws:
        _ = ws.receive_json()  # snapshot

        # Host triggers force_segue to t_2002
        override_res = client.post(
            f"/api/v1/lounges/{lounge_id}/queue/override",
            json={"action": "force_segue", "trackId": "t_2002"},
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert override_res.status_code == 200, override_res.text

        update_msg = ws.receive_json()
        env = WebSocketEnvelopeSchema(**update_msg)
        assert env.event == "lounge.queue.update"

        payload = LoungeQueueUpdatePayload(**env.payload)
        assert payload.reason == "segue"
        assert payload.nowPlaying is not None
        assert payload.nowPlaying.trackId == "t_2002"
        assert payload.queue == []

        # Exactly-once assertion (ping probe)
        _assert_exactly_one_broadcast(ws, lounge_id, probe_ts=50005)


def test_6_queue_broadcast_cross_lounge_isolation(client: TestClient):
    """Test 6: Queue mutations in Lounge A never broadcast to Lounge B (REQ-076)."""
    host_token = _login_as(client, "host@spotify-intern.local")
    admin_token = _login_as(client, "admin@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")

    lounge_a, invite_a = _create_lounge(client, host_token, seed_track="t_2001")
    lounge_b, _ = _create_lounge(client, admin_token, seed_track="t_2004")
    _join_lounge(client, invite_a, listener_token)

    with client.websocket_connect(f"/ws/lounges/{lounge_a}?token={host_token}") as ws_a:
        _ = ws_a.receive_json()  # lounge A snapshot

        with client.websocket_connect(f"/ws/lounges/{lounge_b}?token={admin_token}") as ws_b:
            _ = ws_b.receive_json()  # lounge B snapshot

            # Listener adds track to Lounge A
            add_res = client.post(
                f"/api/v1/lounges/{lounge_a}/queue",
                json={"trackId": "t_2002"},
                headers={"Authorization": f"Bearer {listener_token}"},
            )
            assert add_res.status_code == 201

            # Lounge A receives queue update
            msg_a = ws_a.receive_json()
            assert msg_a["event"] == "lounge.queue.update"
            assert msg_a["loungeId"] == lounge_a

            # Lounge B verifies no event was received by round-tripping a ping/pong
            ws_b.send_json({
                "event": "lounge.sync.ping",
                "loungeId": lounge_b,
                "payload": {"clientSentAt": 54321},
                "ts": 54321,
            })
            pong_b = ws_b.receive_json()
            assert pong_b["event"] == "lounge.sync.pong"
            assert pong_b["loungeId"] == lounge_b


def test_7_persistence_failure_suppresses_queue_broadcast(client: TestClient):
    """Test 7: Persistence failure suppresses queue broadcast (zero phantom state)."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws:
        _ = ws.receive_json()  # snapshot

        # Patch room_state_manager.add_track to raise persistence failure
        with patch.object(
            room_state_manager,
            "add_track",
            side_effect=AppException(
                message="Simulated database failure during add_track",
                status_code=500,
                code="PERSISTENCE_FAILED",
            ),
        ):
            failed_res = client.post(
                f"/api/v1/lounges/{lounge_id}/queue",
                json={"trackId": "t_2002"},
                headers={"Authorization": f"Bearer {listener_token}"},
            )
            assert failed_res.status_code == 500

        # WS verifies no broadcast occurred by performing ping/pong
        ws.send_json({
            "event": "lounge.sync.ping",
            "loungeId": lounge_id,
            "payload": {"clientSentAt": 11111},
            "ts": 11111,
        })
        pong = ws.receive_json()
        assert pong["event"] == "lounge.sync.pong"


def test_8_broadcast_failure_does_not_fail_committed_mutation(client: TestClient):
    """Test 8: Broadcast failure does not undo or fail a committed DB mutation."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    # Patch connection_manager.broadcast to raise an unexpected runtime error
    with patch.object(
        connection_manager,
        "broadcast",
        side_effect=RuntimeError("Simulated network broadcast outage"),
    ):
        add_res = client.post(
            f"/api/v1/lounges/{lounge_id}/queue",
            json={"trackId": "t_2002"},
            headers={"Authorization": f"Bearer {listener_token}"},
        )
        # REST mutation must still succeed
        assert add_res.status_code == 201, add_res.text
        body = add_res.json()
        assert body["version"] == 2
        assert len(body["queue"]) == 1
        assert body["queue"][0]["trackId"] == "t_2002"

    # Verify state remains committed and retrievable via GET
    get_res = client.get(
        f"/api/v1/lounges/{lounge_id}/queue",
        headers={"Authorization": f"Bearer {listener_token}"},
    )
    assert get_res.status_code == 200
    get_body = get_res.json()
    assert get_body["version"] == 2
    assert len(get_body["queue"]) == 1
    assert get_body["queue"][0]["trackId"] == "t_2002"


def test_9_multi_client_queue_broadcast_delivery(client: TestClient):
    """Test 9: All connected clients in room receive broadcast simultaneously without duplicates."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws_host:
        _ = ws_host.receive_json()  # host snapshot

        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws_listener:
            _ = ws_host.receive_json()  # host receives listener joined presence
            _ = ws_listener.receive_json()  # listener snapshot

            # Host adds track t_2002 via REST
            add_res = client.post(
                f"/api/v1/lounges/{lounge_id}/queue",
                json={"trackId": "t_2002"},
                headers={"Authorization": f"Bearer {host_token}"},
            )
            assert add_res.status_code == 201

            # Both host and listener receive the queue update broadcast
            host_msg = ws_host.receive_json()
            listener_msg = ws_listener.receive_json()

            assert host_msg["event"] == "lounge.queue.update"
            assert listener_msg["event"] == "lounge.queue.update"

            assert host_msg["payload"]["version"] == 2
            assert listener_msg["payload"]["version"] == 2
            assert host_msg["payload"]["queue"][0]["trackId"] == "t_2002"
            assert listener_msg["payload"]["queue"][0]["trackId"] == "t_2002"

            # Exactly-once assertion on both client channels (ping probe)
            _assert_exactly_one_broadcast(ws_host, lounge_id, probe_ts=90001)
            _assert_exactly_one_broadcast(ws_listener, lounge_id, probe_ts=90002)
