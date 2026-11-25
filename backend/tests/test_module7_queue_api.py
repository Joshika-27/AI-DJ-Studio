"""Module 7 Phase 2 Integration Tests: Queue REST API endpoints.

Tests:
- POST /api/v1/lounges/{id}/queue (Add Track)
- POST /api/v1/lounges/{id}/queue/vote (Vote Track)
- POST /api/v1/lounges/{id}/queue/override (Promote, Remove, Force Segue)
- GET /api/v1/lounges/{id}/queue (Fetch Queue State)
"""

from unittest.mock import patch

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
from app.seeds.seed_data import seed_database
from app.services.room_state_manager import room_state_manager

TEST_ACCESS_SECRET = "module7_test_access_secret_key_at_least_32_bytes_long_001"
TEST_REFRESH_SECRET = "module7_test_refresh_secret_key_at_least_32_bytes_long_002"
TEST_SEED_PASSWORD = "TestFixtureSeedPass!2026"


@pytest.fixture
async def queue_db_engine() -> AsyncEngine:
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
def clean_cache():
    room_state_manager.clear_cache_for_testing()
    yield
    room_state_manager.clear_cache_for_testing()


@pytest.fixture
def queue_settings() -> Settings:
    return Settings(
        _env_file=None,
        APP_NAME="AI DJ Studio Queue Test",
        APP_ENV="testing",
        JWT_SECRET_KEY=TEST_ACCESS_SECRET,
        JWT_REFRESH_SECRET_KEY=TEST_REFRESH_SECRET,
    )


@pytest.fixture
def client(
    queue_db_engine: AsyncEngine,
    queue_settings: Settings,
) -> TestClient:
    app = create_app(settings_override=queue_settings)
    return TestClient(app, raise_server_exceptions=False)


def _login_as(client: TestClient, email: str, password: str = TEST_SEED_PASSWORD) -> str:
    res = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert res.status_code == 200, f"Login failed for {email}: {res.text}"
    return res.json()["accessToken"]


