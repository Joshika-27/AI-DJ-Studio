"""FastAPI authentication and 4-role server-side RBAC dependencies (REQ-069, REQ-070, REQ-094, Remediated F-07)."""

from typing import Callable, Sequence

from fastapi import Depends, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import AppException
from app.core.logging import get_logger
from app.core.security import decode_access_token, has_permission
from app.db.session import get_db
from app.models.user import User, VALID_USER_ROLES

logger = get_logger("auth.deps")

bearer_scheme = HTTPBearer(auto_error=False)


def get_app_settings(request: Request) -> Settings:
    """Resolve active Settings instance from app.state if overridden, else global get_settings()."""
    override = getattr(request.app.state, "settings", None)
    return override if isinstance(override, Settings) else get_settings()


async def get_bearer_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    """Extract Bearer token from Authorization header or raise 401 Unauthorized."""
    if (
        credentials is None
        or not credentials.scheme
        or credentials.scheme.lower() != "bearer"
        or not credentials.credentials
    ):
        raise AppException(
            message="Missing or invalid Authorization Bearer header.",
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="UNAUTHORIZED",
        )
    return credentials.credentials.strip()


async def get_current_user(
    token: str = Depends(get_bearer_token),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> User:
    """Validate JWT access token and load the authoritative User record from the database."""
    payload = decode_access_token(token, settings=settings)
    user_id = payload.get("sub")
    if not user_id:
        raise AppException(
            message="Invalid authentication token.",
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="INVALID_TOKEN",
        )

    stmt = select(User).where(User.id == str(user_id))
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user is None:
        raise AppException(
            message="Invalid authentication credentials.",
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="UNAUTHORIZED",
        )

    return user


def require_roles(
    *allowed_roles: str | Sequence[str],
) -> Callable[..., User]:
    """Build a server-side RBAC dependency rejecting unauthorized roles with a generic 403 response (REQ-070, F-07)."""
    flattened: list[str] = []
    for item in allowed_roles:
        if isinstance(item, str):
            flattened.append(item)
        else:
            flattened.extend(item)

    allowed_set = tuple(flattened)
    for role_name in allowed_set:
        if role_name not in VALID_USER_ROLES:
            raise ValueError(f"Unknown RBAC role '{role_name}' in require_roles().")

    async def _role_guard(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_set:
            logger.warning(
                "RBAC authorization denied",
                extra={
                    "event": "auth.rbac.denied",
                    "payload": {
                        "userId": current_user.id,
                        "userRole": current_user.role,
                        "allowedRoles": list(allowed_set),
                    },
                },
            )
            raise AppException(
                message="Insufficient permissions to access this resource.",
                status_code=status.HTTP_403_FORBIDDEN,
                code="FORBIDDEN",
                details=None,
            )
        return current_user

    return _role_guard


def require_permission(permission: str) -> Callable[..., User]:
    """Build a permission-based RBAC guard returning a generic 403 response on failure (F-07)."""

    async def _permission_guard(
        current_user: User = Depends(get_current_user),
    ) -> User:
        if not has_permission(current_user.role, permission):
            logger.warning(
                "Permission check denied",
                extra={
                    "event": "auth.permission.denied",
                    "payload": {
                        "userId": current_user.id,
                        "userRole": current_user.role,
                        "requiredPermission": permission,
                    },
                },
            )
            raise AppException(
                message="Insufficient permissions to access this resource.",
                status_code=status.HTTP_403_FORBIDDEN,
                code="FORBIDDEN",
                details=None,
            )
        return current_user

    return _permission_guard


# Convenience 4-tier RBAC dependencies (docs/PROJECT_REQUIREMENTS.md Section 4)
require_listener_or_above = require_roles("listener", "co_dj", "host", "admin")
require_co_dj_or_above = require_roles("co_dj", "host", "admin")
require_host_or_above = require_roles("host", "admin")
require_admin = require_roles("admin")
