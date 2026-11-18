"""Pydantic request and response validation schemas package."""

from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    LogoutResponse,
    RefreshRequest,
    RefreshResponse,
    RegisterRequest,
    RegisterResponse,
    UserPreferencesSchema,
    UserProfileResponse,
)

__all__ = [
    "LoginRequest",
    "LoginResponse",
    "LogoutRequest",
    "LogoutResponse",
    "RefreshRequest",
    "RefreshResponse",
    "RegisterRequest",
    "RegisterResponse",
    "UserPreferencesSchema",
    "UserProfileResponse",
]
