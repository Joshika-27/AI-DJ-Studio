"""Async SQLAlchemy engine, connection pooling, session management, and schema initialization (Module 2)."""

from collections.abc import AsyncGenerator
from typing import Any, Dict

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.base import Base

logger = get_logger("db")

_engine: AsyncEngine | None = None
_session_maker: async_sessionmaker[AsyncSession] | None = None


def _normalize_async_database_url(database_url: str | None) -> str:
    """Ensure the database URL uses an async SQLAlchemy driver (postgresql+asyncpg or sqlite+aiosqlite)."""
    if not database_url:
        # Safe in-memory fallback when DATABASE_URL env var is not configured
        return "sqlite+aiosqlite:///:memory:"
    url = database_url.strip()
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("sqlite:///"):
        return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    return url


def build_async_engine(
    database_url: str | None = None, echo: bool = False
) -> AsyncEngine:
    """Create an AsyncEngine configured with connection pooling for PostgreSQL or StaticPool for in-memory SQLite."""
    settings = get_settings()
    resolved_url = _normalize_async_database_url(database_url or settings.DATABASE_URL)

    engine_kwargs: Dict[str, Any] = {"echo": echo, "future": True}

    if resolved_url.startswith("sqlite"):
        if ":memory:" in resolved_url:
            engine_kwargs["poolclass"] = StaticPool
        engine_kwargs["connect_args"] = {"check_same_thread": False}
    else:
        # Connection pooling for PostgreSQL (PRD Section 9.1 / 9.3)
        engine_kwargs["pool_pre_ping"] = True
        engine_kwargs["pool_size"] = 10
        engine_kwargs["max_overflow"] = 20

    engine = create_async_engine(resolved_url, **engine_kwargs)

    if resolved_url.startswith("sqlite"):
        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_session_maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async_sessionmaker bound to the given AsyncEngine."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


def get_engine() -> AsyncEngine:
    """Return or initialize the global AsyncEngine."""
    global _engine, _session_maker
    if _engine is None:
        _engine = build_async_engine()
        _session_maker = create_session_maker(_engine)
    return _engine


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    """Return or initialize the global async_sessionmaker."""
    global _session_maker
    if _session_maker is None:
        get_engine()
    assert _session_maker is not None
    return _session_maker


def configure_database(database_url: str | None = None) -> AsyncEngine:
    """Reconfigure the global engine and session maker (useful for tests and custom environments)."""
    global _engine, _session_maker
    _engine = build_async_engine(database_url=database_url)
    _session_maker = create_session_maker(_engine)
    return _engine


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a scoped AsyncSession per request."""
    session_maker = get_session_maker()
    async with session_maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def init_db(engine: AsyncEngine | None = None) -> None:
    """Import all ORM models and create all database tables and indexes."""
    # Ensure all models are registered on Base.metadata
    import app.models  # noqa: F401

    target_engine = engine or get_engine()
    async with target_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema initialized", extra={"event": "db.schema.initialized"})


async def drop_db(engine: AsyncEngine | None = None) -> None:
    """Drop all database tables (used in isolated test tear-downs)."""
    import app.models  # noqa: F401

    target_engine = engine or get_engine()
    async with target_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def check_db_connection(engine: AsyncEngine | None = None) -> Dict[str, Any]:
    """Execute a lightweight SELECT 1 probe to verify database connectivity (REQ-079)."""
    target_engine = engine or get_engine()
    try:
        async with target_engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            scalar_val = result.scalar()
        return {
            "status": "connected" if scalar_val == 1 else "degraded",
            "dialect": target_engine.dialect.name,
        }
    except Exception as exc:
        logger.warning(
            "Database health probe failed",
            extra={
                "event": "db.health.failed",
                "payload": {"error": str(exc)},
            },
        )
        return {
            "status": "disconnected",
            "dialect": target_engine.dialect.name,
            "error": str(exc),
        }
