"""QueueEntry and QueueEvent ORM models (PRD Section 7.2.2, REQ-051, REQ-053, REQ-054, REQ-056)."""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict
import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JSONB_TYPE, utc_now

if TYPE_CHECKING:
    from app.models.lounge import DJLounge
    from app.models.track import Track
    from app.models.user import User

VALID_QUEUE_EVENT_TYPES = (
    "vote",
    "segue",
    "battleResolved",
    "chat",
    "sentimentTick",
    "presence",
    "ai_qa",
    "lounge_started",
    "lounge_ended",
)


class QueueEntry(Base):
    """Next Up queue item ordered by sparse integer position (increments of 1000, REQ-054)."""

    __tablename__ = "queue_entries"
    __table_args__ = (
        CheckConstraint("vote_count >= 0", name="non_negative_vote_count"),
        Index("idx_queue_lounge_position", "lounge_id", "position"),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: f"qe_{uuid.uuid4().hex[:10]}",
    )
    lounge_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("dj_lounges.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    track_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("tracks.id"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1000,
    )
    vote_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    added_by_user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("users.id"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    # Relationships
    lounge: Mapped["DJLounge"] = relationship(
        "DJLounge",
        back_populates="queue_entries",
    )
    track: Mapped["Track"] = relationship(
        "Track",
        back_populates="queue_entries",
    )
    added_by_user: Mapped["User"] = relationship(
        "User",
        back_populates="added_queue_entries",
    )


class QueueEvent(Base):
    """Append-only audit and session analytics event log (PRD Section 7.2.1 & 9.4)."""

    __tablename__ = "queue_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('vote', 'segue', 'battleResolved', 'chat', 'sentimentTick', 'presence', 'ai_qa', 'lounge_started', 'lounge_ended')",
            name="valid_queue_event_type",
        ),
        Index("idx_queue_events_lounge_ts", "lounge_id", "ts"),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: f"qev_{uuid.uuid4().hex[:10]}",
    )
    lounge_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("dj_lounges.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("users.id"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[Dict[str, Any]] = mapped_column(
        JSONB_TYPE,
        nullable=False,
        default=dict,
    )
    ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    # Relationships
    lounge: Mapped["DJLounge"] = relationship(
        "DJLounge",
        back_populates="queue_events",
    )
    user: Mapped["User | None"] = relationship(
        "User",
        back_populates="queue_events",
    )
