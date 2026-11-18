"""SoundclashBattle and SoundclashCandidate ORM models (PRD Section 7.2.2, REQ-022, REQ-038, REQ-051, REQ-053)."""

from datetime import datetime
from typing import TYPE_CHECKING, List
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.lounge import DJLounge
    from app.models.track import Track
    from app.models.user import User

VALID_BATTLE_STATUSES = ("open", "resolved")


class SoundclashBattle(Base):
    """Timed crowd battle between 2 to 4 candidate tracks for the Next Up slot."""

    __tablename__ = "soundclash_battles"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'resolved')",
            name="valid_battle_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: f"bt_{uuid.uuid4().hex[:8]}",
    )
    lounge_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("dj_lounges.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    closes_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="open",
    )
    winning_track_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("tracks.id"),
        nullable=True,
    )

    # Relationships
    lounge: Mapped["DJLounge"] = relationship(
        "DJLounge",
        back_populates="soundclash_battles",
    )
    winning_track: Mapped["Track | None"] = relationship(
        "Track",
        back_populates="won_battles",
    )
    candidates: Mapped[List["SoundclashCandidate"]] = relationship(
        "SoundclashCandidate",
        back_populates="battle",
        cascade="all, delete-orphan",
    )


class SoundclashCandidate(Base):
    """Candidate track submitted into a SoundclashBattle (2..4 candidates per battle)."""

    __tablename__ = "soundclash_candidates"
    __table_args__ = (
        CheckConstraint(
            "vote_count >= 0",
            name="non_negative_candidate_votes",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: f"sc_{uuid.uuid4().hex[:8]}",
    )
    battle_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("soundclash_battles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    track_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("tracks.id"),
        nullable=False,
    )
    submitted_by_user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("users.id"),
        nullable=False,
    )
    vote_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    # Relationships
    battle: Mapped["SoundclashBattle"] = relationship(
        "SoundclashBattle",
        back_populates="candidates",
    )
    track: Mapped["Track"] = relationship(
        "Track",
        back_populates="soundclash_candidates",
    )
    submitted_by_user: Mapped["User"] = relationship(
        "User",
        back_populates="submitted_candidates",
    )
