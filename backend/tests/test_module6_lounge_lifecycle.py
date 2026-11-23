"""Module 5/6 automated backend tests: DJ Lounge creation (PRD Section 7.3.1), invite-code joining, capacity & lock enforcement, lifecycle (start/end), Host moderation (mute, remove, lock, set_role), and 4-role RBAC."""

import re

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

TEST_ACCESS_SECRET = "module6_test_access_secret_key_at_least_32_bytes_long_001"
TEST_REFRESH_SECRET = "module6_test_refresh_secret_key_at_least_32_bytes_long_002"
TEST_SEED_PASSWORD = "TestFixtureSeedPass!2026"


@pytest.fixture
async def lounge_db_engine() -> AsyncEngine:
    """Create an isolated in-memory database and seed the 4 role users and 10 tracks."""
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


@pytest.fixture
def lounge_settings() -> Settings:
    return Settings(
        _env_file=None,
        APP_NAME="AI DJ Studio Lounge Test",
        APP_ENV="testing",
        JWT_SECRET_KEY=TEST_ACCESS_SECRET,
        JWT_REFRESH_SECRET_KEY=TEST_REFRESH_SECRET,
    )


@pytest.fixture
def lounge_client(
    lounge_db_engine: AsyncEngine,
    lounge_settings: Settings,
) -> TestClient:
    app = create_app(settings_override=lounge_settings)
    return TestClient(app, raise_server_exceptions=False)


def _login_as(
    client: TestClient, email: str, password: str = TEST_SEED_PASSWORD
) -> str:
    res = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert res.status_code == 200, f"Login failed for {email}: {res.text}"
    return res.json()["accessToken"]


def _register_listener(client: TestClient, suffix: str) -> tuple[str, str]:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"guest_{suffix}@spotify-intern.local",
            "password": "GuestPassword!2026",
            "displayName": f"Guest {suffix}",
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    return body["userId"], body["accessToken"]


