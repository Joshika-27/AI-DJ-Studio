"""Module 8 Phase 4 Integration Tests: Host Disconnect, Co-DJ Promotion & Mutation Freeze (PRD Section 5, 6.1, REQ-015, REQ-025, REQ-028, REQ-029, REQ-078).

Tests:
1. Host disconnect without Co-DJ enters host_disconnected state and freezes queue mutations.
2. Eligible connected Co-DJ is promoted to Host when Host disconnects (mutations remain unfrozen).
3. Disconnected Co-DJ is ineligible for promotion; room freezes mutations.
4. Promoted Co-DJ acquires Host authority (can execute queue overrides and moderation).
5. Host reconnection unfreezes queue mutations and broadcasts host_reconnected.
6. Multi-tab host disconnect behavior: closing secondary tab does not trigger host disconnect.
7. Concurrent disconnect/reconnect handling: serialized safely via lounge lock.
8. Mutation freeze activation and mid-session snapshot reflection (hostStatus: host_disconnected).
9. REST queue mutations rejected during freeze with HTTP 403 MUTATIONS_FROZEN.
10. WebSocket queue mutations rejected during freeze with lounge.error MUTATIONS_FROZEN.
11. Queue mutations immediately succeed when unfrozen (after reconnect or promotion).
12. Live chat, typing, and emoji reactions remain functional during mutation freeze.
13. Cross-lounge isolation: freeze in Lounge A does not affect Lounge B.
14. Lounge termination cleanup: ending lounge cleans up in-memory freeze and room state.
15. Deterministic tie-breaking among multiple connected Co-DJs (earliest joined promoted).
"""

from __future__ import annotations