def _create_lounge(client: TestClient, host_token: str, seed_track: str = "t_2001") -> tuple[str, str]:
    res = client.post(
        "/api/v1/lounges",
        json={
            "seedTrackId": seed_track,
            "vibeProfile": "Deep House Sunset",
            "maxParticipants": 40,
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
# Focused Test Cases
# ==============================================================================

def test_1_successful_track_addition_and_201_response(client: TestClient):
    """Test 1: Successful track addition by host and joined listener returns 201 with full queue state."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token)

    # 1. Host adds track t_2002 -> position 1000
    res1 = client.post(
        f"/api/v1/lounges/{lounge_id}/queue",
        json={"trackId": "t_2002"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res1.status_code == 201, res1.text
    body1 = res1.json()
    assert body1["loungeId"] == lounge_id
    assert body1["nowPlaying"]["trackId"] == "t_2001"
    assert len(body1["queue"]) == 1
    assert body1["queue"][0]["trackId"] == "t_2002"
    assert body1["queue"][0]["position"] == 1000
    assert body1["queue"][0]["voteCount"] == 0
    assert body1["version"] >= 2

    # 2. Listener joins lounge and adds track t_2003 -> position 2000
    _join_lounge(client, invite_code, listener_token)
    res2 = client.post(
        f"/api/v1/lounges/{lounge_id}/queue",
        json={"trackId": "t_2003"},
        headers={"Authorization": f"Bearer {listener_token}"},
    )
    assert res2.status_code == 201, res2.text
    body2 = res2.json()
    assert len(body2["queue"]) == 2
    assert body2["queue"][1]["trackId"] == "t_2003"
    assert body2["queue"][1]["position"] == 2000


def test_2_successful_voting_and_updated_ordering(client: TestClient):
    """Test 2: Upvoting tracks reorders queue deterministically (voteCount DESC, insertionSeq ASC)."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token)
    _join_lounge(client, invite_code, listener_token)

    # Add 3 tracks: t_2002, t_2003, t_2004
    client.post(f"/api/v1/lounges/{lounge_id}/queue", json={"trackId": "t_2002"}, headers={"Authorization": f"Bearer {host_token}"})
    client.post(f"/api/v1/lounges/{lounge_id}/queue", json={"trackId": "t_2003"}, headers={"Authorization": f"Bearer {host_token}"})
    client.post(f"/api/v1/lounges/{lounge_id}/queue", json={"trackId": "t_2004"}, headers={"Authorization": f"Bearer {host_token}"})

    # Upvote t_2004 -> vote_count=1, moves to head
    res_vote1 = client.post(
        f"/api/v1/lounges/{lounge_id}/queue/vote",
        json={"trackId": "t_2004"},
        headers={"Authorization": f"Bearer {listener_token}"},
    )
    assert res_vote1.status_code == 200, res_vote1.text
    body_vote1 = res_vote1.json()
    assert [e["trackId"] for e in body_vote1["queue"]] == ["t_2004", "t_2002", "t_2003"]
    assert body_vote1["queue"][0]["voteCount"] == 1

    # Upvote t_2002 -> both t_2002 and t_2004 have 1 vote.
    # Deterministic tie break: t_2002 (seq=1) takes lead over t_2004 (seq=3)
    res_vote2 = client.post(
        f"/api/v1/lounges/{lounge_id}/queue/vote",
        json={"trackId": "t_2002"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_vote2.status_code == 200
    body_vote2 = res_vote2.json()
    assert [e["trackId"] for e in body_vote2["queue"]] == ["t_2002", "t_2004", "t_2003"]


def test_3_promote_remove_and_force_segue_overrides(client: TestClient):
    """Test 3: Promote, remove, and force segue override actions execute cleanly."""
    host_token = _login_as(client, "host@spotify-intern.local")
    lounge_id, _ = _create_lounge(client, host_token)

    # Add tracks: t_2002, t_2003, t_2004
    client.post(f"/api/v1/lounges/{lounge_id}/queue", json={"trackId": "t_2002"}, headers={"Authorization": f"Bearer {host_token}"})
    client.post(f"/api/v1/lounges/{lounge_id}/queue", json={"trackId": "t_2003"}, headers={"Authorization": f"Bearer {host_token}"})
    client.post(f"/api/v1/lounges/{lounge_id}/queue", json={"trackId": "t_2004"}, headers={"Authorization": f"Bearer {host_token}"})

    # 1. Promote tail (t_2004) to head
    res_prom = client.post(
        f"/api/v1/lounges/{lounge_id}/queue/override",
        json={"action": "promote", "trackId": "t_2004"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_prom.status_code == 200, res_prom.text
    body_prom = res_prom.json()
    assert [e["trackId"] for e in body_prom["queue"]] == ["t_2004", "t_2002", "t_2003"]

    # 2. Promote with explicit targetPosition between t_2004 (500) and t_2002 (1000)
    res_prom2 = client.post(
        f"/api/v1/lounges/{lounge_id}/queue/override",
        json={"action": "promote", "trackId": "t_2003", "targetPosition": 600},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_prom2.status_code == 200
    assert [e["trackId"] for e in res_prom2.json()["queue"]] == ["t_2004", "t_2003", "t_2002"]

    # 3. Remove track t_2003
    res_rem = client.post(
        f"/api/v1/lounges/{lounge_id}/queue/override",
        json={"action": "remove", "trackId": "t_2003"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_rem.status_code == 200
    assert [e["trackId"] for e in res_rem.json()["queue"]] == ["t_2004", "t_2002"]

    # 4. Force segue default head (t_2004) into Now Playing
    res_fs = client.post(
        f"/api/v1/lounges/{lounge_id}/queue/override",
        json={"action": "force_segue"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_fs.status_code == 200
    body_fs = res_fs.json()
    assert body_fs["nowPlaying"]["trackId"] == "t_2004"
    assert [e["trackId"] for e in body_fs["queue"]] == ["t_2002"]

    # 5. Force segue with explicit target track
    res_fs2 = client.post(
        f"/api/v1/lounges/{lounge_id}/queue/override",
        json={"action": "force_segue", "trackId": "t_2002"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_fs2.status_code == 200
    assert res_fs2.json()["nowPlaying"]["trackId"] == "t_2002"
    assert len(res_fs2.json()["queue"]) == 0


def test_4_role_based_authorization(client: TestClient):
    """Test 4: Queue override strictly rejects listeners with 403, and allows Co-DJ, Host, Admin."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    codj_token = _login_as(client, "codj@spotify-intern.local")
    admin_token = _login_as(client, "admin@spotify-intern.local")

    lounge_id, invite_code = _create_lounge(client, host_token)
    _join_lounge(client, invite_code, listener_token)
    _join_lounge(client, invite_code, codj_token)

    # Add tracks
    client.post(f"/api/v1/lounges/{lounge_id}/queue", json={"trackId": "t_2002"}, headers={"Authorization": f"Bearer {host_token}"})
    client.post(f"/api/v1/lounges/{lounge_id}/queue", json={"trackId": "t_2003"}, headers={"Authorization": f"Bearer {host_token}"})

    # 1. Listener token is rejected with 403 Forbidden
    res_listener = client.post(
        f"/api/v1/lounges/{lounge_id}/queue/override",
        json={"action": "promote", "trackId": "t_2003"},
        headers={"Authorization": f"Bearer {listener_token}"},
    )
    assert res_listener.status_code == 403
    assert res_listener.json()["error"]["code"] == "FORBIDDEN"

    # 2. Co-DJ token succeeds with 200
    res_codj = client.post(
        f"/api/v1/lounges/{lounge_id}/queue/override",
        json={"action": "promote", "trackId": "t_2003"},
        headers={"Authorization": f"Bearer {codj_token}"},
    )
    assert res_codj.status_code == 200

    # 3. Host token succeeds with 200
    res_host = client.post(
        f"/api/v1/lounges/{lounge_id}/queue/override",
        json={"action": "promote", "trackId": "t_2002"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_host.status_code == 200

    # 4. Admin token succeeds with 200
    res_admin = client.post(
        f"/api/v1/lounges/{lounge_id}/queue/override",
        json={"action": "remove", "trackId": "t_2002"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_admin.status_code == 200


def test_5_duplicate_and_missing_tracks(client: TestClient):
    """Test 5: Reject duplicates (400), missing tracks (404), and non-existent lounge (404)."""
    host_token = _login_as(client, "host@spotify-intern.local")
    lounge_id, _ = _create_lounge(client, host_token)

    # 1. Adding track currently in Now Playing (t_2001) returns 400 DUPLICATE_TRACK
    res_dup1 = client.post(
        f"/api/v1/lounges/{lounge_id}/queue",
        json={"trackId": "t_2001"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_dup1.status_code == 400
    assert res_dup1.json()["error"]["code"] == "DUPLICATE_TRACK"

    # Add t_2002
    client.post(f"/api/v1/lounges/{lounge_id}/queue", json={"trackId": "t_2002"}, headers={"Authorization": f"Bearer {host_token}"})

    # 2. Adding track already in Next Up (t_2002) returns 400 DUPLICATE_TRACK
    res_dup2 = client.post(
        f"/api/v1/lounges/{lounge_id}/queue",
        json={"trackId": "t_2002"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_dup2.status_code == 400
    assert res_dup2.json()["error"]["code"] == "DUPLICATE_TRACK"

    # 3. Adding non-existent catalog track returns 404 TRACK_NOT_FOUND
    res_missing_track = client.post(
        f"/api/v1/lounges/{lounge_id}/queue",
        json={"trackId": "t_9999_fake"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_missing_track.status_code == 404
    assert res_missing_track.json()["error"]["code"] == "TRACK_NOT_FOUND"

    # 4. Voting for track not in queue returns 404 TRACK_NOT_IN_QUEUE
    res_vote_missing = client.post(
        f"/api/v1/lounges/{lounge_id}/queue/vote",
        json={"trackId": "t_2005"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_vote_missing.status_code == 404
    assert res_vote_missing.json()["error"]["code"] == "TRACK_NOT_IN_QUEUE"

    # 5. Non-existent lounge returns 404 LOUNGE_NOT_FOUND
    res_missing_lounge = client.post(
        "/api/v1/lounges/lg_nonexistent/queue",
        json={"trackId": "t_2002"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_missing_lounge.status_code == 404
    assert res_missing_lounge.json()["error"]["code"] == "LOUNGE_NOT_FOUND"


def test_6_locked_and_ended_lounges(client: TestClient):
    """Test 6: Locked room rejects track addition with 403 ROOM_LOCKED; ended room rejects mutations with 409 LOUNGE_ENDED."""
    host_token = _login_as(client, "host@spotify-intern.local")
    lounge_id, _ = _create_lounge(client, host_token)

    # 1. Lock the lounge
    res_lock = client.post(
        f"/api/v1/lounges/{lounge_id}/moderation",
        json={"action": "lock", "locked": True},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_lock.status_code == 200

    # Adding track to locked lounge must return 403 ROOM_LOCKED
    res_add_locked = client.post(
        f"/api/v1/lounges/{lounge_id}/queue",
        json={"trackId": "t_2002"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_add_locked.status_code == 403
    assert res_add_locked.json()["error"]["code"] == "ROOM_LOCKED"

    # Unlock the lounge
    res_unlock = client.post(
        f"/api/v1/lounges/{lounge_id}/moderation",
        json={"action": "unlock"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_unlock.status_code == 200

    # Add track succeeds when unlocked
    res_add_unlocked = client.post(
        f"/api/v1/lounges/{lounge_id}/queue",
        json={"trackId": "t_2002"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_add_unlocked.status_code == 201

    # 2. End the lounge session
    res_end = client.post(
        f"/api/v1/lounges/{lounge_id}/end",
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_end.status_code == 200

    # Ended lounge rejects add with 409
    res_add_ended = client.post(
        f"/api/v1/lounges/{lounge_id}/queue",
        json={"trackId": "t_2003"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_add_ended.status_code == 409
    assert res_add_ended.json()["error"]["code"] == "LOUNGE_ENDED"

    # Ended lounge rejects vote with 409
    res_vote_ended = client.post(
        f"/api/v1/lounges/{lounge_id}/queue/vote",
        json={"trackId": "t_2002"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_vote_ended.status_code == 409
    assert res_vote_ended.json()["error"]["code"] == "LOUNGE_ENDED"

    # Ended lounge rejects override with 409
    res_over_ended = client.post(
        f"/api/v1/lounges/{lounge_id}/queue/override",
        json={"action": "force_segue"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_over_ended.status_code == 409
    assert res_over_ended.json()["error"]["code"] == "LOUNGE_ENDED"


def test_7_invalid_request_payloads_and_membership(client: TestClient):
    """Test 7: Schema validation rejects malformed payloads (422) and non-members receive 403."""
    host_token = _login_as(client, "host@spotify-intern.local")
    listener_token = _login_as(client, "listener@spotify-intern.local")
    lounge_id, _ = _create_lounge(client, host_token)

    # 1. Non-member listener attempting queue action without joining returns 403
    res_nonmember = client.post(
        f"/api/v1/lounges/{lounge_id}/queue",
        json={"trackId": "t_2002"},
        headers={"Authorization": f"Bearer {listener_token}"},
    )
    assert res_nonmember.status_code == 403
    assert res_nonmember.json()["error"]["code"] == "NOT_LOUNGE_MEMBER"

    # 2. Invalid payload: empty trackId returns 400 VALIDATION_ERROR
    res_empty_tid = client.post(
        f"/api/v1/lounges/{lounge_id}/queue",
        json={"trackId": "   "},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_empty_tid.status_code == 400
    assert res_empty_tid.json()["error"]["code"] == "VALIDATION_ERROR"

    # 3. Invalid payload: invalid action in override returns 400 VALIDATION_ERROR
    res_bad_action = client.post(
        f"/api/v1/lounges/{lounge_id}/queue/override",
        json={"action": "shuffle"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_bad_action.status_code == 400
    assert res_bad_action.json()["error"]["code"] == "VALIDATION_ERROR"

    # 4. Invalid payload: promote without trackId returns 400 VALIDATION_ERROR
    res_no_tid_promote = client.post(
        f"/api/v1/lounges/{lounge_id}/queue/override",
        json={"action": "promote"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_no_tid_promote.status_code == 400
    assert res_no_tid_promote.json()["error"]["code"] == "VALIDATION_ERROR"

    # 5. Invalid payload: non-positive targetPosition returns 400 VALIDATION_ERROR
    res_neg_pos = client.post(
        f"/api/v1/lounges/{lounge_id}/queue/override",
        json={"action": "promote", "trackId": "t_2002", "targetPosition": -10},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_neg_pos.status_code == 400
    assert res_neg_pos.json()["error"]["code"] == "VALIDATION_ERROR"


def test_8_persistence_and_rollback_failures(client: TestClient):
    """Test 8: Database commit failure rolls back transaction and in-memory state cleanly."""
    host_token = _login_as(client, "host@spotify-intern.local")
    lounge_id, _ = _create_lounge(client, host_token)

    # Add initial track successfully
    res1 = client.post(
        f"/api/v1/lounges/{lounge_id}/queue",
        json={"trackId": "t_2002"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res1.status_code == 201
    assert len(res1.json()["queue"]) == 1

    # Simulate database commit failure during second add_track
    with patch("sqlalchemy.ext.asyncio.AsyncSession.commit", side_effect=RuntimeError("Simulated DB Crash")):
        res_fail = client.post(
            f"/api/v1/lounges/{lounge_id}/queue",
            json={"trackId": "t_2003"},
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert res_fail.status_code == 500
        assert res_fail.json()["error"]["code"] == "PERSISTENCE_FAILED"

    # Confirm next request succeeds and in-memory state was cleanly restored to 1 track
    res_get = client.get(
        f"/api/v1/lounges/{lounge_id}/queue",
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_get.status_code == 200
    assert len(res_get.json()["queue"]) == 1
    assert res_get.json()["queue"][0]["trackId"] == "t_2002"

    # Next valid add succeeds cleanly
    res_recover = client.post(
        f"/api/v1/lounges/{lounge_id}/queue",
        json={"trackId": "t_2003"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_recover.status_code == 201
    assert len(res_recover.json()["queue"]) == 2


def test_9_global_codj_as_lounge_listener_cannot_override(client: TestClient):
    """Test 9: A globally assigned Co-DJ who is only a listener in the target lounge must receive 403 on promote, remove, and force_segue."""
    host_token = _login_as(client, "host@spotify-intern.local")
    codj_token = _login_as(client, "codj@spotify-intern.local")
    lounge_id, invite_code = _create_lounge(client, host_token)
    _join_lounge(client, invite_code, codj_token)

    # 1. Verify global role is co_dj
    me_res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {codj_token}"})
    assert me_res.status_code == 200
    assert me_res.json()["role"] == "co_dj"

    # 2. Host moderates u_503 (global co_dj) to be explicitly 'listener' in this lounge
    res_set_role = client.post(
        f"/api/v1/lounges/{lounge_id}/moderation",
        json={"action": "set_role", "targetUserId": "u_503", "newRole": "listener"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_set_role.status_code == 200
    assert res_set_role.json()["moderationState"]["participantRoles"]["u_503"] == "listener"

    # 3. Add tracks so valid targets exist for queue actions
    client.post(f"/api/v1/lounges/{lounge_id}/queue", json={"trackId": "t_2002"}, headers={"Authorization": f"Bearer {host_token}"})
    client.post(f"/api/v1/lounges/{lounge_id}/queue", json={"trackId": "t_2003"}, headers={"Authorization": f"Bearer {host_token}"})

    # 4. Attempt promote -> 403 Forbidden
    res_promote = client.post(
        f"/api/v1/lounges/{lounge_id}/queue/override",
        json={"action": "promote", "trackId": "t_2003"},
        headers={"Authorization": f"Bearer {codj_token}"},
    )
    assert res_promote.status_code == 403
    assert res_promote.json()["error"]["code"] == "FORBIDDEN"

    # 5. Attempt remove -> 403 Forbidden
    res_remove = client.post(
        f"/api/v1/lounges/{lounge_id}/queue/override",
        json={"action": "remove", "trackId": "t_2003"},
        headers={"Authorization": f"Bearer {codj_token}"},
    )
    assert res_remove.status_code == 403
    assert res_remove.json()["error"]["code"] == "FORBIDDEN"

    # 6. Attempt force_segue -> 403 Forbidden
    res_fs = client.post(
        f"/api/v1/lounges/{lounge_id}/queue/override",
        json={"action": "force_segue"},
        headers={"Authorization": f"Bearer {codj_token}"},
    )
    assert res_fs.status_code == 403
    assert res_fs.json()["error"]["code"] == "FORBIDDEN"

    # 7. Host explicitly promotes u_503 to 'co_dj' in this lounge -> now override succeeds
    res_promote_lounge = client.post(
        f"/api/v1/lounges/{lounge_id}/moderation",
        json={"action": "set_role", "targetUserId": "u_503", "newRole": "co_dj"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_promote_lounge.status_code == 200
    assert res_promote_lounge.json()["moderationState"]["participantRoles"]["u_503"] == "co_dj"

    res_allowed = client.post(
        f"/api/v1/lounges/{lounge_id}/queue/override",
        json={"action": "promote", "trackId": "t_2003"},
        headers={"Authorization": f"Bearer {codj_token}"},
    )
    assert res_allowed.status_code == 200
    assert [e["trackId"] for e in res_allowed.json()["queue"]] == ["t_2003", "t_2002"]
