"""FastAPI application entry point and factory for AI DJ Studio (Module 1)."""

import re
import time
from typing import Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.auth import router as auth_router
from app.api.v1.health import router as health_router
from app.api.v1.tracks import router as tracks_router
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import (
    clear_log_context,
    configure_logging,
    get_logger,
    set_log_context,
)
from app.seeds.seed_data import AUDIO_ASSETS_DIR

_LOUNGE_PATH_REGEX = re.compile(r"/lounges/([^/]+)")


def _extract_lounge_id(request: Request) -> str | None:
    header_lounge_id = request.headers.get("X-Lounge-ID")
    if header_lounge_id:
        return header_lounge_id.strip()
    match = _LOUNGE_PATH_REGEX.search(request.url.path)
    if match:
        return match.group(1)
    return None


def create_app(settings_override: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI modular monolith application instance."""
    settings = settings_override or get_settings()
    configure_logging(settings.LOG_LEVEL)
    logger = get_logger("main")

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=(
            "AI DJ Studio: Collaborative Live Queue Curation & Sentiment-Aware Audio Engine"
        ),
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.state.settings = settings

    # 1. Configure CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Correlation-ID", "X-Lounge-ID", "X-Process-Time-Ms"],
    )

    # 2. Correlation ID, Lounge ID context & request latency logging middleware (REQ-074, REQ-077)
    @app.middleware("http")
    async def correlation_and_logging_middleware(
        request: Request, call_next: Callable
    ) -> Response:
        start_perf = time.perf_counter()
        incoming_corr_id = request.headers.get("X-Correlation-ID")
        lounge_id = _extract_lounge_id(request)
        correlation_id = set_log_context(
            correlation_id=incoming_corr_id, lounge_id=lounge_id
        )

        try:
            response: Response = await call_next(request)
            elapsed_ms = round((time.perf_counter() - start_perf) * 1000.0, 2)
            response.headers["X-Correlation-ID"] = correlation_id
            response.headers["X-Process-Time-Ms"] = str(elapsed_ms)
            if lounge_id:
                response.headers["X-Lounge-ID"] = lounge_id

            logger.info(
                f"{request.method} {request.url.path} -> {response.status_code} ({elapsed_ms} ms)",
                extra={
                    "event": "http.request.completed",
                    "payload": {
                        "method": request.method,
                        "path": request.url.path,
                        "statusCode": response.status_code,
                        "durationMs": elapsed_ms,
                    },
                },
            )
            return response
        finally:
            clear_log_context()

    # 3. Centralized error handlers
    register_exception_handlers(app)

    # 4. Mount static /assets/audio/* without import-time or startup-time filesystem generation side effects
    app.mount(
        "/assets/audio",
        StaticFiles(directory=str(AUDIO_ASSETS_DIR), check_dir=False),
        name="audio_assets",
    )

    # 5. Register API v1 routers (Module 1: Health, Module 3: Auth & RBAC, Module 4: Tracks)
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(tracks_router, prefix="/api/v1")

    return app


app = create_app()
