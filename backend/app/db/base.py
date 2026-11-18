"""Declarative base and cross-dialect PostgreSQL JSONB column type (Module 2)."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Uses native PostgreSQL JSONB in production while supporting standard JSON in SQLite test harnesses
JSONB_TYPE: Any = JSON().with_variant(JSONB(), "postgresql")


def utc_now() -> datetime:
    """Return timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 Declarative Base with deterministic constraint naming convention."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
