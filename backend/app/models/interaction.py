"""Interaction ORM model feeding the Hybrid Recommendation Engine (PRD Section 7.2.2, REQ-044, REQ-051, REQ-053)."""

from datetime import datetime
from typing import TYPE_CHECKING, List
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JSONB_TYPE, utc_now

if TYPE_CHECKING:
    from app.models.lounge import DJLounge
    from app.models.track import Track
    from app.models.user import User


class Interaction(Base):
    """Co-listening and track playback interaction record for recommendation training."""

    __tablename__ = "interactions"
    __table_args__ = (
        CheckConstraint(
            "played_seconds >= 0",
            name="non_negative_played_seconds",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: f"int_{uuid.uuid4().hex[:10]}",
    )
    user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    track_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("tracks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    lounge_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("dj_lounges.id", ondelete="SET NULL"),
        nullable=True,
    )
    played_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    completed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    liked: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    co_listened_with: Mapped[List[str]] = mapped_column(
        JSONB_TYPE,
        nullable=False,
        default=list,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="interactions")
    track: Mapped["Track"] = relationship("Track", back_populates="interactions")
    lounge: Mapped["DJLounge | None"] = relationship(
        "DJLounge",
        back_populates="interactions",
    )
