"""Module 4 automated tests: Track Catalog, Mandatory PRD Section 7.3.2 Acoustic Profile Contract, Admin Upload Validation & RBAC, Static .wav Audio Serving, Idempotent Seeding, Security Remediations M4-01..M4-04, and Startup Lifecycle Verification."""

import importlib
from pathlib import Path
import wave

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.db.session import (
    configure_database,
    create_session_maker,
    drop_db,
    init_db,
)
import app.main as main_module
from app.main import create_app
from app.models.track import Track, TrackTimelineEvent, VALID_CAMELOT_KEYS
from app.models.user import User
import app.seeds.seed_data as seed_data_module
from app.seeds.seed_data import (
    AUDIO_ASSETS_DIR,
    ensure_audio_assets,
    run_cli_seed,
    seed_database,
)

TEST_ACCESS_SECRET = "module4_test_access_secret_key_at_least_32_bytes_long_001"
TEST_REFRESH_SECRET = "module4_test_refresh_secret_key_at_least_32_bytes_long_002"
TEST_SEED_PASSWORD = "TestFixtureSeedPass!2026"


@pytest.fixture
async def seeded_db_engine() -> AsyncEngine:
    """Create an isolated in-memory database and run the Module 4 seed script."""
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
def mod4_settings() -> Settings:
    return Settings(
        _env_file=None,
        APP_NAME="AI DJ Studio Module 4 Test",
        APP_ENV="testing",
        JWT_SECRET_KEY=TEST_ACCESS_SECRET,
        JWT_REFRESH_SECRET_KEY=TEST_REFRESH_SECRET,
    )


@pytest.fixture
def mod4_client(
    seeded_db_engine: AsyncEngine,
    mod4_settings: Settings,
) -> TestClient:
    app = create_app(settings_override=mod4_settings)
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


