"""ChatMessage ORM model (PRD Section 7.2.2, REQ-023, REQ-035, REQ-037, REQ-051, REQ-053)."""

from datetime import datetime
from typing import TYPE_CHECKING, List
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JSONB_TYPE, utc_now

if TYPE_CHECKING:
    from app.models.lounge import DJLounge
    from app.models.user import User

VALID_MESSAGE_TYPES = ("chat", "ai_answer", "system", "dj_announcement")
VALID_SENTIMENT_BUCKETS = ("positive", "neutral", "negative")


class ChatMessage(Base):
    """Persisted room chat message, AI DJ Q&A response, or transition announcement."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint(
            "msg_type IN ('chat', 'ai_answer', 'system', 'dj_announcement')",
            name="valid_msg_type",
        ),
        CheckConstraint(
            "sentiment_score IS NULL OR (sentiment_score >= 0.0 AND sentiment_score <= 1.0)",
            name="valid_sentiment_score",
        ),
        CheckConstraint(
            "sentiment_bucket IS NULL OR sentiment_bucket IN ('positive', 'neutral', 'negative')",
            name="valid_sentiment_bucket",
        ),
        CheckConstraint(
            "ts_seconds >= 0",
            name="non_negative_msg_ts_seconds",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: f"msg_{uuid.uuid4().hex[:10]}",
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
    msg_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="chat",
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    sentiment_bucket: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
    )
    source_events: Mapped[List[str]] = mapped_column(
        JSONB_TYPE,
        nullable=False,
        default=list,
    )
    ts_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    # Relationships
    lounge: Mapped["DJLounge"] = relationship(
        "DJLounge",
        back_populates="chat_messages",
    )
    user: Mapped["User | None"] = relationship(
        "User",
        back_populates="chat_messages",
    )
