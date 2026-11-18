"""Track and TrackTimelineEvent ORM models (PRD Section 7.2.2, REQ-030, REQ-034, REQ-051, REQ-053)."""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JSONB_TYPE, utc_now

if TYPE_CHECKING:
    from app.models.interaction import Interaction
    from app.models.lounge import DJLounge
    from app.models.queue import QueueEntry
    from app.models.soundclash import SoundclashBattle, SoundclashCandidate

VALID_CAMELOT_KEYS = tuple(
    f"{num}{letter}" for letter in ("A", "B") for num in range(1, 13)
)
_CAMELOT_KEYS_SQL_LIST = ", ".join(f"'{k}'" for k in VALID_CAMELOT_KEYS)

VALID_TIMELINE_EVENT_TYPES = ("sample_credit", "trivia", "glossary")


class Track(Base):
    """Catalog track with indexed acoustic feature vector and RAG metadata timeline."""

    __tablename__ = "tracks"
    __table_args__ = (
        CheckConstraint("duration_seconds > 0", name="positive_duration"),
        CheckConstraint("bpm > 0", name="positive_bpm"),
        CheckConstraint(
            f"camelot_key IN ({_CAMELOT_KEYS_SQL_LIST})",
            name="valid_camelot_key",
        ),
        CheckConstraint(
            "energy >= 0.0 AND energy <= 1.0",
            name="energy_range",
        ),
        CheckConstraint(
            "valence >= 0.0 AND valence <= 1.0",
            name="valence_range",
        ),
        Index(
            "idx_tracks_acoustic",
            "camelot_key",
            "bpm",
            "energy",
            "valence",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: f"t_{uuid.uuid4().hex[:8]}",
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    artist: Mapped[str] = mapped_column(String(200), nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    audio_asset_url: Mapped[str] = mapped_column(String(500), nullable=False)
    bpm: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    camelot_key: Mapped[str] = mapped_column(String(4), nullable=False, index=True)
    energy: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    valence: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    # Relationships
    timeline_events: Mapped[List["TrackTimelineEvent"]] = relationship(
        "TrackTimelineEvent",
        back_populates="track",
        cascade="all, delete-orphan",
        order_by="TrackTimelineEvent.ts_seconds.asc()",
    )
    active_in_lounges: Mapped[List["DJLounge"]] = relationship(
        "DJLounge",
        back_populates="current_track",
    )
    queue_entries: Mapped[List["QueueEntry"]] = relationship(
        "QueueEntry",
        back_populates="track",
    )
    soundclash_candidates: Mapped[List["SoundclashCandidate"]] = relationship(
        "SoundclashCandidate",
        back_populates="track",
    )
    won_battles: Mapped[List["SoundclashBattle"]] = relationship(
        "SoundclashBattle",
        back_populates="winning_track",
    )
    interactions: Mapped[List["Interaction"]] = relationship(
        "Interaction",
        back_populates="track",
        cascade="all, delete-orphan",
    )


class TrackTimelineEvent(Base):
    """Timestamped track metadata entry feeding RAG transition scripts and listener Q&A."""

    __tablename__ = "track_timeline_events"
    __table_args__ = (
        CheckConstraint("ts_seconds >= 0", name="non_negative_ts_seconds"),
        CheckConstraint(
            "event_type IN ('sample_credit', 'trivia', 'glossary')",
            name="valid_timeline_event_type",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: f"evt_{uuid.uuid4().hex[:8]}",
    )
    track_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("tracks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ts_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    text_body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload: Mapped[Dict[str, Any]] = mapped_column(
        JSONB_TYPE,
        nullable=False,
        default=dict,
    )

    # Relationships
    track: Mapped["Track"] = relationship("Track", back_populates="timeline_events")
