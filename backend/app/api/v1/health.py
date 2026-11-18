"""Health-check endpoints for API layer and Queue Gateway readiness (REQ-079)."""

import time
from typing import Any, Dict

from fastapi import APIRouter

from app.core.config import get_settings
from app.core.logging import get_correlation_id
from app.db.session import check_db_connection

router = APIRouter(tags=["Health"])

_PROCESS_START_MONOTONIC = time.monotonic()


@router.get("/health", summary="API and Service Health Check")
async def get_health() -> Dict[str, Any]:
    """Return service readiness status suitable for container orchestrators or uptime checks."""
    settings = get_settings()
    db_health = await check_db_connection()
    uptime_seconds = round(time.monotonic() - _PROCESS_START_MONOTONIC, 3)
    return {
        "status": "ok" if db_health["status"] == "connected" else "degraded",
        "service": "ai-dj-studio",
        "appName": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.APP_ENV,
        "db": db_health["status"],
        "dbDialect": db_health["dialect"],
        "wsGateway": "ready",
        "uptimeSeconds": uptime_seconds,
        "correlationId": get_correlation_id(),
    }


@router.get("/health/ws", summary="WebSocket Queue Gateway Readiness Check")
async def get_ws_gateway_health() -> Dict[str, Any]:
    """Return WebSocket Queue Gateway health status (REQ-079)."""
    uptime_seconds = round(time.monotonic() - _PROCESS_START_MONOTONIC, 3)
    return {
        "status": "ok",
        "gateway": "ws_queue_state_gateway",
        "wsGateway": "ready",
        "uptimeSeconds": uptime_seconds,
        "correlationId": get_correlation_id(),
    }
