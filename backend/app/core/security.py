"""Password hashing, JWT access/refresh token management, input sanitization, and RBAC definitions (Module 3 — Remediated F-01..F-07)."""

from datetime import datetime, timedelta, timezone
import hashlib
import html
import re
import secrets
from typing import Any, Dict, Tuple
import uuid

import bcrypt
import jwt
from jwt import ExpiredSignatureError, InvalidTokenError

from app.core.config import (
    ALLOWED_JWT_ALGORITHMS,
    KNOWN_PLACEHOLDER_SECRETS,
    MIN_JWT_SECRET_LENGTH,
    Settings,
    get_settings,
)
from app.core.exceptions import AppException
from app.models.user import VALID_USER_ROLES

MAX_BCRYPT_PASSWORD_BYTES = 72
MIN_PASSWORD_CHARS = 8

# Runtime ephemeral secrets generated per-process ONLY when env vars are unset in non-production
_EPHEMERAL_ACCESS_SECRET = secrets.token_urlsafe(48)
_EPHEMERAL_REFRESH_SECRET = secrets.token_urlsafe(48)

# Precomputed bcrypt hash (rounds=12) for constant-time dummy verification when email does not exist (F-05)
_DUMMY_PASSWORD_HASH: str = bcrypt.hashpw(
    b"dummy_timing_equalization_password_2026!", bcrypt.gensalt(rounds=12)
).decode("utf-8")

_SCRIPT_TAG_PATTERN = re.compile(
    r"<\s*script[^>]*>.*?<\s*/\s*script\s*>",
    flags=re.IGNORECASE | re.DOTALL,
)
_UNCLOSED_SCRIPT_PATTERN = re.compile(
    r"<\s*script[^>]*>.*$",
    flags=re.IGNORECASE | re.DOTALL,
)
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_UNCLOSED_TAG_PATTERN = re.compile(r"<[^>]*")
_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Explicit Role & Permission Matrix (docs/PROJECT_REQUIREMENTS.md Section 4 — REQ-070)
ROLE_PERMISSIONS: Dict[str, Tuple[str, ...]] = {
    "listener": (
        "auth:self",
        "dashboard:view",
        "recommendations:view",
        "lounge:join",
        "chat:send",
        "queue:vote",
        "soundclash:submit",
        "soundclash:vote",
        "aidj:qa",
    ),
    "co_dj": (
        "auth:self",
        "dashboard:view",
        "recommendations:view",
        "lounge:join",
        "chat:send",
        "queue:vote",
        "soundclash:submit",
        "soundclash:vote",
        "aidj:qa",
        "queue:override",
        "aidj:approve_segue",
        "soundclash:open",
        "soundclash:resolve",
    ),
    "host": (
        "auth:self",
        "dashboard:view",
        "recommendations:view",
        "lounge:join",
        "chat:send",
        "queue:vote",
        "soundclash:submit",
        "soundclash:vote",
        "aidj:qa",
        "queue:override",
        "aidj:approve_segue",
        "soundclash:open",
        "soundclash:resolve",
        "lounge:create",
        "lounge:configure",
        "lounge:end",
        "lounge:moderate",
    ),
    "admin": (
        "auth:self",
        "dashboard:view",
        "recommendations:view",
        "lounge:join",
        "chat:send",
        "queue:vote",
        "soundclash:submit",
        "soundclash:vote",
        "aidj:qa",
        "queue:override",
        "aidj:approve_segue",
        "soundclash:open",
        "soundclash:resolve",
        "lounge:create",
        "lounge:configure",
        "lounge:end",
        "lounge:moderate",
        "admin:tracks:upload",
        "admin:tracks:manage",
    ),
}


