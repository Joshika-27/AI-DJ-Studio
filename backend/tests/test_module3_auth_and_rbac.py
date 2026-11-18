"""Module 3 automated tests: Registration, Login, Bcrypt 72-Byte Handling, JWT Security, Refresh Rotation & DB Revocation, Timing Equalization, Input Validation, and 4-Role RBAC (REQ-069, REQ-070, REQ-072, REQ-094, Remediations F-01..F-07)."""

import bcrypt
from fastapi import Depends
from fastapi.testclient import TestClient
import jwt
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.deps import require_permission
from app.core.config import Settings
from app.core.security import (
    _DUMMY_PASSWORD_HASH,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.db.session import (
    configure_database,
    create_session_maker,
    drop_db,
    init_db,
)
from app.main import create_app
from app.models.user import RefreshToken, User

TEST_ACCESS_SECRET = "test_only_access_signing_key_with_at_least_32_bytes_length_01"
TEST_REFRESH_SECRET = "test_only_refresh_signing_key_with_at_least_32_bytes_length_02"


@pytest.fixture
async def auth_db_engine() -> AsyncEngine:
    """Initialize an isolated in-memory database for Module 3 Auth & RBAC tests."""
    engine = configure_database("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    yield engine
    await drop_db(engine)
    await engine.dispose()


@pytest.fixture
def test_settings() -> Settings:
    """Return deterministic test Settings with distinct >=32-char access and refresh secrets."""
    return Settings(
        _env_file=None,
        APP_NAME="AI DJ Studio Auth Test",
        APP_ENV="testing",
        JWT_SECRET_KEY=TEST_ACCESS_SECRET,
        JWT_REFRESH_SECRET_KEY=TEST_REFRESH_SECRET,
        JWT_ALGORITHM="HS256",
        ACCESS_TOKEN_EXPIRE_SECONDS=3600,
        REFRESH_TOKEN_EXPIRE_SECONDS=604800,
    )


@pytest.fixture
def auth_client(auth_db_engine: AsyncEngine, test_settings: Settings) -> TestClient:
    """Create a TestClient bound to the initialized database and test settings."""
    app = create_app(settings_override=test_settings)

    @app.get("/api/v1/test-permission-upload")
    async def protected_upload_check(
        user: User = Depends(require_permission("admin:tracks:upload")),
    ) -> dict:
        return {"allowed": True, "userId": user.id}

    return TestClient(app, raise_server_exceptions=False)


async def test_password_hashing_stores_bcrypt_hash_never_plaintext(
    auth_db_engine: AsyncEngine,
    auth_client: TestClient,
) -> None:
    """Verify passwords are hashed with bcrypt and never stored in plaintext."""
    raw_password = "StrongDJPassword!2026"
    hashed = hash_password(raw_password)
    assert hashed != raw_password
    assert hashed.startswith("$2b$12$")
    assert verify_password(raw_password, hashed) is True
    assert verify_password("WrongPassword!2026", hashed) is False

    res = auth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "listener_one@spotify-intern.local",
            "password": raw_password,
            "displayName": "DJ Listener",
        },
    )
    assert res.status_code == 201

    session_maker = create_session_maker(auth_db_engine)
    async with session_maker() as session:
        db_user = (
            await session.execute(
                select(User).where(User.email == "listener_one@spotify-intern.local")
            )
        ).scalar_one()
        assert db_user.password_hash != raw_password
        assert db_user.password_hash.startswith("$2b$12$")
        assert verify_password(raw_password, db_user.password_hash) is True