def test_1_exact_prd_section_7_3_2_acoustic_profile_contract(
    mod4_client: TestClient,
) -> None:
    """1. Verify GET /api/v1/tracks/t_2001/acoustic-profile matches PRD Section 7.3.2 verbatim (REQ-058)."""
    token = _login_as(mod4_client, "listener@spotify-intern.local")
    response = mod4_client.get(
        "/api/v1/tracks/t_2001/acoustic-profile",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200

    expected_prd_contract = {
        "trackId": "t_2001",
        "title": "Night Drive",
        "artist": "Nova Circuit",
        "acoustic": {
            "bpm": 124,
            "camelotKey": "8A",
            "energy": 0.78,
            "valence": 0.65,
        },
        "timeline": [
            {
                "ts": 12,
                "type": "sample_credit",
                "text": "Vocal chop sampled from a 1981 soul record.",
            }
        ],
    }
    assert response.json() == expected_prd_contract


def test_2_track_catalog_listing_and_filtering(mod4_client: TestClient) -> None:
    """2. Verify GET /api/v1/tracks lists all 10 seeded tracks and supports Camelot key & BPM filtering."""
    token = _login_as(mod4_client, "listener@spotify-intern.local")
    headers = {"Authorization": f"Bearer {token}"}

    res = mod4_client.get("/api/v1/tracks", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 10
    assert len(body["tracks"]) == 10
    track_ids = [t["trackId"] for t in body["tracks"]]
    assert track_ids == [f"t_{num}" for num in range(2001, 2011)]

    # Filter by camelotKey=8A (t_2001 and t_2005)
    res_8a = mod4_client.get("/api/v1/tracks?camelotKey=8A", headers=headers)
    assert res_8a.status_code == 200
    assert [t["trackId"] for t in res_8a.json()["tracks"]] == ["t_2001", "t_2005"]

    # Filter by BPM range 120..126
    res_bpm = mod4_client.get(
        "/api/v1/tracks?minBpm=120&maxBpm=126", headers=headers
    )
    assert res_bpm.status_code == 200
    for item in res_bpm.json()["tracks"]:
        assert 120 <= item["acoustic"]["bpm"] <= 126


def test_3_and_4_admin_upload_succeeds_201_and_non_admins_fail_403(
    mod4_client: TestClient,
) -> None:
    """3 & 4. Verify Admin upload returns 201 and persists profile, while listener, co_dj, and host receive 403 (REQ-046, REQ-070, REQ-094)."""
    valid_payload = {
        "trackId": "t_3001",
        "title": "Quantum Groove",
        "artist": "Hyperion",
        "durationSeconds": 200,
        "audioAssetUrl": "/assets/audio/t_2001.wav",
        "acoustic": {
            "bpm": 125,
            "camelotKey": "8A",
            "energy": 0.84,
            "valence": 0.73,
        },
        "timeline": [
            {
                "id": "evt_9001",
                "ts": 16,
                "type": "sample_credit",
                "text": "Chord stab resampled from a 1991 Detroit techno dub.",
            },
            {
                "id": "evt_9002",
                "ts": 64,
                "type": "trivia",
                "text": "Mixed live in one take on a rotary console.",
            },
        ],
    }

    # Test 4: listener, co_dj, and host must be rejected with HTTP 403
    for non_admin_email in (
        "listener@spotify-intern.local",
        "codj@spotify-intern.local",
        "host@spotify-intern.local",
    ):
        non_admin_token = _login_as(mod4_client, non_admin_email)
        forbidden_res = mod4_client.post(
            "/api/v1/admin/tracks",
            json=valid_payload,
            headers={"Authorization": f"Bearer {non_admin_token}"},
        )
        assert forbidden_res.status_code == 403
        assert forbidden_res.json()["error"]["code"] == "FORBIDDEN"

    # Test 3: admin succeeds with HTTP 201 Created
    admin_token = _login_as(mod4_client, "admin@spotify-intern.local")
    create_res = mod4_client.post(
        "/api/v1/admin/tracks",
        json=valid_payload,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create_res.status_code == 201
    created = create_res.json()
    assert created["trackId"] == "t_3001"
    assert created["acoustic"] == {
        "bpm": 125,
        "camelotKey": "8A",
        "energy": 0.84,
        "valence": 0.73,
    }
    assert len(created["timeline"]) == 2

    # Verify persisted track is retrievable via GET /api/v1/tracks/t_3001/acoustic-profile
    profile_res = mod4_client.get(
        "/api/v1/tracks/t_3001/acoustic-profile",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert profile_res.status_code == 200
    assert profile_res.json()["trackId"] == "t_3001"
    assert len(profile_res.json()["timeline"]) == 2


def test_5_6_7_8_admin_upload_validation_rejects_invalid_acoustic_and_timeline_fields(
    mod4_client: TestClient,
) -> None:
    """5, 6, 7 & 8. Verify invalid BPM (-5, 0), Camelot keys (13A, 8C), energy/valence (1.5, -0.2), and timeline types return 400 (REQ-049)."""
    admin_token = _login_as(mod4_client, "admin@spotify-intern.local")
    headers = {"Authorization": f"Bearer {admin_token}"}

    def make_payload(**overrides: object) -> dict:
        base = {
            "title": "Validation Track",
            "artist": "Validator",
            "durationSeconds": 180,
            "audioAssetUrl": "/assets/audio/t_2001.wav",
            "acoustic": {
                "bpm": 124,
                "camelotKey": "8A",
                "energy": 0.75,
                "valence": 0.60,
            },
            "timeline": [
                {
                    "ts": 10,
                    "type": "trivia",
                    "text": "Valid baseline trivia fact.",
                }
            ],
        }
        for k, v in overrides.items():
            if k in {"bpm", "camelotKey", "energy", "valence"}:
                base["acoustic"][k] = v
            else:
                base[k] = v
        return base

    # 5. Invalid BPM values (-5 and 0)
    for bad_bpm in (-5, 0, -0.01):
        res_bpm = mod4_client.post(
            "/api/v1/admin/tracks", json=make_payload(bpm=bad_bpm), headers=headers
        )
        assert res_bpm.status_code == 400
        assert res_bpm.json()["error"]["code"] == "VALIDATION_ERROR"

    # 6. Invalid Camelot keys (13A, 8C, 0A)
    for bad_key in ("13A", "8C", "0A", "12D", "invalid"):
        res_key = mod4_client.post(
            "/api/v1/admin/tracks",
            json=make_payload(camelotKey=bad_key),
            headers=headers,
        )
        assert res_key.status_code == 400
        assert res_key.json()["error"]["code"] == "VALIDATION_ERROR"

    # 7. Invalid energy (1.5, -0.1) and valence (1.2, -0.5)
    for bad_energy in (1.5, -0.1, 2.0):
        res_energy = mod4_client.post(
            "/api/v1/admin/tracks",
            json=make_payload(energy=bad_energy),
            headers=headers,
        )
        assert res_energy.status_code == 400
        assert res_energy.json()["error"]["code"] == "VALIDATION_ERROR"

    for bad_valence in (1.2, -0.5, -0.01):
        res_valence = mod4_client.post(
            "/api/v1/admin/tracks",
            json=make_payload(valence=bad_valence),
            headers=headers,
        )
        assert res_valence.status_code == 400
        assert res_valence.json()["error"]["code"] == "VALIDATION_ERROR"

    # 8. Invalid timeline event type
    bad_timeline_payload = make_payload(
        timeline=[{"ts": 10, "type": "unsupported_type", "text": "Some fact text."}]
    )
    res_tl = mod4_client.post(
        "/api/v1/admin/tracks", json=bad_timeline_payload, headers=headers
    )
    assert res_tl.status_code == 400
    assert res_tl.json()["error"]["code"] == "VALIDATION_ERROR"

    # Unexpected extra fields rejected
    extra_field_payload = make_payload(unexpectedField="forbidden")
    res_extra = mod4_client.post(
        "/api/v1/admin/tracks", json=extra_field_payload, headers=headers
    )
    assert res_extra.status_code == 400


def test_9_unknown_track_id_returns_404(mod4_client: TestClient) -> None:
    """9. Verify unknown track IDs return HTTP 404 Not Found."""
    token = _login_as(mod4_client, "listener@spotify-intern.local")
    res = mod4_client.get(
        "/api/v1/tracks/t_nonexistent_9999/acoustic-profile",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "TRACK_NOT_FOUND"


async def test_10_seed_script_is_idempotent(seeded_db_engine: AsyncEngine) -> None:
    """10. Verify running seed_database multiple times creates zero duplicates and preserves existing records."""
    session_maker = create_session_maker(seeded_db_engine)
    async with session_maker() as session:
        initial_users = (await session.execute(select(func.count(User.id)))).scalar_one()
        initial_tracks = (
            await session.execute(select(func.count(Track.id)))
        ).scalar_one()
        initial_events = (
            await session.execute(select(func.count(TrackTimelineEvent.id)))
        ).scalar_one()

        assert initial_users == 4
        assert initial_tracks == 10
        assert initial_events == 11

        # Second and third seed runs must create 0 new records
        summary_2 = await seed_database(
            session, seed_password=TEST_SEED_PASSWORD, app_env="testing"
        )
        summary_3 = await seed_database(
            session, seed_password=TEST_SEED_PASSWORD, app_env="testing"
        )
        assert summary_2 == {
            "usersCreated": 0,
            "tracksCreated": 0,
            "timelineEventsCreated": 0,
        }
        assert summary_3 == {
            "usersCreated": 0,
            "tracksCreated": 0,
            "timelineEventsCreated": 0,
        }

        final_users = (await session.execute(select(func.count(User.id)))).scalar_one()
        final_tracks = (
            await session.execute(select(func.count(Track.id)))
        ).scalar_one()
        final_events = (
            await session.execute(select(func.count(TrackTimelineEvent.id)))
        ).scalar_one()

        assert final_users == 4
        assert final_tracks == 10
        assert final_events == 11


def test_11_all_ten_wav_assets_exist_and_are_served_by_fastapi(
    mod4_client: TestClient,
) -> None:
    """11. Verify all 10 synthesized .wav audio assets exist on disk, are valid PCM WAV files, and are accessible at /assets/audio/t_2001.wav..t_2010.wav."""
    for num in range(2001, 2011):
        track_id = f"t_{num}"
        wav_path = AUDIO_ASSETS_DIR / f"{track_id}.wav"
        assert wav_path.exists(), f"Missing WAV file: {wav_path}"
        assert wav_path.stat().st_size > 1024

        with wave.open(str(wav_path), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 22050
            assert wf.getnframes() > 0

        http_res = mod4_client.get(f"/assets/audio/{track_id}.wav")
        assert http_res.status_code == 200
        assert http_res.content[:4] == b"RIFF"
        assert http_res.content[8:12] == b"WAVE"


async def test_12_seeded_accounts_tracks_relationships_and_valid_segue_chains(
    seeded_db_engine: AsyncEngine,
) -> None:
    """12. Verify seeded 4 role accounts, 10 tracks, timeline IDs starting at evt_5541, and valid harmonic/BPM segue chains (REQ-080)."""
    session_maker = create_session_maker(seeded_db_engine)
    async with session_maker() as session:
        users = (
            (await session.execute(select(User).order_by(User.id.asc())))
            .scalars()
            .all()
        )
        assert {u.role for u in users} == {"listener", "co_dj", "host", "admin"}
        assert {u.id for u in users} == {"u_501", "u_502", "u_503", "u_504"}

        tracks = (
            (
                await session.execute(
                    select(Track)
                    .options(selectinload(Track.timeline_events))
                    .order_by(Track.id.asc())
                )
            )
            .scalars()
            .all()
        )
        assert len(tracks) == 10
        tracks_by_id = {t.id: t for t in tracks}

        for t in tracks:
            assert t.bpm > 0
            assert t.camelot_key in VALID_CAMELOT_KEYS
            assert 0.0 <= t.energy <= 1.0
            assert 0.0 <= t.valence <= 1.0
            assert len(t.timeline_events) >= 1

        # Verify evt_5541 exists on t_2001
        assert tracks_by_id["t_2001"].timeline_events[0].id == "evt_5541"

        # Verify valid harmonic & BPM (±8% or half-time 0.5x) segue chain:
        t1 = tracks_by_id["t_2001"]
        t3 = tracks_by_id["t_2003"]
        t4 = tracks_by_id["t_2004"]
        t5 = tracks_by_id["t_2005"]
        t8 = tracks_by_id["t_2008"]

        assert abs(t3.bpm - t1.bpm) / t1.bpm <= 0.08
        assert t1.camelot_key == "8A" and t3.camelot_key == "9A"

        assert abs(t5.bpm - t3.bpm) / t3.bpm <= 0.08
        assert t3.camelot_key == "9A" and t5.camelot_key == "8A"

        assert abs(t4.bpm - t5.bpm) / t5.bpm <= 0.08
        assert t5.camelot_key == "8A" and t4.camelot_key == "8B"

        assert abs(t8.bpm - (0.5 * t3.bpm)) / (0.5 * t3.bpm) <= 0.08
        assert t8.camelot_key == t3.camelot_key == "9A"


async def test_m4_01_production_seed_protection_and_dev_credential_policy(
    mod4_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M4-01 & Issue 1: Verify missing SEED_USER_PASSWORD fails clearly in dev/test, configured SEED_USER_PASSWORD allows dev login, and production seeding is blocked by default."""
    monkeypatch.delenv("ALLOW_PRODUCTION_SEED", raising=False)
    monkeypatch.delenv("SEED_USER_PASSWORD", raising=False)

    engine = configure_database("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    session_maker = create_session_maker(engine)

    try:
        async with session_maker() as session:
            # 1. Missing SEED_USER_PASSWORD in development and testing stops immediately with an actionable error
            for env_name in ("development", "testing"):
                with pytest.raises(
                    RuntimeError,
                    match="SEED_USER_PASSWORD is not configured",
                ):
                    await seed_database(session, app_env=env_name)

                with pytest.raises(
                    RuntimeError,
                    match="SEED_USER_PASSWORD is not configured",
                ):
                    await run_cli_seed(app_env=env_name)

            # Confirm zero users or tracks were created when SEED_USER_PASSWORD was missing
            assert (
                await session.execute(select(func.count(User.id)))
            ).scalar_one() == 0
            assert (
                await session.execute(select(func.count(Track.id)))
            ).scalar_one() == 0

            # 2. Placeholder or too-short SEED_USER_PASSWORD is rejected in development/testing
            with pytest.raises(RuntimeError, match="placeholder"):
                await seed_database(
                    session,
                    seed_password="replace_with_dev_password",
                    app_env="development",
                )
            with pytest.raises(RuntimeError, match="at least 8 characters"):
                await seed_database(
                    session,
                    seed_password="short",
                    app_env="development",
                )

            # 3. Production seeding is blocked by default even when SEED_USER_PASSWORD is set
            monkeypatch.setenv("SEED_USER_PASSWORD", "ValidConfiguredSeedPass!2026")
            with pytest.raises(RuntimeError, match="blocked in production"):
                await seed_database(session, app_env="production")

            with pytest.raises(RuntimeError, match="blocked in production"):
                await run_cli_seed(app_env="production")

            # 4. Production override without SEED_USER_PASSWORD or with placeholder password fails
            monkeypatch.delenv("SEED_USER_PASSWORD", raising=False)
            with pytest.raises(
                RuntimeError, match="SEED_USER_PASSWORD is not configured"
            ):
                await seed_database(
                    session, app_env="production", allow_production=True
                )

            monkeypatch.setenv("ALLOW_PRODUCTION_SEED", "true")
            monkeypatch.setenv("SEED_USER_PASSWORD", "changeme")
            with pytest.raises(RuntimeError, match="placeholder"):
                await seed_database(session, app_env="production")

            # 5. Configured SEED_USER_PASSWORD in development creates accessible accounts for all 4 roles
            monkeypatch.delenv("ALLOW_PRODUCTION_SEED", raising=False)
            dev_pw = "ConfiguredDevLoginPass!2026"
            monkeypatch.setenv("SEED_USER_PASSWORD", dev_pw)
            dev_summary = await seed_database(session, app_env="development")
            assert dev_summary == {
                "usersCreated": 4,
                "tracksCreated": 10,
                "timelineEventsCreated": 11,
            }

            # Verify all 4 seeded accounts can log in via POST /api/v1/auth/login using the configured SEED_USER_PASSWORD
            app = create_app(settings_override=mod4_settings)
            client = TestClient(app, raise_server_exceptions=False)
            for email in (
                "host@spotify-intern.local",
                "listener@spotify-intern.local",
                "codj@spotify-intern.local",
                "admin@spotify-intern.local",
            ):
                token = _login_as(client, email, password=dev_pw)
                assert token

            # 6. Production override with valid SEED_USER_PASSWORD succeeds idempotently
            monkeypatch.setenv("ALLOW_PRODUCTION_SEED", "true")
            monkeypatch.setenv("SEED_USER_PASSWORD", "ExplicitProdEnvSeedPass!2026")
            prod_summary = await seed_database(session, app_env="production")
            assert prod_summary == {
                "usersCreated": 0,
                "tracksCreated": 0,
                "timelineEventsCreated": 0,
            }
    finally:
        await drop_db(engine)
        await engine.dispose()


def test_issue2_import_and_startup_have_no_audio_filesystem_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue 2: Prove importing app.main and calling create_app() in dev/test/production never generate or modify audio assets on disk."""
    monkeypatch.delenv("ALLOW_PRODUCTION_SEED", raising=False)
    synth_calls: list[Path] = []

    def spy_synthesize(file_path: Path, *args: object, **kwargs: object) -> None:
        synth_calls.append(file_path)

    monkeypatch.setattr(seed_data_module, "_synthesize_wav_file", spy_synthesize)

    # Record modification timestamps of all 10 existing WAV files before import/startup
    existing_mtimes = {
        wav_file.name: wav_file.stat().st_mtime_ns
        for wav_file in AUDIO_ASSETS_DIR.glob("t_20*.wav")
    }
    assert len(existing_mtimes) == 10

    # 1. Reloading/importing app.main must not invoke audio synthesis or modify any WAV files
    importlib.reload(main_module)
    assert synth_calls == []

    # 2. Calling create_app() in development and testing must not generate or modify audio files
    dev_settings = Settings(
        _env_file=None,
        APP_ENV="development",
        JWT_SECRET_KEY=TEST_ACCESS_SECRET,
        JWT_REFRESH_SECRET_KEY=TEST_REFRESH_SECRET,
    )
    create_app(settings_override=dev_settings)
    assert synth_calls == []

    # 3. Production startup (create_app with APP_ENV="production") must not generate or overwrite audio assets
    prod_settings = Settings(
        _env_file=None,
        APP_ENV="production",
        JWT_SECRET_KEY=TEST_ACCESS_SECRET,
        JWT_REFRESH_SECRET_KEY=TEST_REFRESH_SECRET,
    )
    create_app(settings_override=prod_settings)
    assert synth_calls == []

    # Verify none of the 10 existing WAV files were modified during import or startup
    after_mtimes = {
        wav_file.name: wav_file.stat().st_mtime_ns
        for wav_file in AUDIO_ASSETS_DIR.glob("t_20*.wav")
    }
    assert after_mtimes == existing_mtimes

    # 4. Even if AUDIO_ASSETS_DIR points to an empty directory, create_app() in production or dev does not populate it
    empty_audio_dir = tmp_path / "empty_audio"
    monkeypatch.setattr(main_module, "AUDIO_ASSETS_DIR", empty_audio_dir)
    create_app(settings_override=prod_settings)
    assert not empty_audio_dir.exists()
    assert synth_calls == []

    # 5. Direct call to ensure_audio_assets() in production is blocked by default
    with pytest.raises(RuntimeError, match="blocked in production"):
        ensure_audio_assets(target_dir=empty_audio_dir, app_env="production")
    assert not empty_audio_dir.exists()


async def test_m4_02_timeline_duplicate_ids_duration_bounds_and_rollback(
    seeded_db_engine: AsyncEngine,
    mod4_client: TestClient,
) -> None:
    """M4-02: Verify duplicate timeline IDs (in-request and in-DB), ts > durationSeconds, boundary timestamps, and rollback safety."""
    admin_token = _login_as(mod4_client, "admin@spotify-intern.local")
    headers = {"Authorization": f"Bearer {admin_token}"}

    # 1. Duplicate timeline event IDs within the same request -> 400 VALIDATION_ERROR
    dup_in_req_payload = {
        "trackId": "t_4001",
        "title": "Dup Event Track",
        "artist": "Test Artist",
        "durationSeconds": 180,
        "audioAssetUrl": "/assets/audio/t_2001.wav",
        "acoustic": {
            "bpm": 124,
            "camelotKey": "8A",
            "energy": 0.7,
            "valence": 0.6,
        },
        "timeline": [
            {"id": "evt_same_1", "ts": 10, "type": "trivia", "text": "First event."},
            {"id": "evt_same_1", "ts": 30, "type": "trivia", "text": "Duplicate ID event."},
        ],
    }
    res_dup_req = mod4_client.post(
        "/api/v1/admin/tracks", json=dup_in_req_payload, headers=headers
    )
    assert res_dup_req.status_code == 400
    assert res_dup_req.json()["error"]["code"] == "VALIDATION_ERROR"

    # 2. Timeline event ID already present in the database (evt_5541) -> 409 Conflict & zero partial track record left behind
    dup_in_db_payload = {
        "trackId": "t_4002_rollback",
        "title": "Rollback Check Track",
        "artist": "Test Artist",
        "durationSeconds": 180,
        "audioAssetUrl": "/assets/audio/t_2001.wav",
        "acoustic": {
            "bpm": 124,
            "camelotKey": "8A",
            "energy": 0.7,
            "valence": 0.6,
        },
        "timeline": [
            {
                "id": "evt_5541",
                "ts": 12,
                "type": "sample_credit",
                "text": "Collides with seeded evt_5541.",
            }
        ],
    }
    res_dup_db = mod4_client.post(
        "/api/v1/admin/tracks", json=dup_in_db_payload, headers=headers
    )
    assert res_dup_db.status_code == 409
    assert res_dup_db.json()["error"]["code"] == "TIMELINE_EVENT_ALREADY_EXISTS"

    # Verify t_4002_rollback was NOT partially persisted in the database
    session_maker = create_session_maker(seeded_db_engine)
    async with session_maker() as session:
        orphan = (
            await session.execute(
                select(Track).where(Track.id == "t_4002_rollback")
            )
        ).scalar_one_or_none()
        assert orphan is None

    # 3. Timeline timestamp beyond durationSeconds (ts=181 > durationSeconds=180) -> 400 VALIDATION_ERROR
    beyond_duration_payload = {
        "trackId": "t_4003",
        "title": "Beyond Duration Track",
        "artist": "Test Artist",
        "durationSeconds": 180,
        "audioAssetUrl": "/assets/audio/t_2001.wav",
        "acoustic": {
            "bpm": 124,
            "camelotKey": "8A",
            "energy": 0.7,
            "valence": 0.6,
        },
        "timeline": [
            {"ts": 181, "type": "trivia", "text": "Timestamp exceeds track duration."}
        ],
    }
    res_beyond = mod4_client.post(
        "/api/v1/admin/tracks", json=beyond_duration_payload, headers=headers
    )
    assert res_beyond.status_code == 400
    assert res_beyond.json()["error"]["code"] == "VALIDATION_ERROR"

    # 4. Valid boundary timestamps (ts=0 and ts=durationSeconds=180) with auto-generated event IDs -> 201 Created
    boundary_payload = {
        "trackId": "t_4004_boundary",
        "title": "Boundary Track",
        "artist": "Test Artist",
        "durationSeconds": 180,
        "audioAssetUrl": "/assets/audio/t_2001.wav",
        "acoustic": {
            "bpm": 124,
            "camelotKey": "8A",
            "energy": 0.7,
            "valence": 0.6,
        },
        "timeline": [
            {"ts": 0, "type": "glossary", "text": "Intro boundary at 0 seconds."},
            {"ts": 180, "type": "trivia", "text": "Outro boundary at 180 seconds."},
        ],
    }
    res_boundary = mod4_client.post(
        "/api/v1/admin/tracks", json=boundary_payload, headers=headers
    )
    assert res_boundary.status_code == 201
    assert len(res_boundary.json()["timeline"]) == 2


def test_m4_03_audio_asset_url_path_traversal_and_extension_validation(
    mod4_client: TestClient,
) -> None:
    """M4-03: Verify literal, single-encoded, double-encoded traversal, drive/UNC paths, null bytes, and unsupported extensions are rejected."""
    admin_token = _login_as(mod4_client, "admin@spotify-intern.local")
    headers = {"Authorization": f"Bearer {admin_token}"}

    def upload_with_url(url: str, suffix: str) -> int:
        payload = {
            "trackId": f"t_url_{suffix}",
            "title": "URL Test Track",
            "artist": "URL Validator",
            "durationSeconds": 180,
            "audioAssetUrl": url,
            "acoustic": {
                "bpm": 124,
                "camelotKey": "8A",
                "energy": 0.75,
                "valence": 0.65,
            },
            "timeline": [],
        }
        return mod4_client.post(
            "/api/v1/admin/tracks", json=payload, headers=headers
        ).status_code

    # 1. Valid local paths (.wav, .mp3, .ogg) and valid external HTTP(S) audio URLs succeed (201)
    assert upload_with_url("/assets/audio/t_2001.wav", "valid_wav") == 201
    assert upload_with_url("/assets/audio/sample_beat.mp3", "valid_mp3") == 201
    assert upload_with_url("/assets/audio/ambient_loop.ogg", "valid_ogg") == 201
    assert (
        upload_with_url(
            "https://cdn.example.com/royalty-free/track_01.wav", "valid_https"
        )
        == 201
    )

    # 2. All literal, encoded, double-encoded, drive, UNC, null-byte, query/fragment, and extension attacks fail (400)
    invalid_urls = [
        "/assets/audio/../secret.wav",
        "/assets/audio/%2e%2e/secret.wav",
        "/assets/audio/%2E%2E/secret.wav",
        "/assets/audio/%252e%252e/secret.wav",
        "/assets/audio/..%2fsecret.wav",
        "/assets/audio/..%5csecret.wav",
        "/assets/audio/sub%2ft_2001.wav",
        "/assets/audio/sub%5ct_2001.wav",
        "/assets/audio/%252e%252e%252fsecret.wav",
        "/assets/audio/../../etc/passwd",
        "/assets/audio/subdir/t_2001.wav",
        "C:\\audio\\track.wav",
        "C:/audio/track.wav",
        "/assets/audio/C:track.wav",
        "\\\\server\\share\\track.wav",
        "//server/share/track.wav",
        "/assets/audio/t_2001.wav\x00.txt",
        "/assets/audio/t_2001%00.wav",
        "/assets/audio/t_2001.wav?evil=1",
        "/assets/audio/t_2001.wav#frag",
        "/assets/audio/script.py",
        "/assets/audio/notes.txt",
        "/other_folder/t_2001.wav",
        "file:///etc/passwd",
        "https://cdn.example.com/../secret.wav",
        "https://cdn.example.com/%2e%2e/secret.wav",
        "https://cdn.example.com/%252e%252e/secret.wav",
        "https://cdn.example.com/audio/payload.exe",
    ]
    for idx, bad_url in enumerate(invalid_urls):
        assert upload_with_url(bad_url, f"bad_{idx}") == 400, (
            f"Expected 400 for invalid audioAssetUrl='{bad_url}'"
        )


def test_m4_04_bpm_filter_validation(mod4_client: TestClient) -> None:
    """M4-04: Verify minBpm > maxBpm returns 400 while equal, min-only, max-only, and valid range filters return 200."""
    token = _login_as(mod4_client, "listener@spotify-intern.local")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. minBpm > maxBpm -> 400 INVALID_BPM_RANGE
    res_inverted = mod4_client.get(
        "/api/v1/tracks?minBpm=140&maxBpm=110", headers=headers
    )
    assert res_inverted.status_code == 400
    assert res_inverted.json()["error"]["code"] == "INVALID_BPM_RANGE"

    # 2. Equal minBpm == maxBpm (124) -> 200 OK matching t_2001
    res_equal = mod4_client.get(
        "/api/v1/tracks?minBpm=124&maxBpm=124", headers=headers
    )
    assert res_equal.status_code == 200
    assert [t["trackId"] for t in res_equal.json()["tracks"]] == ["t_2001"]

    # 3. Minimum-only filter (minBpm=128) -> 200 OK
    res_min = mod4_client.get("/api/v1/tracks?minBpm=128", headers=headers)
    assert res_min.status_code == 200
    assert res_min.json()["total"] >= 3
    assert all(t["acoustic"]["bpm"] >= 128 for t in res_min.json()["tracks"])

    # 4. Maximum-only filter (maxBpm=96) -> 200 OK
    res_max = mod4_client.get("/api/v1/tracks?maxBpm=96", headers=headers)
    assert res_max.status_code == 200
    assert res_max.json()["total"] >= 3
    assert all(t["acoustic"]["bpm"] <= 96 for t in res_max.json()["tracks"])

    # 5. Normal valid range (minBpm=122, maxBpm=126) -> 200 OK
    res_range = mod4_client.get(
        "/api/v1/tracks?minBpm=122&maxBpm=126", headers=headers
    )
    assert res_range.status_code == 200
    assert res_range.json()["total"] == 4
    assert all(
        122 <= t["acoustic"]["bpm"] <= 126 for t in res_range.json()["tracks"]
    )