def sanitize_text_input(value: str) -> str:
    """Sanitize user-supplied text, stripping closed and unclosed HTML/script tags and control characters (REQ-072, F-06)."""
    if not isinstance(value, str):
        return ""
    cleaned = _CONTROL_CHAR_PATTERN.sub("", value)
    for _ in range(2):
        cleaned = _SCRIPT_TAG_PATTERN.sub("", cleaned)
        cleaned = _UNCLOSED_SCRIPT_PATTERN.sub("", cleaned)
        cleaned = _HTML_TAG_PATTERN.sub("", cleaned)
        cleaned = _UNCLOSED_TAG_PATTERN.sub("", cleaned)
        cleaned = cleaned.replace("<", "").replace(">", "")
        cleaned = html.unescape(cleaned)
    cleaned = _SCRIPT_TAG_PATTERN.sub("", cleaned)
    cleaned = _UNCLOSED_SCRIPT_PATTERN.sub("", cleaned)
    cleaned = _HTML_TAG_PATTERN.sub("", cleaned)
    cleaned = _UNCLOSED_TAG_PATTERN.sub("", cleaned)
    cleaned = cleaned.replace("<", "").replace(">", "")
    return cleaned.strip()


def validate_password_bytes(plain_password: str) -> bytes:
    """Validate password length in characters (>=8) and UTF-8 bytes (<=72 for bcrypt) (F-02)."""
    if not isinstance(plain_password, str) or "\x00" in plain_password:
        raise ValueError("Password contains invalid characters.")
    if len(plain_password.strip()) < MIN_PASSWORD_CHARS:
        raise ValueError(
            f"Password must be at least {MIN_PASSWORD_CHARS} non-whitespace characters."
        )
    password_bytes = plain_password.encode("utf-8")
    if len(password_bytes) > MAX_BCRYPT_PASSWORD_BYTES:
        raise ValueError(
            f"Password cannot exceed {MAX_BCRYPT_PASSWORD_BYTES} UTF-8 bytes."
        )
    return password_bytes


def hash_password(plain_password: str) -> str:
    """Hash a plaintext password using bcrypt after validating the 72-byte UTF-8 boundary (F-02)."""
    password_bytes = validate_password_bytes(plain_password)
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Verify a plaintext password against a stored bcrypt hash safely without 72-byte ValueError (F-02)."""
    if not plain_password or not password_hash or "\x00" in plain_password:
        return False
    password_bytes = plain_password.encode("utf-8")
    if len(password_bytes) > MAX_BCRYPT_PASSWORD_BYTES:
        return False
    try:
        return bcrypt.checkpw(
            password_bytes,
            password_hash.encode("utf-8"),
        )
    except ValueError:
        return False


def verify_password_constant_time(
    plain_password: str, password_hash: str | None
) -> bool:
    """Always execute bcrypt verification (using dummy hash when user is absent) to prevent timing enumeration (F-05)."""
    target_hash = password_hash if password_hash is not None else _DUMMY_PASSWORD_HASH
    if not isinstance(plain_password, str) or "\x00" in plain_password:
        bcrypt.checkpw(b"invalid_input_pad", target_hash.encode("utf-8"))
        return False
    password_bytes = plain_password.encode("utf-8")
    if len(password_bytes) > MAX_BCRYPT_PASSWORD_BYTES:
        bcrypt.checkpw(password_bytes[:MAX_BCRYPT_PASSWORD_BYTES], target_hash.encode("utf-8"))
        return False
    try:
        matched = bcrypt.checkpw(password_bytes, target_hash.encode("utf-8"))
    except ValueError:
        return False
    return bool(matched and password_hash is not None)


def hash_token(raw_token: str) -> str:
    """Compute a deterministic SHA-256 hex digest of a JWT string for persistent revocation storage (F-03)."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _assert_valid_secret(secret: str, name: str) -> str:
    stripped = secret.strip()
    lower_val = stripped.lower()
    if (
        lower_val in KNOWN_PLACEHOLDER_SECRETS
        or lower_val.startswith("replace_with_")
        or lower_val.startswith("example_")
    ):
        raise RuntimeError(f"{name} uses a forbidden placeholder/example secret.")
    if len(stripped) < MIN_JWT_SECRET_LENGTH:
        raise RuntimeError(
            f"{name} must be at least {MIN_JWT_SECRET_LENGTH} characters long."
        )
    return stripped