def test_f01_public_registration_prevents_role_escalation(
    auth_client: TestClient,
    test_settings: Settings,
) -> None:
    """F-01: Verify public registration always assigns 'listener' and rejects any role parameter."""
    # 1. Standard registration without role creates a 'listener' account and 'listener' JWT
    res = auth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "public_user@spotify-intern.local",
            "password": "ValidPassword123!",
            "displayName": "Public Listener",
        },
    )
    assert res.status_code == 201
    body = res.json()
    assert body["role"] == "listener"
    assert body["user"]["role"] == "listener"
    decoded = decode_access_token(body["accessToken"], settings=test_settings)
    assert decoded["role"] == "listener"

    # 2. Attempting to submit role='admin', 'host', 'co_dj', or even 'listener' is rejected (400)
    for attempted_role in ("admin", "host", "co_dj", "listener"):
        escalation_res = auth_client.post(
            "/api/v1/auth/register",
            json={
                "email": f"attempt_{attempted_role}@spotify-intern.local",
                "password": "ValidPassword123!",
                "displayName": "Escalation Attempt",
                "role": attempted_role,
            },
        )
        assert escalation_res.status_code == 400
        assert escalation_res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_f02_bcrypt_72_byte_boundary_and_multibyte_passwords(
    auth_client: TestClient,
) -> None:
    """F-02: Verify exact 72-byte UTF-8 boundary succeeds while 73+ bytes return 400 instead of 500."""
    # 1. Exact 72 ASCII bytes succeeds on register and login
    pw_72_ascii = "A" * 72
    res_72 = auth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "ascii72@spotify-intern.local",
            "password": pw_72_ascii,
            "displayName": "ASCII 72 User",
        },
    )
    assert res_72.status_code == 201

    login_72 = auth_client.post(
        "/api/v1/auth/login",
        json={"email": "ascii72@spotify-intern.local", "password": pw_72_ascii},
    )
    assert login_72.status_code == 200

    # 2. 73 ASCII bytes returns 400 Bad Request on both register and login (never 500)
    pw_73_ascii = "A" * 73
    res_73 = auth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "ascii73@spotify-intern.local",
            "password": pw_73_ascii,
            "displayName": "ASCII 73 User",
        },
    )
    assert res_73.status_code == 400
    assert res_73.json()["error"]["code"] == "VALIDATION_ERROR"

    login_73 = auth_client.post(
        "/api/v1/auth/login",
        json={"email": "ascii72@spotify-intern.local", "password": pw_73_ascii},
    )
    assert login_73.status_code == 400
    assert login_73.json()["error"]["code"] == "VALIDATION_ERROR"

    # 3. Multibyte UTF-8 boundary: 18 fire emojis = 72 bytes (valid), 19 fire emojis = 76 bytes (invalid)
    pw_72_utf8 = "🔥" * 18
    assert len(pw_72_utf8) == 18
    assert len(pw_72_utf8.encode("utf-8")) == 72
    res_emoji_72 = auth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "emoji72@spotify-intern.local",
            "password": pw_72_utf8,
            "displayName": "Emoji 72 User",
        },
    )
    assert res_emoji_72.status_code == 201

    pw_76_utf8 = "🔥" * 19
    assert len(pw_76_utf8) == 19
    assert len(pw_76_utf8.encode("utf-8")) == 76
    res_emoji_76 = auth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "emoji76@spotify-intern.local",
            "password": pw_76_utf8,
            "displayName": "Emoji 76 User",
        },
    )
    assert res_emoji_76.status_code == 400
    assert res_emoji_76.json()["error"]["code"] == "VALIDATION_ERROR"

    # Direct helper checks
    with pytest.raises(ValueError, match="72 UTF-8 bytes"):
        hash_password(pw_73_ascii)
    assert verify_password(pw_73_ascii, hash_password("ValidPass123!")) is False