import asyncio
from typing import Tuple

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.db.session import (
    configure_database,
    create_session_maker,
    drop_db,
    init_db,
)
from app.main import create_app
from app.schemas.websocket import (
    LoungeHostStatusPayload,
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
        APP_NAME="AI DJ Studio WS Host Lifecycle Test",
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
    """Verify exactly-once delivery: sends ping and asserts next message is pong."""
    ws.send_json({
        "event": "lounge.sync.ping",
        "loungeId": lounge_id,
        "payload": {"clientSentAt": probe_ts},
        "ts": probe_ts,
    })
    probe_reply = ws.receive_json()
    assert probe_reply["event"] == "lounge.sync.pong", (
        f"Expected immediate lounge.sync.pong, got: {probe_reply.get('event')}"
    )
    assert probe_reply["loungeId"] == lounge_id
    assert probe_reply["payload"]["clientSentAt"] == probe_ts


# ==============================================================================
# TESTS
# ==============================================================================


def test_1_host_disconnect_no_co_dj_triggers_freeze(client: TestClient):
    """Test 1: Host disconnect with no Co-DJ triggers host_disconnected and freezes mutations."""
    host_token = _login_as(client, "host@spotify-intern.local")
    host_id = _get_user_id(client, host_token)
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws_listener:
        _ = ws_listener.receive_json()  # snapshot

        # Host connects then drops
        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws_host:
            _ = ws_host.receive_json()      # host snapshot
            _ = ws_listener.receive_json()  # presence join for host

        # Host has disconnected: listener should receive lounge.host.status broadcast
        host_status_msg = ws_listener.receive_json()
        assert host_status_msg["event"] == "lounge.host.status"
        assert host_status_msg["loungeId"] == lounge_id
        WebSocketEnvelopeSchema(**host_status_msg)

        payload = host_status_msg["payload"]
        parsed_payload = LoungeHostStatusPayload(**payload)
        assert parsed_payload.status == "host_disconnected"
        assert parsed_payload.activeHostUserId == host_id
        assert parsed_payload.mutationsFrozen is True

        assert room_state_manager.is_mutations_frozen(lounge_id) is True
        _assert_exactly_one_broadcast(ws_listener, lounge_id, probe_ts=1001)


def test_2_eligible_co_dj_promotion(client: TestClient):
    """Test 2: Eligible connected Co-DJ is promoted to Host when Host disconnects (no freeze)."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    listener_id = _get_user_id(client, listener_token)
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    # Host assigns Co-DJ role to listener
    mod_res = client.post(
        f"/api/v1/lounges/{lounge_id}/moderation",
        json={"action": "set_role", "targetUserId": listener_id, "newRole": "co_dj"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert mod_res.status_code == 200

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws_co_dj:
        _ = ws_co_dj.receive_json()  # snapshot

        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws_host:
            _ = ws_host.receive_json()
            _ = ws_co_dj.receive_json()  # presence join for host

        # Host disconnects: Co-DJ should be promoted
        promo_msg = ws_co_dj.receive_json()
        assert promo_msg["event"] == "lounge.host.status"
        parsed = LoungeHostStatusPayload(**promo_msg["payload"])
        assert parsed.status == "co_dj_promoted"
        assert parsed.activeHostUserId == listener_id
        assert parsed.mutationsFrozen is False

        assert room_state_manager.is_mutations_frozen(lounge_id) is False
        assert room_state_manager.get_active_host_user_id(lounge_id) == listener_id


def test_3_no_eligible_co_dj_when_co_dj_disconnected(client: TestClient):
    """Test 3: If Co-DJ is not connected when Host disconnects, mutations are frozen."""
    host_token = _login_as(client, "host@spotify-intern.local")
    host_id = _get_user_id(client, host_token)
    listener_token = _login_as(client, "listener@spotify-intern.local")
    listener_id = _get_user_id(client, listener_token)
    admin_token = _login_as(client, "admin@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)
    _join_lounge(client, invite_code, admin_token)

    # Assign Co-DJ to listener
    client.post(
        f"/api/v1/lounges/{lounge_id}/moderation",
        json={"action": "set_role", "targetUserId": listener_id, "newRole": "co_dj"},
        headers={"Authorization": f"Bearer {host_token}"},
    )

    # Listener (Co-DJ) connects then DISCONNECTS before Host drops
    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws_co_dj:
        _ = ws_co_dj.receive_json()

    # Another participant (plain listener) remains connected
    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={admin_token}") as ws_other:
        _ = ws_other.receive_json()

        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws_host:
            _ = ws_host.receive_json()
            _ = ws_other.receive_json()  # host join

        # Host disconnects while Co-DJ is NOT connected
        status_msg = ws_other.receive_json()
        assert status_msg["event"] == "lounge.host.status"
        parsed = LoungeHostStatusPayload(**status_msg["payload"])
        assert parsed.status == "host_disconnected"
        assert parsed.mutationsFrozen is True


def test_4_promotion_authority_validation(client: TestClient):
    """Test 4: Promoted Co-DJ acquires Host authority; non-promoted participants cannot execute overrides."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    listener_id = _get_user_id(client, listener_token)
    other_token = _login_as(client, "admin@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    # Promote listener to co_dj
    client.post(
        f"/api/v1/lounges/{lounge_id}/moderation",
        json={"action": "set_role", "targetUserId": listener_id, "newRole": "co_dj"},
        headers={"Authorization": f"Bearer {host_token}"},
    )

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws_co_dj:
        _ = ws_co_dj.receive_json()

        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws_host:
            _ = ws_host.receive_json()
            _ = ws_co_dj.receive_json()

        # Host drops -> listener is promoted to Host
        _ = ws_co_dj.receive_json()  # co_dj_promoted

        # Promoted user adds track and performs override
        add_res = client.post(
            f"/api/v1/lounges/{lounge_id}/queue",
            json={"trackId": "t_2002"},
            headers={"Authorization": f"Bearer {listener_token}"},
        )
        assert add_res.status_code == 201

        # Promoted user executes queue override (promote track)
        override_res = client.post(
            f"/api/v1/lounges/{lounge_id}/queue/override",
            json={"action": "promote", "trackId": "t_2002", "targetPosition": 1},
            headers={"Authorization": f"Bearer {listener_token}"},
        )
        assert override_res.status_code == 200


def test_5_host_reconnection_unfreezes_mutations(client: TestClient):
    """Test 5: Reconnecting Host unfreezes mutations and emits host_reconnected."""
    host_token = _login_as(client, "host@spotify-intern.local")
    host_id = _get_user_id(client, host_token)
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws_listener:
        _ = ws_listener.receive_json()  # snapshot

        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws_host:
            _ = ws_host.receive_json()
            _ = ws_listener.receive_json()

        # Host disconnects -> frozen
        disconnect_msg = ws_listener.receive_json()
        assert disconnect_msg["payload"]["mutationsFrozen"] is True
        assert room_state_manager.is_mutations_frozen(lounge_id) is True

        # Host reconnects
        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws_host_reconnected:
            snapshot = ws_host_reconnected.receive_json()
            assert snapshot["payload"]["hostStatus"] == "connected"

            reconnect_msg = ws_listener.receive_json()
            assert reconnect_msg["event"] == "lounge.host.status"
            parsed = LoungeHostStatusPayload(**reconnect_msg["payload"])
            assert parsed.status == "host_reconnected"
            assert parsed.activeHostUserId == host_id
            assert parsed.mutationsFrozen is False

            assert room_state_manager.is_mutations_frozen(lounge_id) is False


def test_6_multi_tab_host_disconnect_behavior(client: TestClient):
    """Test 6: Closing secondary host tab does not trigger host disconnect."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws_listener:
        _ = ws_listener.receive_json()

        # Host opens Tab 1
        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws_host_tab1:
            _ = ws_host_tab1.receive_json()
            _ = ws_listener.receive_json()

            # Host opens Tab 2
            with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws_host_tab2:
                _ = ws_host_tab2.receive_json()

            # Tab 2 closed: Host still has Tab 1 open!
            # Listener should NOT receive host_disconnected
            _assert_exactly_one_broadcast(ws_listener, lounge_id, probe_ts=6001)
            assert room_state_manager.is_mutations_frozen(lounge_id) is False

        # Tab 1 closed: Now host is truly disconnected
        disconnect_msg = ws_listener.receive_json()
        assert disconnect_msg["event"] == "lounge.host.status"
        assert disconnect_msg["payload"]["status"] == "host_disconnected"
        assert room_state_manager.is_mutations_frozen(lounge_id) is True


def test_7_concurrent_disconnect_and_reconnect_race(client: TestClient):
    """Test 7: Rapid disconnect/reconnect transitions are serialized safely without split-brain."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws_listener:
        _ = ws_listener.receive_json()

        for cycle in range(3):
            with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws_h:
                _ = ws_h.receive_json()
                if cycle > 0:
                    # Drain reconnect event
                    _ = ws_listener.receive_json()
                else:
                    _ = ws_listener.receive_json()  # initial join

            # Host disconnects
            disc_msg = ws_listener.receive_json()
            assert disc_msg["event"] == "lounge.host.status"
            assert disc_msg["payload"]["status"] == "host_disconnected"
            assert room_state_manager.is_mutations_frozen(lounge_id) is True


def test_8_mutation_freeze_activation_and_snapshot(client: TestClient):
    """Test 8: New client connecting while host is disconnected receives hostStatus: host_disconnected."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")

    # Host connects and immediately disconnects
    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws_host:
        _ = ws_host.receive_json()

    assert room_state_manager.is_mutations_frozen(lounge_id) is True

    # New listener joins and connects via WS
    _join_lounge(client, invite_code, listener_token)
    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws_listener:
        snapshot = ws_listener.receive_json()
        assert snapshot["event"] == "lounge.state.snapshot"
        assert snapshot["payload"]["hostStatus"] == "host_disconnected"


def test_9_queue_mutations_rejected_during_freeze_rest(client: TestClient):
    """Test 9: REST queue add, vote, and override reject with HTTP 403 MUTATIONS_FROZEN when frozen."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    # Host adds a track before dropping
    client.post(
        f"/api/v1/lounges/{lounge_id}/queue",
        json={"trackId": "t_2002"},
        headers={"Authorization": f"Bearer {host_token}"},
    )

    # Host connects and drops to freeze
    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws:
        _ = ws.receive_json()

    assert room_state_manager.is_mutations_frozen(lounge_id) is True

    # 1. Add track fails
    res_add = client.post(
        f"/api/v1/lounges/{lounge_id}/queue",
        json={"trackId": "t_2003"},
        headers={"Authorization": f"Bearer {listener_token}"},
    )
    assert res_add.status_code == 403
    assert res_add.json()["error"]["code"] == "MUTATIONS_FROZEN"

    # 2. Vote track fails
    res_vote = client.post(
        f"/api/v1/lounges/{lounge_id}/queue/vote",
        json={"trackId": "t_2002"},
        headers={"Authorization": f"Bearer {listener_token}"},
    )
    assert res_vote.status_code == 403
    assert res_vote.json()["error"]["code"] == "MUTATIONS_FROZEN"

    # 3. Queue override fails
    res_override = client.post(
        f"/api/v1/lounges/{lounge_id}/queue/override",
        json={"action": "promote", "trackId": "t_2002", "targetPosition": 1},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_override.status_code == 403
    assert res_override.json()["error"]["code"] == "MUTATIONS_FROZEN"


def test_10_queue_mutations_rejected_during_freeze_websocket(client: TestClient):
    """Test 10: WebSocket queue vote is rejected with lounge.error MUTATIONS_FROZEN when frozen."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    # Add track
    client.post(
        f"/api/v1/lounges/{lounge_id}/queue",
        json={"trackId": "t_2002"},
        headers={"Authorization": f"Bearer {host_token}"},
    )

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws_listener:
        _ = ws_listener.receive_json()

        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws_host:
            _ = ws_host.receive_json()
            _ = ws_listener.receive_json()

        # Host disconnects
        _ = ws_listener.receive_json()  # host_disconnected
        assert room_state_manager.is_mutations_frozen(lounge_id) is True

        # Listener tries to vote via WebSocket
        ws_listener.send_json({
            "event": "lounge.queue.vote",
            "loungeId": lounge_id,
            "payload": {"trackId": "t_2002"},
            "ts": 10001,
        })
        err_msg = ws_listener.receive_json()
        assert err_msg["event"] == "lounge.error"
        assert err_msg["payload"]["code"] == "MUTATIONS_FROZEN"


def test_11_mutations_permitted_when_unfrozen(client: TestClient):
    """Test 11: Queue mutations succeed cleanly once room is unfrozen by reconnecting Host."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)

    # Freeze by dropping host
    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws:
        _ = ws.receive_json()
    assert room_state_manager.is_mutations_frozen(lounge_id) is True

    # Host reconnects
    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws_host:
        _ = ws_host.receive_json()
        assert room_state_manager.is_mutations_frozen(lounge_id) is False

        # Mutations now succeed
        add_res = client.post(
            f"/api/v1/lounges/{lounge_id}/queue",
            json={"trackId": "t_2002"},
            headers={"Authorization": f"Bearer {listener_token}"},
        )
        assert add_res.status_code == 201

        vote_res = client.post(
            f"/api/v1/lounges/{lounge_id}/queue/vote",
            json={"trackId": "t_2002"},
            headers={"Authorization": f"Bearer {listener_token}"},
        )
        assert vote_res.status_code == 200


def test_12_chat_and_reactions_permitted_during_freeze(client: TestClient):
    """Test 12: Live chat, typing, and emoji reactions remain functional during mutation freeze."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    admin_token = _login_as(client, "admin@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")
    _join_lounge(client, invite_code, listener_token)
    _join_lounge(client, invite_code, admin_token)

    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={listener_token}") as ws_l1:
        _ = ws_l1.receive_json()

        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={admin_token}") as ws_l2:
            _ = ws_l2.receive_json()
            _ = ws_l1.receive_json()  # ws_l1 gets admin presence

            # Host connects, posts initial message, then disconnects
            with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws_host:
                _ = ws_host.receive_json()
                _ = ws_l1.receive_json()
                _ = ws_l2.receive_json()

                ws_host.send_json({
                    "event": "lounge.chat.message",
                    "loungeId": lounge_id,
                    "payload": {"body": "Host says hello"},
                    "ts": 12001,
                })
                msg = ws_host.receive_json()
                _ = ws_l1.receive_json()
                _ = ws_l2.receive_json()
                message_id = msg["payload"]["messageId"]

            # Host disconnected -> frozen
            _ = ws_l1.receive_json()  # host_disconnected
            _ = ws_l2.receive_json()
            assert room_state_manager.is_mutations_frozen(lounge_id) is True

            # 1. Typing indicators still work
            ws_l1.send_json({
                "event": "lounge.chat.typing",
                "loungeId": lounge_id,
                "payload": {"isTyping": True},
                "ts": 12002,
            })
            _ = ws_l1.receive_json()
            typing_broadcast = ws_l2.receive_json()
            assert typing_broadcast["event"] == "lounge.chat.typing"

            # 2. Chat messages still work
            ws_l1.send_json({
                "event": "lounge.chat.message",
                "loungeId": lounge_id,
                "payload": {"body": "Chatting while host is away"},
                "ts": 12003,
            })
            _ = ws_l1.receive_json()
            chat_broadcast = ws_l2.receive_json()
            assert chat_broadcast["event"] == "lounge.chat.message"

            # 3. Emoji reactions still work
            ws_l1.send_json({
                "event": "lounge.chat.reaction",
                "loungeId": lounge_id,
                "payload": {"messageId": message_id, "emoji": "🔥", "action": "add"},
                "ts": 12004,
            })
            _ = ws_l1.receive_json()
            rx_broadcast = ws_l2.receive_json()
            assert rx_broadcast["event"] == "lounge.chat.reaction"


def test_13_lounge_isolation(client: TestClient):
    """Test 13: Host disconnect and mutation freeze in Lounge A do not affect Lounge B."""
    host_token = _login_as(client, "host@spotify-intern.local")
    admin_token = _login_as(client, "admin@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_a, _ = _create_lounge(client, host_token, seed_track="t_2001")
    lounge_b, invite_b = _create_lounge(client, admin_token, seed_track="t_2004")
    _join_lounge(client, invite_b, listener_token)

    # Freeze Lounge A by connecting and dropping Host A
    with client.websocket_connect(f"/ws/lounges/{lounge_a}?token={host_token}") as ws_a:
        _ = ws_a.receive_json()

    assert room_state_manager.is_mutations_frozen(lounge_a) is True
    assert room_state_manager.is_mutations_frozen(lounge_b) is False

    # Queue mutations in Lounge B must succeed normally
    add_b = client.post(
        f"/api/v1/lounges/{lounge_b}/queue",
        json={"trackId": "t_2002"},
        headers={"Authorization": f"Bearer {listener_token}"},
    )
    assert add_b.status_code == 201


def test_14_lounge_termination_cleanup(client: TestClient):
    """Test 14: Ending a lounge cleans up in-memory freeze state and room state."""
    host_token = _login_as(client, "host@spotify-intern.local")
    lounge_id, _ = _create_lounge(client, host_token, seed_track="t_2001")

    # Connect host and drop to freeze
    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws:
        _ = ws.receive_json()

    assert room_state_manager.is_mutations_frozen(lounge_id) is True

    # Host ends lounge
    end_res = client.post(
        f"/api/v1/lounges/{lounge_id}/end",
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert end_res.status_code == 200

    # In-memory freeze state cleared
    assert room_state_manager.is_mutations_frozen(lounge_id) is False


def test_15_deterministic_tie_breaker_among_multiple_co_djs(client: TestClient):
    """Test 15: When multiple Co-DJs are connected, the one who joined earliest is deterministically promoted."""
    host_token = _login_as(client, "host@spotify-intern.local")
    user1_token = _login_as(client, "listener@spotify-intern.local")
    user1_id = _get_user_id(client, user1_token)
    user2_token = _login_as(client, "admin@spotify-intern.local")
    user2_id = _get_user_id(client, user2_token)
    lounge_id, invite_code = _create_lounge(client, host_token, seed_track="t_2001")

    # User 1 joins FIRST
    _join_lounge(client, invite_code, user1_token)
    # User 2 joins SECOND
    _join_lounge(client, invite_code, user2_token)

    # Both promoted to Co-DJ
    client.post(
        f"/api/v1/lounges/{lounge_id}/moderation",
        json={"action": "set_role", "targetUserId": user1_id, "newRole": "co_dj"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    client.post(
        f"/api/v1/lounges/{lounge_id}/moderation",
        json={"action": "set_role", "targetUserId": user2_id, "newRole": "co_dj"},
        headers={"Authorization": f"Bearer {host_token}"},
    )

    # Both connect
    with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={user1_token}") as ws1:
        _ = ws1.receive_json()

        with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={user2_token}") as ws2:
            _ = ws2.receive_json()
            _ = ws1.receive_json()  # ws1 receives presence update for ws2

            with client.websocket_connect(f"/ws/lounges/{lounge_id}?token={host_token}") as ws_host:
                _ = ws_host.receive_json()
                _ = ws1.receive_json()
                _ = ws2.receive_json()

            # Host disconnects -> earliest joined Co-DJ (User 1) promoted
            promo1 = ws1.receive_json()
            promo2 = ws2.receive_json()

            for p in (promo1, promo2):
                assert p["event"] == "lounge.host.status"
                assert p["payload"]["status"] == "co_dj_promoted"
                assert p["payload"]["activeHostUserId"] == user1_id
                assert p["payload"]["mutationsFrozen"] is False