def _resolve_algorithm(settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    alg = (cfg.JWT_ALGORITHM or "").strip().upper()
    if alg not in ALLOWED_JWT_ALGORITHMS:
        raise RuntimeError(
            f"Unsupported JWT_ALGORITHM '{cfg.JWT_ALGORITHM}'. Allowed: {sorted(ALLOWED_JWT_ALGORITHMS)}."
        )
    return alg


def _resolve_access_secret(settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    if cfg.JWT_SECRET_KEY:
        if (
            cfg.JWT_REFRESH_SECRET_KEY
            and cfg.JWT_SECRET_KEY.strip() == cfg.JWT_REFRESH_SECRET_KEY.strip()
        ):
            raise RuntimeError(
                "JWT_SECRET_KEY and JWT_REFRESH_SECRET_KEY must be distinct secrets."
            )
        return _assert_valid_secret(cfg.JWT_SECRET_KEY, "JWT_SECRET_KEY")
    if cfg.APP_ENV.strip().lower() == "production":
        raise RuntimeError(
            "JWT_SECRET_KEY environment variable must be configured in production."
        )
    return _EPHEMERAL_ACCESS_SECRET


def _resolve_refresh_secret(settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    if cfg.JWT_REFRESH_SECRET_KEY:
        if (
            cfg.JWT_SECRET_KEY
            and cfg.JWT_SECRET_KEY.strip() == cfg.JWT_REFRESH_SECRET_KEY.strip()
        ):
            raise RuntimeError(
                "JWT_SECRET_KEY and JWT_REFRESH_SECRET_KEY must be distinct secrets."
            )
        return _assert_valid_secret(cfg.JWT_REFRESH_SECRET_KEY, "JWT_REFRESH_SECRET_KEY")
    if cfg.APP_ENV.strip().lower() == "production":
        raise RuntimeError(
            "JWT_REFRESH_SECRET_KEY environment variable must be configured in production."
        )
    return _EPHEMERAL_REFRESH_SECRET


def create_access_token(
    user_id: str,
    email: str,
    role: str,
    expires_delta_seconds: int | None = None,
    settings: Settings | None = None,
) -> Tuple[str, int]:
    """Issue a short-lived JWT access token (<= 3600 seconds per REQ-069) with a required jti claim."""
    if role not in VALID_USER_ROLES:
        raise ValueError(f"Invalid role '{role}'. Must be one of {VALID_USER_ROLES}.")

    cfg = settings or get_settings()
    alg = _resolve_algorithm(cfg)
    secret = _resolve_access_secret(cfg)
    ttl = (
        expires_delta_seconds
        if expires_delta_seconds is not None
        else cfg.ACCESS_TOKEN_EXPIRE_SECONDS
    )
    # Enforce REQ-069 hard upper bound of 1 hour (3600 seconds) for access tokens
    ttl = min(int(ttl), 3600)

    now = datetime.now(timezone.utc)
    exp = now + timedelta(seconds=ttl)
    payload: Dict[str, Any] = {
        "sub": user_id,
        "email": email,
        "role": role,
        "type": "access",
        "jti": f"atk_{uuid.uuid4().hex}",
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(
        payload,
        secret,
        algorithm=alg,
    )
    return token, ttl


def create_refresh_token_with_metadata(
    user_id: str,
    email: str,
    role: str,
    expires_delta_seconds: int | None = None,
    settings: Settings | None = None,
) -> Tuple[str, str, datetime, datetime]:
    """Issue a signed JWT refresh token and return (token, jti, issued_at, expires_at) for DB persistence (F-03)."""
    if role not in VALID_USER_ROLES:
        raise ValueError(f"Invalid role '{role}'. Must be one of {VALID_USER_ROLES}.")

    cfg = settings or get_settings()
    alg = _resolve_algorithm(cfg)
    secret = _resolve_refresh_secret(cfg)
    ttl = (
        int(expires_delta_seconds)
        if expires_delta_seconds is not None
        else cfg.REFRESH_TOKEN_EXPIRE_SECONDS
    )
    now = datetime.now(timezone.utc)
    exp = now + timedelta(seconds=ttl)
    jti = f"rtk_{uuid.uuid4().hex}"
    payload: Dict[str, Any] = {
        "sub": user_id,
        "email": email,
        "role": role,
        "type": "refresh",
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(
        payload,
        secret,
        algorithm=alg,
    )
    return token, jti, now, exp


def create_refresh_token(
    user_id: str,
    email: str,
    role: str,
    expires_delta_seconds: int | None = None,
    settings: Settings | None = None,
) -> str:
    """Issue a signed JWT refresh token string."""
    token, _, _, _ = create_refresh_token_with_metadata(
        user_id=user_id,
        email=email,
        role=role,
        expires_delta_seconds=expires_delta_seconds,
        settings=settings,
    )
    return token


def decode_access_token(
    token: str, settings: Settings | None = None
) -> Dict[str, Any]:
    """Validate and decode a Bearer access JWT, requiring sub, role, type, jti, exp, and iat (F-03, F-04)."""
    cfg = settings or get_settings()
    alg = _resolve_algorithm(cfg)
    secret = _resolve_access_secret(cfg)
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[alg],
            options={"require": ["sub", "role", "type", "jti", "exp", "iat"]},
        )
    except ExpiredSignatureError as exc:
        raise AppException(
            message="Access token has expired.",
            status_code=401,
            code="TOKEN_EXPIRED",
        ) from exc
    except InvalidTokenError as exc:
        raise AppException(
            message="Invalid authentication token.",
            status_code=401,
            code="INVALID_TOKEN",
        ) from exc

    if payload.get("type") != "access":
        raise AppException(
            message="Invalid token type; access token required.",
            status_code=401,
            code="INVALID_TOKEN_TYPE",
        )

    jti = payload.get("jti")
    if not isinstance(jti, str) or not jti.strip():
        raise AppException(
            message="Token is missing a valid jti identifier.",
            status_code=401,
            code="INVALID_TOKEN",
        )

    role = payload.get("role")
    if role not in VALID_USER_ROLES:
        raise AppException(
            message="Token carries an unrecognized role.",
            status_code=401,
            code="INVALID_TOKEN_ROLE",
        )

    return payload


def decode_refresh_token(
    token: str, settings: Settings | None = None
) -> Dict[str, Any]:
    """Validate and decode a refresh JWT, requiring sub, role, type, jti, exp, and iat (F-03, F-04)."""
    cfg = settings or get_settings()
    alg = _resolve_algorithm(cfg)
    secret = _resolve_refresh_secret(cfg)
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[alg],
            options={"require": ["sub", "role", "type", "jti", "exp", "iat"]},
        )
    except ExpiredSignatureError as exc:
        raise AppException(
            message="Refresh token has expired.",
            status_code=401,
            code="REFRESH_TOKEN_EXPIRED",
        ) from exc
    except InvalidTokenError as exc:
        raise AppException(
            message="Invalid refresh token.",
            status_code=401,
            code="INVALID_REFRESH_TOKEN",
        ) from exc

    if payload.get("type") != "refresh":
        raise AppException(
            message="Invalid token type; refresh token required.",
            status_code=401,
            code="INVALID_TOKEN_TYPE",
        )

    jti = payload.get("jti")
    if not isinstance(jti, str) or not jti.strip():
        raise AppException(
            message="Refresh token is missing a valid jti identifier.",
            status_code=401,
            code="INVALID_REFRESH_TOKEN",
        )

    return payload


def has_permission(role: str, permission: str) -> bool:
    """Return True if the specified role grants the requested permission string."""
    allowed = ROLE_PERMISSIONS.get(role, ())
    return permission in allowed
