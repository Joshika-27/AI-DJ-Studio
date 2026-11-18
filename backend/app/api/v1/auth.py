"""Authentication and 4-role RBAC endpoints (Remediated F-01..F-07 and R-01..R-03)."""

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_app_settings,
    get_current_user,
    require_admin,
    require_co_dj_or_above,
    require_host_or_above,
    require_listener_or_above,
)
from app.core.config import Settings
from app.core.exceptions import AppException
from app.core.logging import get_logger
from app.core.security import (
    ROLE_PERMISSIONS,
    create_access_token,
    create_refresh_token_with_metadata,
    decode_refresh_token,
    hash_password,
    hash_token,
    verify_password_constant_time,
)
from app.db.base import utc_now
from app.db.session import get_db
from app.models.user import RefreshToken, User
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    LogoutResponse,
    RefreshRequest,
    RefreshResponse,
    RegisterRequest,
    RegisterResponse,
    UserProfileResponse,
)

logger = get_logger("api.auth")

router = APIRouter(prefix="/auth", tags=["Authentication & RBAC"])

# Per-process async lock coordinating concurrent refresh transactions on shared/pooled connections
_REFRESH_ROTATION_LOCK = asyncio.Lock()


def _ensure_utc(dt: datetime) -> datetime:
    """Normalize a database datetime to timezone-aware UTC (handles naive UTC from SQLite)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def _persist_refresh_token(
    db: AsyncSession,
    user: User,
    settings: Settings,
) -> str:
    """Issue a new refresh JWT and store its hashed record in the refresh_tokens table (F-03)."""
    raw_token, jti, issued_at, expires_at = create_refresh_token_with_metadata(
        user_id=user.id,
        email=user.email,
        role=user.role,
        settings=settings,
    )
    token_record = RefreshToken(
        jti=jti,
        user_id=user.id,
        token_hash=hash_token(raw_token),
        issued_at=issued_at,
        expires_at=expires_at,
        revoked=False,
        replaced_by_jti=None,
    )
    db.add(token_record)
    return raw_token


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new listener user account",
)
async def register_user(
    payload: RegisterRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> RegisterResponse:
    """Create a new user account strictly assigned the 'listener' role (F-01)."""
    existing_stmt = select(User).where(User.email == payload.email)
    existing_user = (await db.execute(existing_stmt)).scalar_one_or_none()
    if existing_user is not None:
        raise AppException(
            message="An account with this email address already exists.",
            status_code=status.HTTP_409_CONFLICT,
            code="EMAIL_ALREADY_EXISTS",
        )

    # F-01: Public registration ALWAYS assigns role='listener' on the server
    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        display_name=payload.displayName,
        role="listener",
        preferences=payload.preferences.model_dump(),
    )
    db.add(user)
    await db.flush()

    access_token, expires_in = create_access_token(
        user_id=user.id,
        email=user.email,
        role=user.role,
        settings=settings,
    )
    refresh_token = await _persist_refresh_token(
        db=db,
        user=user,
        settings=settings,
    )
    await db.commit()
    await db.refresh(user)

    logger.info(
        "Registered new user account",
        extra={
            "event": "auth.user.registered",
            "payload": {"userId": user.id, "role": user.role},
        },
    )

    profile = UserProfileResponse.from_orm_user(user)
    return RegisterResponse(
        userId=user.id,
        email=user.email,
        displayName=user.display_name,
        role=user.role,
        accessToken=access_token,
        refreshToken=refresh_token,
        expiresIn=expires_in,
        user=profile,
    )


@router.post(
    "/login",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    summary="Authenticate user and issue short-lived JWT + persistent refresh token",
)
async def login_user(
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> LoginResponse:
    """Authenticate email/password with constant-time dummy verification for absent users (F-05)."""
    stmt = select(User).where(User.email == payload.email)
    user = (await db.execute(stmt)).scalar_one_or_none()

    # F-05: Always execute bcrypt verification even when user is None to prevent timing enumeration
    password_ok = verify_password_constant_time(
        payload.password,
        user.password_hash if user is not None else None,
    )
    if user is None or not password_ok:
        logger.warning(
            "Authentication failed",
            extra={
                "event": "auth.login.failed",
                "payload": {"reason": "invalid_credentials"},
            },
        )
        raise AppException(
            message="Invalid email or password.",
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="INVALID_CREDENTIALS",
        )

    access_token, expires_in = create_access_token(
        user_id=user.id,
        email=user.email,
        role=user.role,
        settings=settings,
    )
    refresh_token = await _persist_refresh_token(
        db=db,
        user=user,
        settings=settings,
    )
    await db.commit()

    logger.info(
        "User logged in",
        extra={
            "event": "auth.login.succeeded",
            "payload": {"userId": user.id, "role": user.role},
        },
    )

    return LoginResponse(
        accessToken=access_token,
        refreshToken=refresh_token,
        expiresIn=expires_in,
        user=UserProfileResponse.from_orm_user(user),
    )


@router.post(
    "/refresh",
    response_model=RefreshResponse,
    status_code=status.HTTP_200_OK,
    summary="Atomically rotate refresh token and issue a new short-lived access JWT",
)
async def refresh_access_token(
    payload: RefreshRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> RefreshResponse:
    """Atomically consume single-use refresh token, detect replay, validate DB expiry, and rotate token (F-03, R-01, R-03)."""
    decoded = decode_refresh_token(payload.refreshToken, settings=settings)
    user_id = str(decoded.get("sub", ""))
    jti = str(decoded.get("jti", ""))
    presented_hash = hash_token(payload.refreshToken)

    async with _REFRESH_ROTATION_LOCK:
        now_utc = utc_now()

        # Pre-generate candidate replacement metadata; only persisted and returned if atomic UPDATE succeeds
        candidate_user_stmt = select(User).where(User.id == user_id)
        user = (await db.execute(candidate_user_stmt)).scalar_one_or_none()
        if user is None:
            await db.execute(
                update(RefreshToken)
                .where(RefreshToken.user_id == user_id)
                .values(revoked=True)
            )
            await db.commit()
            raise AppException(
                message="Invalid authentication credentials.",
                status_code=status.HTTP_401_UNAUTHORIZED,
                code="UNAUTHORIZED",
            )

        new_refresh_token, new_jti, new_issued_at, new_expires_at = (
            create_refresh_token_with_metadata(
                user_id=user.id,
                email=user.email,
                role=user.role,
                settings=settings,
            )
        )

        # R-01 & R-03: Atomic conditional UPDATE — succeeds ONLY if token is active, unreplaced, and not expired in DB
        consume_stmt = (
            update(RefreshToken)
            .where(
                RefreshToken.jti == jti,
                RefreshToken.user_id == user_id,
                RefreshToken.token_hash == presented_hash,
                RefreshToken.revoked.is_(False),
                RefreshToken.replaced_by_jti.is_(None),
                RefreshToken.expires_at > now_utc,
            )
            .values(
                revoked=True,
                replaced_by_jti=new_jti,
            )
        )
        consume_result = await db.execute(consume_stmt)

        if consume_result.rowcount != 1:
            # Atomic consumption did not match an active token; inspect the existing record to classify & handle replay/expiry
            token_stmt = select(RefreshToken).where(RefreshToken.jti == jti)
            token_record = (await db.execute(token_stmt)).scalar_one_or_none()

            if (
                token_record is None
                or token_record.user_id != user_id
                or token_record.token_hash != presented_hash
            ):
                raise AppException(
                    message="Invalid or unrecognized refresh token.",
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    code="INVALID_REFRESH_TOKEN",
                )

            if token_record.replaced_by_jti is not None:
                # R-01 & F-03: Replay of an already-rotated token detected -> revoke all active refresh tokens for this user and COMMIT
                await db.execute(
                    update(RefreshToken)
                    .where(RefreshToken.user_id == user_id)
                    .values(revoked=True)
                )
                await db.commit()
                logger.warning(
                    "Consumed refresh token replay detected; revoked all user refresh tokens",
                    extra={
                        "event": "auth.refresh.replay_detected",
                        "payload": {"userId": user_id, "replayedJti": jti},
                    },
                )
                raise AppException(
                    message="Refresh token has already been used and is no longer valid.",
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    code="REFRESH_TOKEN_REUSED",
                )

            # R-03: Check database expires_at against current UTC time
            if _ensure_utc(token_record.expires_at) <= now_utc:
                if not token_record.revoked:
                    token_record.revoked = True
                    await db.commit()
                raise AppException(
                    message="Refresh token has expired.",
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    code="REFRESH_TOKEN_EXPIRED",
                )

            raise AppException(
                message="Refresh token has been revoked.",
                status_code=status.HTTP_401_UNAUTHORIZED,
                code="REFRESH_TOKEN_REVOKED",
            )

        # Exactly one request won the atomic conditional UPDATE: persist the single replacement token and commit
        new_record = RefreshToken(
            jti=new_jti,
            user_id=user.id,
            token_hash=hash_token(new_refresh_token),
            issued_at=new_issued_at,
            expires_at=new_expires_at,
            revoked=False,
            replaced_by_jti=None,
        )
        db.add(new_record)

        access_token, expires_in = create_access_token(
            user_id=user.id,
            email=user.email,
            role=user.role,
            settings=settings,
        )
        await db.commit()

        return RefreshResponse(
            accessToken=access_token,
            refreshToken=new_refresh_token,
            expiresIn=expires_in,
        )


@router.post(
    "/logout",
    response_model=LogoutResponse,
    status_code=status.HTTP_200_OK,
    summary="Revoke a refresh token and its descendant rotation family",
)
async def logout_user(
    payload: LogoutRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> LogoutResponse:
    """Persistently revoke the supplied refresh token and any descendant rotated tokens in its family (F-03, R-02)."""
    decoded = decode_refresh_token(payload.refreshToken, settings=settings)
    jti = str(decoded.get("jti", ""))
    user_id = str(decoded.get("sub", ""))
    presented_hash = hash_token(payload.refreshToken)

    async with _REFRESH_ROTATION_LOCK:
        stmt = select(RefreshToken).where(
            RefreshToken.jti == jti,
            RefreshToken.user_id == user_id,
        )
        record = (await db.execute(stmt)).scalar_one_or_none()
        if record is None or record.token_hash != presented_hash:
            raise AppException(
                message="Invalid or unrecognized refresh token.",
                status_code=status.HTTP_401_UNAUTHORIZED,
                code="INVALID_REFRESH_TOKEN",
            )

        record.revoked = True

        # R-02: If this token was already rotated/replaced, walk the descendant chain and revoke the entire active token family
        if record.replaced_by_jti is not None:
            visited: set[str] = {jti}
            next_jti: str | None = record.replaced_by_jti
            while next_jti and next_jti not in visited:
                visited.add(next_jti)
                desc_stmt = select(RefreshToken).where(
                    RefreshToken.jti == next_jti,
                    RefreshToken.user_id == user_id,
                )
                desc_record = (await db.execute(desc_stmt)).scalar_one_or_none()
                if desc_record is None:
                    break
                desc_record.revoked = True
                next_jti = desc_record.replaced_by_jti

            # Also ensure all active tokens for this user family are revoked
            await db.execute(
                update(RefreshToken)
                .where(
                    RefreshToken.user_id == user_id,
                    RefreshToken.revoked.is_(False),
                )
                .values(revoked=True)
            )

        await db.commit()

    logger.info(
        "User refresh token family revoked via logout",
        extra={
            "event": "auth.logout.succeeded",
            "payload": {"userId": user_id, "jti": jti},
        },
    )
    return LogoutResponse(
        revoked=True,
        message="Refresh token revoked successfully.",
    )


@router.get(
    "/me",
    response_model=UserProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Get current authenticated user profile and permissions",
)
async def get_me(
    current_user: User = Depends(get_current_user),
) -> UserProfileResponse:
    """Return profile and active RBAC permissions for the authenticated user."""
    return UserProfileResponse.from_orm_user(current_user)


@router.get(
    "/rbac/listener",
    summary="Verify Listener-or-above RBAC access (listener, co_dj, host, admin)",
)
async def verify_listener_tier(
    current_user: User = Depends(require_listener_or_above),
) -> Dict[str, Any]:
    """Protected endpoint accessible by any authenticated role."""
    return {
        "authorized": True,
        "tier": "listener",
        "userId": current_user.id,
        "role": current_user.role,
        "permissions": list(ROLE_PERMISSIONS.get(current_user.role, ())),
    }


@router.get(
    "/rbac/co-dj",
    summary="Verify Co-DJ-or-above RBAC access (co_dj, host, admin)",
)
async def verify_co_dj_tier(
    current_user: User = Depends(require_co_dj_or_above),
) -> Dict[str, Any]:
    """Protected endpoint accessible only by co_dj, host, or admin roles."""
    return {
        "authorized": True,
        "tier": "co_dj",
        "userId": current_user.id,
        "role": current_user.role,
        "permissions": list(ROLE_PERMISSIONS.get(current_user.role, ())),
    }


@router.get(
    "/rbac/host",
    summary="Verify Host-or-above RBAC access (host, admin)",
)
async def verify_host_tier(
    current_user: User = Depends(require_host_or_above),
) -> Dict[str, Any]:
    """Protected endpoint accessible only by host or admin roles."""
    return {
        "authorized": True,
        "tier": "host",
        "userId": current_user.id,
        "role": current_user.role,
        "permissions": list(ROLE_PERMISSIONS.get(current_user.role, ())),
    }


@router.get(
    "/rbac/admin",
    summary="Verify Admin-only RBAC access (admin)",
)
async def verify_admin_tier(
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Protected endpoint accessible strictly by the admin role."""
    return {
        "authorized": True,
        "tier": "admin",
        "userId": current_user.id,
        "role": current_user.role,
        "permissions": list(ROLE_PERMISSIONS.get(current_user.role, ())),
    }