def test_f03_refresh_token_rotation_replay_detection_and_logout_revocation(
    auth_db_engine: AsyncEngine,
    auth_client: TestClient,
    test_settings: Settings,
) -> None:
    """F-03: Verify jti requirement, single-use refresh rotation, replay revocation, logout, and persistence across app restarts."""
    reg = auth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "rotate@spotify-intern.local",
            "password": "RotatePassword99!",
            "displayName": "Rotation User",
        },
    )
    assert reg.status_code == 201
    user_id = reg.json()["userId"]
    rtk_1 = reg.json()["refreshToken"]

    # 1. First refresh succeeds and rotates refresh token (rtk_1 -> rtk_2)
    ref_1 = auth_client.post("/api/v1/auth/refresh", json={"refreshToken": rtk_1})
    assert ref_1.status_code == 200
    rtk_2 = ref_1.json()["refreshToken"]
    assert rtk_2 != rtk_1

    # 2. Replaying consumed rtk_1 fails with 401 REFRESH_TOKEN_REUSED AND revokes rtk_2!
    replay_res = auth_client.post("/api/v1/auth/refresh", json={"refreshToken": rtk_1})
    assert replay_res.status_code == 401
    assert replay_res.json()["error"]["code"] == "REFRESH_TOKEN_REUSED"

    # Verify rtk_2 was automatically revoked by the replay defense
    after_replay_res = auth_client.post(
        "/api/v1/auth/refresh", json={"refreshToken": rtk_2}
    )
    assert after_replay_res.status_code == 401
    assert after_replay_res.json()["error"]["code"] == "REFRESH_TOKEN_REVOKED"

    # 3. Fresh login issues rtk_3; logout revokes rtk_3 persistently in database
    login_res = auth_client.post(
        "/api/v1/auth/login",
        json={
            "email": "rotate@spotify-intern.local",
            "password": "RotatePassword99!",
        },
    )
    assert login_res.status_code == 200
    rtk_3 = login_res.json()["refreshToken"]

    logout_res = auth_client.post("/api/v1/auth/logout", json={"refreshToken": rtk_3})
    assert logout_res.status_code == 200
    assert logout_res.json()["revoked"] is True

    # Simulate server restart by creating a brand-new FastAPI app instance sharing the DB
    restarted_client = TestClient(
        create_app(settings_override=test_settings), raise_server_exceptions=False
    )
    revoked_check = restarted_client.post(
        "/api/v1/auth/refresh", json={"refreshToken": rtk_3}
    )
    assert revoked_check.status_code == 401
    assert revoked_check.json()["error"]["code"] == "REFRESH_TOKEN_REVOKED"

    # 4. Token missing 'jti' claim is rejected with 401
    token_without_jti = jwt.encode(
        {
            "sub": user_id,
            "email": "rotate@spotify-intern.local",
            "role": "listener",
            "type": "access",
            "iat": 1700000000,
            "exp": 9999999999,
        },
        TEST_ACCESS_SECRET,
        algorithm="HS256",
    )
    no_jti_res = auth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token_without_jti}"},
    )
    assert no_jti_res.status_code == 401

    # 5. Expired access and refresh tokens are rejected with 401
    expired_access, _ = create_access_token(
        user_id=user_id,
        email="rotate@spotify-intern.local",
        role="listener",
        expires_delta_seconds=-10,
        settings=test_settings,
    )
    assert (
        auth_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {expired_access}"},
        ).json()["error"]["code"]
        == "TOKEN_EXPIRED"
    )

    expired_refresh = create_refresh_token(
        user_id=user_id,
        email="rotate@spotify-intern.local",
        role="listener",
        expires_delta_seconds=-10,
        settings=test_settings,
    )
    assert (
        auth_client.post(
            "/api/v1/auth/refresh",
            json={"refreshToken": expired_refresh},
        ).json()["error"]["code"]
        == "REFRESH_TOKEN_EXPIRED"
    )


def test_f04_jwt_secret_and_algorithm_validation() -> None:
    """F-04: Verify algorithm allowlist, minimum secret length, placeholder rejection, distinct secrets, and production checks."""
    # 1. Invalid algorithms rejected
    for bad_alg in ("none", "RS256", "HS1", "invalid"):
        with pytest.raises(ValueError, match="JWT_ALGORITHM"):
            Settings(_env_file=None, JWT_ALGORITHM=bad_alg)

    # 2. Weak (< 32 chars) secrets rejected
    with pytest.raises(ValueError, match="at least 32 characters"):
        Settings(_env_file=None, JWT_SECRET_KEY="too_short_secret")
    with pytest.raises(ValueError, match="at least 32 characters"):
        Settings(_env_file=None, JWT_REFRESH_SECRET_KEY="too_short_refresh")

    # 3. Placeholder / .env.example secrets rejected
    with pytest.raises(ValueError, match="placeholder"):
        Settings(
            _env_file=None,
            JWT_SECRET_KEY="replace_with_a_strong_32_byte_secret_key",
        )
    with pytest.raises(ValueError, match="placeholder"):
        Settings(
            _env_file=None,
            JWT_REFRESH_SECRET_KEY="replace_with_a_strong_32_byte_refresh_secret_key",
        )

    # 4. Identical access and refresh secrets rejected
    same_secret = "identical_strong_secret_key_at_least_32_bytes_long!"
    with pytest.raises(ValueError, match="distinct"):
        Settings(
            _env_file=None,
            JWT_SECRET_KEY=same_secret,
            JWT_REFRESH_SECRET_KEY=same_secret,
        )

    # 5. Production mode requires BOTH secrets to be configured (never derived)
    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        Settings(
            _env_file=None,
            APP_ENV="production",
            JWT_SECRET_KEY=None,
            JWT_REFRESH_SECRET_KEY=TEST_REFRESH_SECRET,
        )
    with pytest.raises(ValueError, match="JWT_REFRESH_SECRET_KEY"):
        Settings(
            _env_file=None,
            APP_ENV="production",
            JWT_SECRET_KEY=TEST_ACCESS_SECRET,
            JWT_REFRESH_SECRET_KEY=None,
        )


