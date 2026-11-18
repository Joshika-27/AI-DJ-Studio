"""Authentication and RBAC Pydantic schemas (Remediated F-01, F-02, F-03, F-06)."""

from datetime import datetime
import re
from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.security import (
    MAX_BCRYPT_PASSWORD_BYTES,
    ROLE_PERMISSIONS,
    sanitize_text_input,
    validate_password_bytes,
)
from app.models.user import User

_EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _validate_raw_email(value: str) -> str:
    """Validate email strictly without stripping HTML tags out of malformed inputs (F-06)."""
    if not isinstance(value, str):
        raise ValueError("Email must be a string.")
    raw = value.strip()
    if "<" in raw or ">" in raw or "\x00" in raw or "&lt;" in raw.lower() or "&gt;" in raw.lower():
        raise ValueError("Email contains forbidden HTML or control characters.")
    normalized = raw.lower()
    if not _EMAIL_REGEX.match(normalized):
        raise ValueError("Invalid email address format.")
    return normalized


class UserPreferencesSchema(BaseModel):
    """Typed, bounded user preferences schema preventing arbitrary nested JSON injection (F-06)."""

    model_config = ConfigDict(extra="forbid")

    genreAffinity: List[str] = Field(default_factory=list, max_length=20)

    @field_validator("genreAffinity")
    @classmethod
    def validate_genre_items(cls, items: List[str]) -> List[str]:
        if len(items) > 20:
            raise ValueError("genreAffinity cannot contain more than 20 genres.")
        cleaned_list: List[str] = []
        for item in items:
            if not isinstance(item, str):
                raise ValueError("Each genre in genreAffinity must be a string.")
            if "<" in item or ">" in item:
                raise ValueError("Genre names cannot contain HTML or angle brackets.")
            sanitized = sanitize_text_input(item)
            if not sanitized or len(sanitized) > 50:
                raise ValueError("Each genre must be between 1 and 50 characters.")
            cleaned_list.append(sanitized)
        return cleaned_list


class RegisterRequest(BaseModel):
    """Payload for POST /api/v1/auth/register (F-01: role cannot be supplied by client)."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., min_length=5, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)
    displayName: str = Field(..., min_length=1, max_length=100)
    preferences: UserPreferencesSchema = Field(default_factory=UserPreferencesSchema)

    @field_validator("email")
    @classmethod
    def validate_and_normalize_email(cls, value: str) -> str:
        return _validate_raw_email(value)

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        validate_password_bytes(value)
        return value

    @field_validator("displayName")
    @classmethod
    def validate_and_sanitize_display_name(cls, value: str) -> str:
        sanitized = sanitize_text_input(value)
        if len(sanitized) < 2 or len(sanitized) > 100:
            raise ValueError(
                "displayName must contain between 2 and 100 valid characters after sanitization."
            )
        return sanitized


class LoginRequest(BaseModel):
    """Payload for POST /api/v1/auth/login (F-02, F-06)."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., min_length=5, max_length=255)
    password: str = Field(..., min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_login_email(cls, value: str) -> str:
        return _validate_raw_email(value)

    @field_validator("password")
    @classmethod
    def validate_login_password_bytes(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Password contains invalid control characters.")
        if len(value.encode("utf-8")) > MAX_BCRYPT_PASSWORD_BYTES:
            raise ValueError(
                f"Password cannot exceed {MAX_BCRYPT_PASSWORD_BYTES} UTF-8 bytes."
            )
        return value


class RefreshRequest(BaseModel):
    """Payload for POST /api/v1/auth/refresh."""

    model_config = ConfigDict(extra="forbid")

    refreshToken: str = Field(..., min_length=10)


class LogoutRequest(BaseModel):
    """Payload for POST /api/v1/auth/logout (F-03)."""

    model_config = ConfigDict(extra="forbid")

    refreshToken: str = Field(..., min_length=10)


class UserProfileResponse(BaseModel):
    """Serialized authenticated user representation."""

    id: str
    email: str
    displayName: str
    role: str
    preferences: Dict[str, Any]
    permissions: List[str]
    createdAt: datetime

    @classmethod
    def from_orm_user(cls, user: User) -> "UserProfileResponse":
        return cls(
            id=user.id,
            email=user.email,
            displayName=user.display_name,
            role=user.role,
            preferences=user.preferences or {"genreAffinity": []},
            permissions=list(ROLE_PERMISSIONS.get(user.role, ())),
            createdAt=user.created_at,
        )


class RegisterResponse(BaseModel):
    """Response for POST /api/v1/auth/register (201 Created)."""

    userId: str
    email: str
    displayName: str
    role: str
    accessToken: str
    refreshToken: str
    tokenType: str = "bearer"
    expiresIn: int
    user: UserProfileResponse


class LoginResponse(BaseModel):
    """Response for POST /api/v1/auth/login (200 OK)."""

    accessToken: str
    refreshToken: str
    tokenType: str = "bearer"
    expiresIn: int
    user: UserProfileResponse


class RefreshResponse(BaseModel):
    """Response for POST /api/v1/auth/refresh with single-use rotated refreshToken (F-03)."""

    accessToken: str
    refreshToken: str
    tokenType: str = "bearer"
    expiresIn: int


class LogoutResponse(BaseModel):
    """Response for POST /api/v1/auth/logout (200 OK)."""

    revoked: bool
    message: str
