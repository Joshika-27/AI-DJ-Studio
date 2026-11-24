"""DJLounge ORM model (PRD Section 7.2.2, REQ-007, REQ-019, REQ-025, REQ-051, REQ-053)."""

from datetime import datetime
from typing import TYPE_CHECKING, List
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.chat import ChatMessage
    from app.models.interaction import Interaction
    from app.models.queue import QueueEntry, QueueEvent
    from app.models.soundclash import SoundclashBattle
    from app.models.track import Track
    from app.models.user import User

VALID_LOUNGE_STATUSES = ("scheduled", "live", "ended")
VALID_AI_VERBOSITIES = ("low", "normal", "high")


class DJLounge(Base):
    """Collaborative listening room owned by a Host user."""

    __tablename__ = "dj_lounges"
    __table_args__ = (
        CheckConstraint(
            "status IN ('scheduled', 'live', 'ended')",
            name="valid_lounge_status",
        ),
        CheckConstraint(
            "max_participants >= 2 AND max_participants <= 50",
            name="valid_max_participants",
        ),
        CheckConstraint(
            "bpm_tolerance_pct > 0",
            name="positive_bpm_tolerance_pct",
        ),
        CheckConstraint(
            "ai_verbosity IN ('low', 'normal', 'high')",
            name="valid_ai_verbosity",
        ),
        CheckConstraint(
            "peak_participants >= 1",
            name="positive_peak_participants",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: f"lg_{uuid.uuid4().hex[:8]}",
    )
    invite_code: Mapped[str] = mapped_column(
        String(16),
        unique=True,
        nullable=False,
        index=True,
    )
    host_user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    vibe_profile: Mapped[str] = mapped_column(String(100), nullable=False)
    current_track_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("tracks.id"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="scheduled",
        index=True,
    )
    max_participants: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=50,
    )
    bpm_tolerance_pct: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=8.0,
    )
    soundclash_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    ai_verbosity: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="normal",
    )
    peak_participants: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    is_locked: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    queue_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    host_user: Mapped["User"] = relationship(
        "User",
        back_populates="hosted_lounges",
        foreign_keys=[host_user_id],
    )
    current_track: Mapped["Track | None"] = relationship(
        "Track",
        back_populates="active_in_lounges",
        foreign_keys=[current_track_id],
    )
    queue_entries: Mapped[List["QueueEntry"]] = relationship(
        "QueueEntry",
        back_populates="lounge",
        cascade="all, delete-orphan",
        order_by="QueueEntry.position.asc()",
    )
    soundclash_battles: Mapped[List["SoundclashBattle"]] = relationship(
        "SoundclashBattle",
        back_populates="lounge",
        cascade="all, delete-orphan",
    )
    chat_messages: Mapped[List["ChatMessage"]] = relationship(
        "ChatMessage",
        back_populates="lounge",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at.asc()",
    )
    queue_events: Mapped[List["QueueEvent"]] = relationship(
        "QueueEvent",
        back_populates="lounge",
        cascade="all, delete-orphan",
        order_by="QueueEvent.ts.asc()",
    )
    interactions: Mapped[List["Interaction"]] = relationship(
        "Interaction",
        back_populates="lounge",
    )
