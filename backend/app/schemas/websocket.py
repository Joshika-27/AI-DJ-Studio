"""Pydantic schemas and envelope constructors for Real-Time WebSocket traffic (Module 8 — PRD Section 7.3.4, REQ-060..REQ-068, REQ-078)."""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from app.core.security import sanitize_text_input

from app.schemas.queue import (
    NowPlayingTrackSchema,
    QueueItemSchema,
    QueueVoteRequest,
)


def make_ws_envelope(
    event: str,
    lounge_id: str,
    payload: Dict[str, Any],
    ts: Optional[int] = None,
) -> Dict[str, Any]:
    """Construct a canonical WebSocket envelope dictionary (REQ-060).

    Format: {"event": "<name>", "loungeId": "<id>", "payload": { ... }, "ts": <epoch_ms>}
    """
    if ts is None:
        ts = int(time.time() * 1000)
    return {
        "event": event,
        "loungeId": lounge_id,
        "payload": payload,
        "ts": ts,
    }


class WebSocketEnvelopeSchema(BaseModel):
    """Canonical WebSocket envelope contract (PRD Section 7.3.4, REQ-060)."""

    model_config = ConfigDict(extra="forbid")

    event: str = Field(..., description="Canonical event name, e.g. lounge.state.snapshot")
    loungeId: str = Field(..., description="Unique DJ Lounge identifier")
    payload: Dict[str, Any] = Field(default_factory=dict, description="Event payload dictionary")
    ts: int = Field(..., description="Epoch millisecond timestamp")


class SoundclashSnapshotSchema(BaseModel):
    """Active or idle Soundclash battle state in the mid-session snapshot."""

    model_config = ConfigDict(extra="forbid")

    active: bool = False
    battleId: Optional[str] = None
    candidates: List[str] = Field(default_factory=list)
    votes: Dict[str, int] = Field(default_factory=dict)
    closesAt: Optional[int] = None


class SentimentSnapshotSchema(BaseModel):
    """Rolling sentiment score state in the mid-session snapshot."""

    model_config = ConfigDict(extra="forbid")

    rollingScore: float = 0.5
    windowSeconds: int = 60
    sampleSize: int = 0


class LoungeStateSnapshotPayload(BaseModel):
    """Immediate mid-session snapshot payload delivered within < 2s of connecting (REQ-028)."""

    model_config = ConfigDict(extra="forbid")

    nowPlaying: Optional[Dict[str, Any]] = None
    queue: List[Dict[str, Any]] = Field(default_factory=list)
    soundclash: Dict[str, Any] = Field(default_factory=dict)
    sentiment: Dict[str, Any] = Field(default_factory=dict)
    participants: List[Dict[str, Any]] = Field(default_factory=list)
    chatHistory: List[Dict[str, Any]] = Field(default_factory=list)
    hostStatus: str = "connected"


class LoungePresenceUpdatePayload(BaseModel):
    """Presence update broadcast payload (REQ-062)."""

    model_config = ConfigDict(extra="forbid")

    participantId: str
    action: str  # "joined" | "left"
    participantCount: int
    displayName: Optional[str] = None


class LoungeSyncPingPayload(BaseModel):
    """Client-to-server latency ping payload (REQ-068)."""

    model_config = ConfigDict(extra="forbid")

    clientSentAt: int


class LoungeSyncPongPayload(BaseModel):
    """Server-to-client latency pong reply payload (REQ-068)."""

    model_config = ConfigDict(extra="forbid")

    clientSentAt: int
    serverReceivedAt: int