def test_f05_login_constant_time_dummy_bcrypt_verification(
    auth_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-05: Verify nonexistent email login executes bcrypt.checkpw against _DUMMY_PASSWORD_HASH."""
    checked_hashes: list[bytes] = []
    real_checkpw = bcrypt.checkpw

    def spy_checkpw(password: bytes, hashed_password: bytes) -> bool:
        checked_hashes.append(hashed_password)
        return real_checkpw(password, hashed_password)

    monkeypatch.setattr(bcrypt, "checkpw", spy_checkpw)

    res = auth_client.post(
        "/api/v1/auth/login",
        json={
            "email": "nonexistent_user@spotify-intern.local",
            "password": "AnyPassword123!",
        },
    )
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert res.json()["error"]["message"] == "Invalid email or password."
    assert len(checked_hashes) == 1
    assert checked_hashes[0] == _DUMMY_PASSWORD_HASH.encode("utf-8")


def test_f06_email_validation_unclosed_html_tags_and_typed_preferences(
    auth_client: TestClient,
) -> None:
    """F-06: Verify malformed HTML emails are rejected, unclosed tags are stripped from displayName, and preferences are strictly typed."""
    # 1. Malformed email containing HTML tags is explicitly rejected (400), NOT silently stripped into a valid email
    html_email_reg = auth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "<script>x</script>victim@spotify-intern.local",
            "password": "ValidPassword123!",
            "displayName": "Valid User",
        },
    )
    assert html_email_reg.status_code == 400
    assert html_email_reg.json()["error"]["code"] == "VALIDATION_ERROR"

    html_email_login = auth_client.post(
        "/api/v1/auth/login",
        json={
            "email": "<img src=x>victim@spotify-intern.local",
            "password": "ValidPassword123!",
        },
    )
    assert html_email_login.status_code == 400

    # 2. Unclosed HTML tag in displayName is completely stripped
    unclosed_res = auth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "clean_user@spotify-intern.local",
            "password": "ValidPassword123!",
            "displayName": "DJ Nova<img src=x onerror=alert(1)",
            "preferences": {"genreAffinity": ["High-Energy EDM", "Synthwave"]},
        },
    )
    assert unclosed_res.status_code == 201
    assert unclosed_res.json()["displayName"] == "DJ Nova"
    assert "<" not in unclosed_res.json()["displayName"]
    assert "onerror" not in unclosed_res.json()["displayName"]

    # Pure unclosed tag leaving < 2 chars is rejected with 400
    assert (
        auth_client.post(
            "/api/v1/auth/register",
            json={
                "email": "pure_unclosed@spotify-intern.local",
                "password": "ValidPassword123!",
                "displayName": "<img src=x onerror=alert(1)",
            },
        ).status_code
        == 400
    )

    # 3. Preferences validation: unknown keys, HTML in genres, or > 20 genres rejected with 400
    assert (
        auth_client.post(
            "/api/v1/auth/register",
            json={
                "email": "badpref1@spotify-intern.local",
                "password": "ValidPassword123!",
                "displayName": "Bad Pref User",
                "preferences": {"arbitraryNested": {"evil": True}},
            },
        ).status_code
        == 400
    )
    assert (
        auth_client.post(
            "/api/v1/auth/register",
            json={
                "email": "badpref2@spotify-intern.local",
                "password": "ValidPassword123!",
                "displayName": "Bad Pref User",
                "preferences": {"genreAffinity": ["<script>EDM</script>"]},
            },
        ).status_code
        == 400
    )
    assert (
        auth_client.post(
            "/api/v1/auth/register",
            json={
                "email": "badpref3@spotify-intern.local",
                "password": "ValidPassword123!",
                "displayName": "Bad Pref User",
                "preferences": {"genreAffinity": [f"Genre {i}" for i in range(25)]},
            },
        ).status_code
        == 400
    )


async def test_f07_and_four_role_rbac_matrix_via_db_fixtures(
    auth_db_engine: AsyncEngine,
    auth_client: TestClient,
) -> None:
    """F-01 & F-07: Seed all 4 roles via controlled DB setup, verify full RBAC matrix, and verify generic 403 responses."""
    session_maker = create_session_maker(auth_db_engine)
    passwords: dict[str, str] = {}
    async with session_maker() as session:
        for role in ("listener", "co_dj", "host", "admin"):
            pw = f"FixturePass_{role}_2026!"
            passwords[role] = pw
            session.add(
                User(
                    id=f"u_{role}",
                    email=f"{role}@spotify-intern.local",
                    password_hash=hash_password(pw),
                    display_name=f"Seeded {role.upper()}",
                    role=role,
                )
            )
        await session.commit()

    tokens: dict[str, str] = {}
    for role, pw in passwords.items():
        login_res = auth_client.post(
            "/api/v1/auth/login",
            json={"email": f"{role}@spotify-intern.local", "password": pw},
        )
        assert login_res.status_code == 200
        tokens[role] = login_res.json()["accessToken"]

    # Unauthenticated request returns 401
    assert auth_client.get("/api/v1/auth/rbac/listener").status_code == 401

    rbac_expectations = {
        "/api/v1/auth/rbac/listener": {
            "listener": 200,
            "co_dj": 200,
            "host": 200,
            "admin": 200,
        },
        "/api/v1/auth/rbac/co-dj": {
            "listener": 403,
            "co_dj": 200,
            "host": 200,
            "admin": 200,
        },
        "/api/v1/auth/rbac/host": {
            "listener": 403,
            "co_dj": 403,
            "host": 200,
            "admin": 200,
        },
        "/api/v1/auth/rbac/admin": {
            "listener": 403,
            "co_dj": 403,
            "host": 403,
            "admin": 200,
        },
        "/api/v1/test-permission-upload": {
            "listener": 403,
            "co_dj": 403,
            "host": 403,
            "admin": 200,
        },
    }

    for endpoint, role_map in rbac_expectations.items():
        for role, expected_status in role_map.items():
            response = auth_client.get(
                endpoint,
                headers={"Authorization": f"Bearer {tokens[role]}"},
            )
            assert response.status_code == expected_status, (
                f"Expected {expected_status} for role='{role}' on {endpoint}, got {response.status_code}"
            )
            if expected_status == 403:
                # F-07: Verify generic 403 error message with zero role/permission topology leakage
                err = response.json()["error"]
                assert err["code"] == "FORBIDDEN"
                assert (
                    err["message"]
                    == "Insufficient permissions to access this resource."
                )
                assert err["details"] is None



async def test_r01_concurrent_refresh_requests_atomic_consumption(
    auth_db_engine: AsyncEngine,
    test_settings: Settings,
) -> None:
    """R-01: Fire two simultaneous refresh requests via asyncio.gather with the same refresh token and verify atomic consumption."""
    import asyncio
    import httpx

    app = create_app(settings_override=test_settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as ac:
        reg_res = await ac.post(
            "/api/v1/auth/register",
            json={
                "email": "concurrent_refresh@spotify-intern.local",
                "password": "ConcurrentPassword2026!",
                "displayName": "Concurrent User",
            },
        )
        assert reg_res.status_code == 201
        user_id = reg_res.json()["userId"]
        parent_rtk = reg_res.json()["refreshToken"]

        # Fire two simultaneous POST /api/v1/auth/refresh requests with the exact same parent_rtk
        res_a, res_b = await asyncio.gather(
            ac.post("/api/v1/auth/refresh", json={"refreshToken": parent_rtk}),
            ac.post("/api/v1/auth/refresh", json={"refreshToken": parent_rtk}),
        )

    statuses = sorted([res_a.status_code, res_b.status_code])
    # Exactly one request succeeds (200) and the concurrent losing request is rejected (401)
    assert statuses == [200, 401]

    losing_res = res_a if res_a.status_code == 401 else res_b
    assert losing_res.json()["error"]["code"] == "REFRESH_TOKEN_REUSED"

    # Verify DB state: only 1 replacement token was ever created from parent_rtk (2 rows total),
    # and replay detection revoked all refresh tokens for this user
    session_maker = create_session_maker(auth_db_engine)
    async with session_maker() as session:
        tokens = (
            (
                await session.execute(
                    select(RefreshToken).where(RefreshToken.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(tokens) == 2
        assert all(t.revoked is True for t in tokens)


async def test_r02_logout_with_rotated_token_revokes_entire_descendant_family(
    auth_db_engine: AsyncEngine,
    auth_client: TestClient,
) -> None:
    """R-02: Verify logging out with a rotated parent token revokes the entire active descendant token family."""
    reg = auth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "family_logout@spotify-intern.local",
            "password": "FamilyLogoutPass2026!",
            "displayName": "Family Logout User",
        },
    )
    assert reg.status_code == 201
    user_id = reg.json()["userId"]
    rtk_1 = reg.json()["refreshToken"]

    # Rotate rtk_1 -> rtk_2 -> rtk_3
    ref_1 = auth_client.post("/api/v1/auth/refresh", json={"refreshToken": rtk_1})
    assert ref_1.status_code == 200
    rtk_2 = ref_1.json()["refreshToken"]

    ref_2 = auth_client.post("/api/v1/auth/refresh", json={"refreshToken": rtk_2})
    assert ref_2.status_code == 200
    rtk_3 = ref_2.json()["refreshToken"]

    # Call /logout passing the already-rotated parent token rtk_1
    logout_res = auth_client.post("/api/v1/auth/logout", json={"refreshToken": rtk_1})
    assert logout_res.status_code == 200
    assert logout_res.json()["revoked"] is True

    # Descendant active token rtk_3 must now be revoked in DB and rejected on /refresh
    ref_3 = auth_client.post("/api/v1/auth/refresh", json={"refreshToken": rtk_3})
    assert ref_3.status_code == 401
    assert ref_3.json()["error"]["code"] == "REFRESH_TOKEN_REVOKED"

    session_maker = create_session_maker(auth_db_engine)
    async with session_maker() as session:
        all_tokens = (
            (
                await session.execute(
                    select(RefreshToken).where(RefreshToken.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(all_tokens) == 3
        assert all(t.revoked is True for t in all_tokens)


async def test_r03_database_expires_at_enforced_even_when_jwt_exp_not_reached(
    auth_db_engine: AsyncEngine,
    auth_client: TestClient,
    test_settings: Settings,
) -> None:
    """R-03: Verify RefreshToken.expires_at in DB is enforced against UTC now even if the JWT exp claim is still in the future."""
    from datetime import timedelta
    from app.core.security import decode_refresh_token
    from app.db.base import utc_now

    reg = auth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "db_expiry@spotify-intern.local",
            "password": "DbExpiryPassword2026!",
            "displayName": "DB Expiry User",
        },
    )
    assert reg.status_code == 201
    rtk = reg.json()["refreshToken"]
    jti = decode_refresh_token(rtk, settings=test_settings)["jti"]

    # Manually expire the database record while JWT exp is still +7 days in the future
    session_maker = create_session_maker(auth_db_engine)
    async with session_maker() as session:
        rec = (
            await session.execute(select(RefreshToken).where(RefreshToken.jti == jti))
        ).scalar_one()
        rec.expires_at = utc_now() - timedelta(minutes=10)
        await session.commit()

    ref_res = auth_client.post("/api/v1/auth/refresh", json={"refreshToken": rtk})
    assert ref_res.status_code == 401
    assert ref_res.json()["error"]["code"] == "REFRESH_TOKEN_EXPIRED"

    # Verify the expired record was also marked revoked=True in DB
    async with session_maker() as session:
        rec_after = (
            await session.execute(select(RefreshToken).where(RefreshToken.jti == jti))
        ).scalar_one()
        assert rec_after.revoked is True