def test_1_exact_prd_section_7_3_1_create_lounge_contract_and_rbac(
    lounge_client: TestClient,
) -> None:
    """1. Verify POST /api/v1/lounges returns 201 with exact PRD Section 7.3.1 keys for Host/Admin and 403 for Listener/Co-DJ (REQ-057, REQ-070)."""
    create_payload = {
        "seedTrackId": "t_2001",
        "vibeProfile": "High-Energy EDM",
        "maxParticipants": 40,
        "bpmTolerancePct": 8,
        "soundclashEnabled": True,
        "aiVerbosity": "normal",
    }

    # Listener and Co-DJ must be rejected with 403 Forbidden
    for email in (
        "listener@spotify-intern.local",
        "codj@spotify-intern.local",
    ):
        token = _login_as(lounge_client, email)
        forbidden_res = lounge_client.post(
            "/api/v1/lounges",
            json=create_payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert forbidden_res.status_code == 403
        assert forbidden_res.json()["error"]["code"] == "FORBIDDEN"

    # Host succeeds with 201 Created and exact PRD Section 7.3.1 fields
    host_token = _login_as(lounge_client, "host@spotify-intern.local")
    res = lounge_client.post(
        "/api/v1/lounges",
        json=create_payload,
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res.status_code == 201
    body = res.json()

    assert set(body.keys()) == {
        "loungeId",
        "inviteCode",
        "status",
        "hostUserId",
        "createdAt",
    }
    assert body["loungeId"].startswith("lg_")
    assert re.match(r"^SP-[A-Z0-9]{4,10}$", body["inviteCode"])
    assert body["status"] == "scheduled"
    assert body["hostUserId"] == "u_501"
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", body["createdAt"])


def test_2_lounge_creation_validation_and_unknown_seed_track(
    lounge_client: TestClient,
) -> None:
    """2. Verify lounge creation validates maxParticipants (2..50), bpmTolerancePct (>0), aiVerbosity, and seedTrackId existence."""
    host_token = _login_as(lounge_client, "host@spotify-intern.local")
    headers = {"Authorization": f"Bearer {host_token}"}

    base = {
        "seedTrackId": "t_2001",
        "vibeProfile": "High-Energy EDM",
        "maxParticipants": 40,
        "bpmTolerancePct": 8,
        "soundclashEnabled": True,
        "aiVerbosity": "normal",
    }

    # Unknown seedTrackId -> 404 TRACK_NOT_FOUND
    res_404 = lounge_client.post(
        "/api/v1/lounges",
        json={**base, "seedTrackId": "t_unknown_999"},
        headers=headers,
    )
    assert res_404.status_code == 404
    assert res_404.json()["error"]["code"] == "TRACK_NOT_FOUND"

    # Invalid maxParticipants (> 50 or < 2) -> 400 VALIDATION_ERROR
    for bad_max in (0, 1, 51, 100):
        res_max = lounge_client.post(
            "/api/v1/lounges",
            json={**base, "maxParticipants": bad_max},
            headers=headers,
        )
        assert res_max.status_code == 400

    # Invalid bpmTolerancePct (<= 0 or > 50) -> 400 VALIDATION_ERROR
    for bad_tol in (-5, 0, 55):
        res_tol = lounge_client.post(
            "/api/v1/lounges",
            json={**base, "bpmTolerancePct": bad_tol},
            headers=headers,
        )
        assert res_tol.status_code == 400

    # Invalid aiVerbosity -> 400 VALIDATION_ERROR
    res_verb = lounge_client.post(
        "/api/v1/lounges",
        json={**base, "aiVerbosity": "extreme"},
        headers=headers,
    )
    assert res_verb.status_code == 400


def test_3_join_via_invite_code_duplicate_protection_and_capacity_409(
    lounge_client: TestClient,
) -> None:
    """3. Verify joining via invite code, duplicate membership prevention, and HTTP 409 when maxParticipants capacity is reached (REQ-007, REQ-019)."""
    host_token = _login_as(lounge_client, "host@spotify-intern.local")
    listener_token = _login_as(lounge_client, "listener@spotify-intern.local")
    codj_token = _login_as(lounge_client, "codj@spotify-intern.local")

    # Create a lounge with maxParticipants = 2 (Host is participant #1)
    create_res = lounge_client.post(
        "/api/v1/lounges",
        json={
            "seedTrackId": "t_2001",
            "vibeProfile": "Synthwave Session",
            "maxParticipants": 2,
            "bpmTolerancePct": 8,
            "soundclashEnabled": True,
            "aiVerbosity": "normal",
        },
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert create_res.status_code == 201
    lounge_id = create_res.json()["loungeId"]
    invite_code = create_res.json()["inviteCode"]

    # Listener joins as participant #2 (lowercase invite code also accepted and normalized)
    join_1 = lounge_client.post(
        f"/api/v1/lounges/join/{invite_code.lower()}",
        headers={"Authorization": f"Bearer {listener_token}"},
    )
    assert join_1.status_code == 200
    assert join_1.json()["loungeId"] == lounge_id
    assert join_1.json()["participantCount"] == 2
    assert join_1.json()["snapshot"]["nowPlaying"]["trackId"] == "t_2001"

    # Duplicate join by the same Listener must be idempotent and keep participantCount == 2
    join_dup = lounge_client.post(
        f"/api/v1/lounges/join/{invite_code}",
        headers={"Authorization": f"Bearer {listener_token}"},
    )
    assert join_dup.status_code == 200
    assert join_dup.json()["participantCount"] == 2

    # Third user (Co-DJ) attempting to join when room is at capacity (2/2) receives 409 Conflict
    join_full = lounge_client.post(
        f"/api/v1/lounges/join/{invite_code}",
        headers={"Authorization": f"Bearer {codj_token}"},
    )
    assert join_full.status_code == 409
    assert join_full.json()["error"]["code"] == "LOUNGE_FULL"

    # Malformed invite code returns 400 and unknown valid-format invite code returns 404
    bad_code_res = lounge_client.post(
        "/api/v1/lounges/join/INVALID_CODE!",
        headers={"Authorization": f"Bearer {listener_token}"},
    )
    assert bad_code_res.status_code == 400

    unknown_code_res = lounge_client.post(
        "/api/v1/lounges/join/SP-ZZZZ",
        headers={"Authorization": f"Bearer {listener_token}"},
    )
    assert unknown_code_res.status_code == 404


def test_4_host_moderation_mute_remove_lock_and_set_role(
    lounge_client: TestClient,
) -> None:
    """4. Verify Host moderation actions (mute, remove, lock, set_role) and 403 rejection for non-Host callers (REQ-025, REQ-070)."""
    host_token = _login_as(lounge_client, "host@spotify-intern.local")
    listener_token = _login_as(lounge_client, "listener@spotify-intern.local")
    codj_token = _login_as(lounge_client, "codj@spotify-intern.local")
    _, guest_token = _register_listener(lounge_client, "modtest")

    create_res = lounge_client.post(
        "/api/v1/lounges",
        json={
            "seedTrackId": "t_2001",
            "vibeProfile": "Techno Bunker",
            "maxParticipants": 10,
            "bpmTolerancePct": 8,
            "soundclashEnabled": True,
            "aiVerbosity": "high",
        },
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert create_res.status_code == 201
    lounge_id = create_res.json()["loungeId"]
    invite_code = create_res.json()["inviteCode"]

    # Listener (u_502) and Co-DJ (u_503) join the lounge
    assert (
        lounge_client.post(
            f"/api/v1/lounges/join/{invite_code}",
            headers={"Authorization": f"Bearer {listener_token}"},
        ).status_code
        == 200
    )
    assert (
        lounge_client.post(
            f"/api/v1/lounges/join/{invite_code}",
            headers={"Authorization": f"Bearer {codj_token}"},
        ).status_code
        == 200
    )

    # Non-host (listener and co_dj) attempting moderation must fail with 403 Forbidden
    for non_host_tok in (listener_token, codj_token):
        res_forbidden = lounge_client.post(
            f"/api/v1/lounges/{lounge_id}/moderation",
            json={"action": "lock"},
            headers={"Authorization": f"Bearer {non_host_tok}"},
        )
        assert res_forbidden.status_code == 403

    # 1. Host mutes u_502
    mute_res = lounge_client.post(
        f"/api/v1/lounges/{lounge_id}/moderation",
        json={"action": "mute", "targetUserId": "u_502"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert mute_res.status_code == 200
    assert "u_502" in mute_res.json()["moderationState"]["mutedUserIds"]

    # 2. Host locks the lounge -> new guest attempting to join receives 403 LOUNGE_LOCKED
    lock_res = lounge_client.post(
        f"/api/v1/lounges/{lounge_id}/moderation",
        json={"action": "lock"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert lock_res.status_code == 200
    assert lock_res.json()["moderationState"]["isLocked"] is True

    locked_join = lounge_client.post(
        f"/api/v1/lounges/join/{invite_code}",
        headers={"Authorization": f"Bearer {guest_token}"},
    )
    assert locked_join.status_code == 403
    assert locked_join.json()["error"]["code"] == "LOUNGE_LOCKED"

    # Unlocking the lounge allows guest to join
    unlock_res = lounge_client.post(
        f"/api/v1/lounges/{lounge_id}/moderation",
        json={"action": "unlock"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert unlock_res.status_code == 200
    assert unlock_res.json()["moderationState"]["isLocked"] is False

    # 3. Host removes u_502 -> u_502 is removed from active participants and cannot rejoin (403 USER_REMOVED)
    remove_res = lounge_client.post(
        f"/api/v1/lounges/{lounge_id}/moderation",
        json={"action": "remove", "targetUserId": "u_502"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert remove_res.status_code == 200
    assert "u_502" in remove_res.json()["moderationState"]["removedUserIds"]

    kicked_rejoin = lounge_client.post(
        f"/api/v1/lounges/join/{invite_code}",
        headers={"Authorization": f"Bearer {listener_token}"},
    )
    assert kicked_rejoin.status_code == 403
    assert kicked_rejoin.json()["error"]["code"] == "USER_REMOVED"

    # 4. Host uses set_role to promote u_503 to 'host' (role transfer)
    role_res = lounge_client.post(
        f"/api/v1/lounges/{lounge_id}/moderation",
        json={"action": "set_role", "targetUserId": "u_503", "newRole": "host"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert role_res.status_code == 200
    assert role_res.json()["moderationState"]["hostUserId"] == "u_503"
    assert (
        role_res.json()["moderationState"]["participantRoles"]["u_503"] == "host"
    )


def test_5_lounge_lifecycle_start_end_list_and_multi_room_isolation(
    lounge_client: TestClient,
) -> None:
    """5. Verify lounge start -> live, end -> ended with summary, listing, and multi-room state isolation (REQ-015, REQ-076)."""
    host_token = _login_as(lounge_client, "host@spotify-intern.local")
    listener_token = _login_as(lounge_client, "listener@spotify-intern.local")

    # Create two concurrent lounges (Lounge A and Lounge B)
    res_a = lounge_client.post(
        "/api/v1/lounges",
        json={
            "seedTrackId": "t_2001",
            "vibeProfile": "Lounge A EDM",
            "maxParticipants": 25,
            "bpmTolerancePct": 8,
            "soundclashEnabled": True,
            "aiVerbosity": "normal",
        },
        headers={"Authorization": f"Bearer {host_token}"},
    )
    res_b = lounge_client.post(
        "/api/v1/lounges",
        json={
            "seedTrackId": "t_2009",
            "vibeProfile": "Lounge B Lo-Fi Chill",
            "maxParticipants": 15,
            "bpmTolerancePct": 6,
            "soundclashEnabled": False,
            "aiVerbosity": "low",
        },
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_a.status_code == 201 and res_b.status_code == 201
    lounge_a_id = res_a.json()["loungeId"]
    lounge_b_id = res_b.json()["loungeId"]

    # Listener joins only Lounge A
    lounge_client.post(
        f"/api/v1/lounges/join/{res_a.json()['inviteCode']}",
        headers={"Authorization": f"Bearer {listener_token}"},
    )

    # Listener cannot start or end Lounge A (403 Forbidden)
    assert (
        lounge_client.post(
            f"/api/v1/lounges/{lounge_a_id}/start",
            headers={"Authorization": f"Bearer {listener_token}"},
        ).status_code
        == 403
    )
    assert (
        lounge_client.post(
            f"/api/v1/lounges/{lounge_a_id}/end",
            headers={"Authorization": f"Bearer {listener_token}"},
        ).status_code
        == 403
    )

    # Host starts Lounge A -> status transitions to 'live' while Lounge B remains 'scheduled'
    start_res = lounge_client.post(
        f"/api/v1/lounges/{lounge_a_id}/start",
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert start_res.status_code == 200
    assert start_res.json() == {
        "loungeId": lounge_a_id,
        "status": "live",
        "currentTrackId": "t_2001",
    }

    detail_b = lounge_client.get(
        f"/api/v1/lounges/{lounge_b_id}",
        headers={"Authorization": f"Bearer {listener_token}"},
    )
    assert detail_b.status_code == 200
    assert detail_b.json()["status"] == "scheduled"
    assert detail_b.json()["participantCount"] == 1

    # Host ends Lounge A -> status transitions to 'ended' with summary
    end_res = lounge_client.post(
        f"/api/v1/lounges/{lounge_a_id}/end",
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert end_res.status_code == 200
    end_body = end_res.json()
    assert end_body["loungeId"] == lounge_a_id
    assert end_body["status"] == "ended"
    assert end_body["summary"]["peakConcurrentParticipants"] == 2

    # List lounges verifies both lounges exist with distinct states
    list_res = lounge_client.get(
        "/api/v1/lounges",
        headers={"Authorization": f"Bearer {listener_token}"},
    )
    assert list_res.status_code == 200
    assert list_res.json()["total"] == 2


def test_6_frontend_foundation_and_api_contract_wiring_verification() -> None:
    """6. Verify React + Vite frontend files exist and wire exact backend API contracts without logging secrets."""
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[2]
    frontend_dir = project_root / "frontend"

    required_files = [
        frontend_dir / "package.json",
        frontend_dir / "vite.config.js",
        frontend_dir / "index.html",
        frontend_dir / "src" / "main.jsx",
        frontend_dir / "src" / "App.jsx",
        frontend_dir / "src" / "index.css",
        frontend_dir / "src" / "api" / "httpClient.js",
        frontend_dir / "src" / "api" / "httpClient.test.js",
        frontend_dir / "src" / "context" / "AuthContext.jsx",
        frontend_dir / "src" / "pages" / "LoginPage.jsx",
        frontend_dir / "src" / "pages" / "CatalogPage.jsx",
        frontend_dir / "src" / "pages" / "AdminUploadPage.jsx",
        frontend_dir / "src" / "pages" / "LoungeLobbyPage.jsx",
        frontend_dir / "src" / "pages" / "LoungeWarRoomPage.jsx",
        frontend_dir / "src" / "components" / "lounge" / "HostModerationBar.jsx",
    ]
    for file_path in required_files:
        assert file_path.is_file(), f"Missing required frontend file: {file_path}"

    http_client_src = (frontend_dir / "src" / "api" / "httpClient.js").read_text(
        encoding="utf-8"
    )
    assert "/api/v1/auth/refresh" in http_client_src
    assert "activeRefreshPromise" in http_client_src
    assert "_isRetry" in http_client_src
    assert "Bearer " in http_client_src
    assert "console.log" not in http_client_src

    catalog_src = (frontend_dir / "src" / "pages" / "CatalogPage.jsx").read_text(
        encoding="utf-8"
    )
    assert "/api/v1/tracks" in catalog_src
    assert "/acoustic-profile" in catalog_src

    admin_src = (frontend_dir / "src" / "pages" / "AdminUploadPage.jsx").read_text(
        encoding="utf-8"
    )
    assert "/api/v1/admin/tracks" in admin_src

    lobby_src = (frontend_dir / "src" / "pages" / "LoungeLobbyPage.jsx").read_text(
        encoding="utf-8"
    )
    assert "/api/v1/lounges" in lobby_src
    assert "/api/v1/lounges/join/" in lobby_src

    mod_src = (
        frontend_dir / "src" / "components" / "lounge" / "HostModerationBar.jsx"
    ).read_text(encoding="utf-8")
    assert "/moderation" in mod_src
    assert "/start" in mod_src
    assert "/end" in mod_src


def test_7_m5_02_lounge_scoped_roles_and_moderation_security(
    lounge_client: TestClient,
) -> None:
    """7. M5-02: Verify lounge-scoped role isolation, non-mutation of global User.role, promoted host authority, host-of-other-lounge 403, and moderation protections (CANNOT_MODERATE_ADMIN, CANNOT_MODERATE_HOST, PARTICIPANT_NOT_IN_LOUNGE)."""
    admin_token = _login_as(lounge_client, "admin@spotify-intern.local")
    host1_token = _login_as(lounge_client, "host@spotify-intern.local")
    listener_token = _login_as(lounge_client, "listener@spotify-intern.local")
    guest_id, _ = _register_listener(lounge_client, "m5_02_outsider")

    # 1. Host creates Lounge 1
    res1 = lounge_client.post(
        "/api/v1/lounges",
        json={
            "seedTrackId": "t_2001",
            "vibeProfile": "Lounge 1 Host Scope Test",
            "maxParticipants": 10,
            "bpmTolerancePct": 8,
            "soundclashEnabled": True,
            "aiVerbosity": "normal",
        },
        headers={"Authorization": f"Bearer {host1_token}"},
    )
    assert res1.status_code == 201
    lounge1_id = res1.json()["loungeId"]
    invite1 = res1.json()["inviteCode"]

    # Listener (u_502) joins Lounge 1
    join_res = lounge_client.post(
        f"/api/v1/lounges/join/{invite1}",
        headers={"Authorization": f"Bearer {listener_token}"},
    )
    assert join_res.status_code == 200

    # 2. Host promotes u_502 to 'co_dj' in Lounge 1
    set_codj_res = lounge_client.post(
        f"/api/v1/lounges/{lounge1_id}/moderation",
        json={"action": "set_role", "targetUserId": "u_502", "newRole": "co_dj"},
        headers={"Authorization": f"Bearer {host1_token}"},
    )
    assert set_codj_res.status_code == 200
    assert (
        set_codj_res.json()["moderationState"]["participantRoles"]["u_502"]
        == "co_dj"
    )

    # CRUCIAL CHECK: Global users.role for u_502 MUST NOT be mutated!
    me_res = lounge_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {listener_token}"},
    )
    assert me_res.status_code == 200
    assert (
        me_res.json()["role"] == "listener"
    ), f"Global role was mutated: {me_res.json()}"

    # 3. Host promotes u_502 to 'host' in Lounge 1 (room ownership transfer)
    set_host_res = lounge_client.post(
        f"/api/v1/lounges/{lounge1_id}/moderation",
        json={"action": "set_role", "targetUserId": "u_502", "newRole": "host"},
        headers={"Authorization": f"Bearer {host1_token}"},
    )
    assert set_host_res.status_code == 200
    mod_state = set_host_res.json()["moderationState"]
    assert mod_state["hostUserId"] == "u_502"
    assert mod_state["participantRoles"]["u_502"] == "host"
    assert mod_state["participantRoles"]["u_501"] == "co_dj"

    # u_502's global role remains 'listener'
    me_res2 = lounge_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {listener_token}"},
    )
    assert me_res2.status_code == 200
    assert me_res2.json()["role"] == "listener"

    # Promoted host (u_502) now has host authority over Lounge 1 (e.g. lock/start/end)
    lock_by_promoted = lounge_client.post(
        f"/api/v1/lounges/{lounge1_id}/moderation",
        json={"action": "lock"},
        headers={"Authorization": f"Bearer {listener_token}"},
    )
    assert lock_by_promoted.status_code == 200
    assert lock_by_promoted.json()["moderationState"]["isLocked"] is True

    # 4. Cross-lounge host isolation:
    # Former host u_501 (global 'host') is no longer the host of Lounge 1 and has no moderation power on Lounge 1
    res_former_host = lounge_client.post(
        f"/api/v1/lounges/{lounge1_id}/moderation",
        json={"action": "unlock"},
        headers={"Authorization": f"Bearer {host1_token}"},
    )
    assert res_former_host.status_code == 403
    assert res_former_host.json()["error"]["code"] == "FORBIDDEN"

    # Former host cannot start or end Lounge 1
    assert (
        lounge_client.post(
            f"/api/v1/lounges/{lounge1_id}/start",
            headers={"Authorization": f"Bearer {host1_token}"},
        ).status_code
        == 403
    )
    assert (
        lounge_client.post(
            f"/api/v1/lounges/{lounge1_id}/end",
            headers={"Authorization": f"Bearer {host1_token}"},
        ).status_code
        == 403
    )

    # 5. Platform admin bypass: Admin can moderate Lounge 1 even without being the host
    admin_mod = lounge_client.post(
        f"/api/v1/lounges/{lounge1_id}/moderation",
        json={"action": "unlock"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert admin_mod.status_code == 200
    assert admin_mod.json()["moderationState"]["isLocked"] is False

    # 6. Moderation restrictions:
    # A. Moderating non-participant -> 400 PARTICIPANT_NOT_IN_LOUNGE
    non_part_res = lounge_client.post(
        f"/api/v1/lounges/{lounge1_id}/moderation",
        json={"action": "mute", "targetUserId": guest_id},
        headers={"Authorization": f"Bearer {listener_token}"},
    )
    assert non_part_res.status_code == 400
    assert non_part_res.json()["error"]["code"] == "PARTICIPANT_NOT_IN_LOUNGE"

    # B. Moderating platform admin -> 400 CANNOT_MODERATE_ADMIN
    # Admin joins Lounge 1 first
    lounge_client.post(
        f"/api/v1/lounges/join/{invite1}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    mod_admin_res = lounge_client.post(
        f"/api/v1/lounges/{lounge1_id}/moderation",
        json={"action": "mute", "targetUserId": "u_504"},
        headers={"Authorization": f"Bearer {listener_token}"},
    )
    assert mod_admin_res.status_code == 400
    assert mod_admin_res.json()["error"]["code"] == "CANNOT_MODERATE_ADMIN"

    # C. Moderating the owning host (u_502) -> 400 CANNOT_MODERATE_HOST
    mod_host_mute = lounge_client.post(
        f"/api/v1/lounges/{lounge1_id}/moderation",
        json={"action": "mute", "targetUserId": "u_502"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert mod_host_mute.status_code == 400
    assert mod_host_mute.json()["error"]["code"] == "CANNOT_MODERATE_HOST"

    mod_host_remove = lounge_client.post(
        f"/api/v1/lounges/{lounge1_id}/moderation",
        json={"action": "remove", "targetUserId": "u_502"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert mod_host_remove.status_code == 400
    assert mod_host_remove.json()["error"]["code"] == "CANNOT_MODERATE_HOST"


@pytest.mark.asyncio
async def test_8_m5_03_concurrent_joins_and_capacity_row_locking(
    lounge_db_engine: AsyncEngine,
    lounge_settings: Settings,
) -> None:
    """8. M5-03: Concurrency and capacity verification using asyncio.gather.

    Simulates concurrent join requests from distinct users when only 1 capacity slot remains:
    - Exactly 1 request succeeds with 200 OK.
    - Remaining concurrent requests receive 409 Conflict with code 'LOUNGE_FULL'.
    - Final active participant count exactly matches maxParticipants.
    - Simultaneous duplicate joins by the same user do not duplicate membership.

    NOTE ON DATABASE CONCURRENCY:
    In this test environment, SQLite with StaticPool serializes execution via Python GIL and
    database connection locking. In production PostgreSQL deployments, the service layer executes
    'SELECT ... FOR UPDATE' row-level locks on the DJLounge row inside the transaction alongside
    per-room asyncio locks, serializing concurrent transactions at the database engine level (REQ-076).
    """
    import asyncio
    from httpx import ASGITransport, AsyncClient

    app = create_app(settings_override=lounge_settings)
    transport = ASGITransport(app=app)

    async with AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        # Login host and create lounge with maxParticipants = 3
        host_login = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "host@spotify-intern.local",
                "password": TEST_SEED_PASSWORD,
            },
        )
        assert host_login.status_code == 200
        host_tok = host_login.json()["accessToken"]

        create_res = await client.post(
            "/api/v1/lounges",
            json={
                "seedTrackId": "t_2001",
                "vibeProfile": "Concurrency Capacity Test",
                "maxParticipants": 3,
                "bpmTolerancePct": 8,
                "soundclashEnabled": True,
                "aiVerbosity": "normal",
            },
            headers={"Authorization": f"Bearer {host_tok}"},
        )
        assert create_res.status_code == 201
        lounge_id = create_res.json()["loungeId"]
        invite_code = create_res.json()["inviteCode"]

        # Slot 1: Host is member 1.
        # Slot 2: Listener joins as member 2.
        listener_login = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "listener@spotify-intern.local",
                "password": TEST_SEED_PASSWORD,
            },
        )
        assert listener_login.status_code == 200
        listener_tok = listener_login.json()["accessToken"]

        join_listener = await client.post(
            f"/api/v1/lounges/join/{invite_code}",
            headers={"Authorization": f"Bearer {listener_tok}"},
        )
        assert join_listener.status_code == 200
        assert join_listener.json()["participantCount"] == 2

        # 1 slot remains (maxParticipants = 3, current = 2).
        # Register 4 distinct users:
        competitor_tokens = []
        for i in range(4):
            reg = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": f"competitor_{i}_{lounge_id}@spotify-intern.local",
                    "password": "CompetitorPass!2026",
                    "displayName": f"Competitor {i}",
                },
            )
            assert reg.status_code == 201
            competitor_tokens.append(reg.json()["accessToken"])

        # Execute 4 concurrent join requests simultaneously via asyncio.gather
        tasks = [
            client.post(
                f"/api/v1/lounges/join/{invite_code}",
                headers={"Authorization": f"Bearer {tok}"},
            )
            for tok in competitor_tokens
        ]
        results = await asyncio.gather(*tasks)

        status_codes = [r.status_code for r in results]
        success_count = status_codes.count(200)
        full_count = status_codes.count(409)

        assert (
            success_count == 1
        ), f"Expected exactly 1 successful join, got {success_count}. Statuses: {status_codes}"
        assert (
            full_count == 3
        ), f"Expected 3 LOUNGE_FULL conflicts, got {full_count}. Statuses: {status_codes}"

        for r in results:
            if r.status_code == 409:
                assert r.json()["error"]["code"] == "LOUNGE_FULL"

        # Verify lounge state has participantCount == 3
        detail_res = await client.get(
            f"/api/v1/lounges/{lounge_id}",
            headers={"Authorization": f"Bearer {host_tok}"},
        )
        assert detail_res.status_code == 200
        assert detail_res.json()["participantCount"] == 3

        # Simultaneous duplicate joins by the winning user must all succeed idempotently without duplicating membership
        winning_tok = [
            tok
            for tok, r in zip(competitor_tokens, results)
            if r.status_code == 200
        ][0]

        dup_tasks = [
            client.post(
                f"/api/v1/lounges/join/{invite_code}",
                headers={"Authorization": f"Bearer {winning_tok}"},
            )
            for _ in range(3)
        ]
        dup_results = await asyncio.gather(*dup_tasks)
        for dr in dup_results:
            assert dr.status_code == 200
            assert dr.json()["participantCount"] == 3

        # Final check: total participants still exactly 3
        final_detail = await client.get(
            f"/api/v1/lounges/{lounge_id}",
            headers={"Authorization": f"Bearer {host_tok}"},
        )
        assert final_detail.status_code == 200
        assert final_detail.json()["participantCount"] == 3


def test_9_m5_04_lounge_listing_validation_and_batching(
    lounge_client: TestClient,
) -> None:
    """9. M5-04: Verify lounge listing status_filter validation (400 INVALID_LOUNGE_STATUS for unknown statuses) and batch metadata reconstruction."""
    host_token = _login_as(lounge_client, "host@spotify-intern.local")
    listener_token = _login_as(lounge_client, "listener@spotify-intern.local")

    # 1. Invalid status_filter returns 400 INVALID_LOUNGE_STATUS
    for bad_status in ("invalid", "pending", "active", "deleted", "foo"):
        res_bad = lounge_client.get(
            f"/api/v1/lounges?status={bad_status}",
            headers={"Authorization": f"Bearer {listener_token}"},
        )
        assert res_bad.status_code == 400
        assert res_bad.json()["error"]["code"] == "INVALID_LOUNGE_STATUS"

    # 2. Valid status filters return 200 OK (including case-insensitivity)
    for valid_status in (
        "scheduled",
        "live",
        "ended",
        "SCHEDULED",
        "Live",
        "ENDED",
    ):
        res_valid = lounge_client.get(
            f"/api/v1/lounges?status={valid_status}",
            headers={"Authorization": f"Bearer {listener_token}"},
        )
        assert res_valid.status_code == 200
        body = res_valid.json()
        assert isinstance(body["lounges"], list)
        for lounge in body["lounges"]:
            assert lounge["status"].lower() == valid_status.strip().lower()

    # 3. Create multiple lounges and verify batching returns correct counts and host metadata
    # Lounge A: scheduled, 1 participant (host)
    res_a = lounge_client.post(
        "/api/v1/lounges",
        json={
            "seedTrackId": "t_2001",
            "vibeProfile": "Batch Test Scheduled",
            "maxParticipants": 20,
            "bpmTolerancePct": 8,
            "soundclashEnabled": True,
            "aiVerbosity": "normal",
        },
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_a.status_code == 201
    lounge_a_id = res_a.json()["loungeId"]

    # Lounge B: live, 2 participants (host + listener)
    res_b = lounge_client.post(
        "/api/v1/lounges",
        json={
            "seedTrackId": "t_2002",
            "vibeProfile": "Batch Test Live",
            "maxParticipants": 20,
            "bpmTolerancePct": 8,
            "soundclashEnabled": False,
            "aiVerbosity": "low",
        },
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_b.status_code == 201
    lounge_b_id = res_b.json()["loungeId"]
    invite_b = res_b.json()["inviteCode"]

    # Listener joins Lounge B
    lounge_client.post(
        f"/api/v1/lounges/join/{invite_b}",
        headers={"Authorization": f"Bearer {listener_token}"},
    )
    # Start Lounge B -> transitions to 'live'
    lounge_client.post(
        f"/api/v1/lounges/{lounge_b_id}/start",
        headers={"Authorization": f"Bearer {host_token}"},
    )

    # Lounge C: ended
    res_c = lounge_client.post(
        "/api/v1/lounges",
        json={
            "seedTrackId": "t_2003",
            "vibeProfile": "Batch Test Ended",
            "maxParticipants": 20,
            "bpmTolerancePct": 8,
            "soundclashEnabled": True,
            "aiVerbosity": "high",
        },
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res_c.status_code == 201
    lounge_c_id = res_c.json()["loungeId"]
    lounge_client.post(
        f"/api/v1/lounges/{lounge_c_id}/end",
        headers={"Authorization": f"Bearer {host_token}"},
    )

    # List all lounges without filter
    list_all = lounge_client.get(
        "/api/v1/lounges",
        headers={"Authorization": f"Bearer {listener_token}"},
    )
    assert list_all.status_code == 200
    all_lounges = {l["loungeId"]: l for l in list_all.json()["lounges"]}

    assert lounge_a_id in all_lounges
    assert all_lounges[lounge_a_id]["status"] == "scheduled"
    assert all_lounges[lounge_a_id]["participantCount"] == 1
    assert all_lounges[lounge_a_id]["nowPlayingTitle"] == "Night Drive"
    assert all_lounges[lounge_a_id]["hostDisplayName"] == "DJ Nova"

    assert lounge_b_id in all_lounges
    assert all_lounges[lounge_b_id]["status"] == "live"
    assert all_lounges[lounge_b_id]["participantCount"] == 2
    assert all_lounges[lounge_b_id]["nowPlayingTitle"] == "Subzero Drift"

    assert lounge_c_id in all_lounges
    assert all_lounges[lounge_c_id]["status"] == "ended"
    assert all_lounges[lounge_c_id]["endedAt"] is not None