class LoungeErrorPayload(BaseModel):
    """Structured WebSocket error payload."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class LoungeQueueUpdatePayload(BaseModel):
    """Payload for lounge.queue.update broadcasts (REQ-061)."""

    model_config = ConfigDict(extra="forbid")

    queue: List[QueueItemSchema]
    nowPlaying: Optional[NowPlayingTrackSchema] = None
    reason: Literal["vote", "add", "segue", "battle", "override"]
    version: int


class LoungeQueueVotePayload(QueueVoteRequest):
    """Payload for client-to-server lounge.queue.vote event (REQ-061, API_DESIGN Section 2.3)."""

    model_config = ConfigDict(extra="forbid")


MAX_CHAT_MESSAGE_LENGTH = 500


class LoungeChatMessagePayload(BaseModel):
    """Payload for client-to-server lounge.chat.message event (PRD Section 7.3.4, REQ-023, REQ-063)."""

    model_config = ConfigDict(extra="forbid")

    body: str = Field(..., description="Sanitized text content of the chat message")

    @field_validator("body")
    @classmethod
    def validate_and_sanitize_body(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("Message body must be a string.")
        cleaned = sanitize_text_input(value)
        cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()
        if not cleaned:
            raise ValueError("Message body cannot be empty or contain solely whitespace/tags.")
        if len(cleaned) > MAX_CHAT_MESSAGE_LENGTH:
            raise ValueError(
                f"Message body cannot exceed {MAX_CHAT_MESSAGE_LENGTH} characters."
            )
        return cleaned


class LoungeChatBroadcastPayload(BaseModel):
    """Payload for server-to-client lounge.chat.message broadcasts (PRD Section 7.3.4, REQ-023)."""

    model_config = ConfigDict(extra="forbid")

    messageId: str
    userId: str
    displayName: Optional[str] = None
    body: str
    sentimentBucket: Optional[str] = None
    tsSeconds: int
    createdAt: str


class LoungeChatTypingPayload(BaseModel):
    """Payload for client-to-server lounge.chat.typing event."""

    model_config = ConfigDict(extra="forbid")

    isTyping: Optional[StrictBool] = None
    action: Optional[Literal["started", "stopped", "start", "stop"]] = None

    def model_post_init(self, __context: Any) -> None:
        if self.isTyping is None and self.action is None:
            raise ValueError("Either 'isTyping' or 'action' must be provided.")


class LoungeChatTypingBroadcastPayload(BaseModel):
    """Payload for server-to-client lounge.chat.typing broadcasts."""

    model_config = ConfigDict(extra="forbid")

    userId: str
    displayName: Optional[str] = None
    isTyping: bool
    action: Literal["started", "stopped"]


MAX_REACTION_EMOJI_LENGTH = 16


class LoungeChatReactionPayload(BaseModel):
    """Payload for client-to-server lounge.chat.reaction event (Module 8.3.3)."""

    model_config = ConfigDict(extra="forbid")

    messageId: str = Field(..., description="ID of the target chat message being reacted to")
    emoji: str = Field(..., description="Unicode emoji character sequence (e.g. 🔥, ❤️, 👏)")
    action: Optional[Literal["add", "remove", "toggle"]] = Field(
        default="add",
        description="Reaction action (default: add)",
    )

    @field_validator("messageId")
    @classmethod
    def validate_message_id(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("messageId must be a string.")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("messageId cannot be empty.")
        return cleaned

    @field_validator("emoji")
    @classmethod
    def validate_emoji(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("emoji must be a string.")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("emoji cannot be empty.")
        if len(cleaned) > MAX_REACTION_EMOJI_LENGTH:
            raise ValueError(
                f"emoji cannot exceed {MAX_REACTION_EMOJI_LENGTH} characters."
            )
        # Disallow ASCII alphanumeric, spaces, and HTML/script injection characters
        if re.search(r"[a-zA-Z0-9\s<>&\"'/]", cleaned):
            raise ValueError(
                "Invalid emoji: must be a unicode emoji and cannot contain alphanumeric, whitespace, or HTML characters."
            )
        return cleaned


class LoungeChatReactionBroadcastPayload(BaseModel):
    """Payload for server-to-client lounge.chat.reaction broadcasts (Module 8.3.3)."""

    model_config = ConfigDict(extra="forbid")

    messageId: str
    userId: str
    displayName: Optional[str] = None
    emoji: str
    action: Literal["add", "remove"]


class LoungeHostStatusPayload(BaseModel):
    """Payload for server-to-room lounge.host.status broadcast (Module 8.4 — REQ-029)."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["host_disconnected", "host_reconnected", "co_dj_promoted"]
    activeHostUserId: Optional[str] = None
    mutationsFrozen: bool = False
