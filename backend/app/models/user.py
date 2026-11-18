"""User ORM model (PRD Section 7.2.2, REQ-051, REQ-053, REQ-070)."""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List
import uuid

from sqlalchemy import CheckConstraint, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JSONB_TYPE, utc_now

if TYPE_CHECKING:
    from app.models.chat import ChatMessage
    from app.models.interaction import Interaction
    from app.models.lounge import DJLounge
    from app.models.queue import QueueEntry, QueueEvent
    from app.models.soundclash import SoundclashCandidate

VALID_USER_ROLES = ("listener", "co_dj", "host", "admin")


class User(Base):
    """Registered user account with four-role RBAC enforcement."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('listener', 'co_dj', 'host', 'admin')",
            name="valid_user_role",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: f"u_{uuid.uuid4().hex[:10]}",
    )
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="listener",
    )
    preferences: Mapped[Dict[str, Any]] = mapped_column(
        JSONB_TYPE,
        nullable=False,
        default=lambda: {"genreAffinity": []},
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    # Relationships
    hosted_lounges: Mapped[List["DJLounge"]] = relationship(
        "DJLounge",
        back_populates="host_user",
        foreign_keys="DJLounge.host_user_id",
    )
    added_queue_entries: Mapped[List["QueueEntry"]] = relationship(
        "QueueEntry",
        back_populates="added_by_user",
    )
    submitted_candidates: Mapped[List["SoundclashCandidate"]] = relationship(
        "SoundclashCandidate",
        back_populates="submitted_by_user",
    )
    chat_messages: Mapped[List["ChatMessage"]] = relationship(
        "ChatMessage",
        back_populates="user",
    )
    interactions: Mapped[List["Interaction"]] = relationship(
        "Interaction",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    queue_events: Mapped[List["QueueEvent"]] = relationship(
        "QueueEvent",
        back_populates="user",
    )
