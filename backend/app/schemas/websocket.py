"""Pydantic schemas and envelope constructors for Real-Time WebSocket traffic (Module 8 — PRD Section 7.3.4, REQ-060..REQ-068, REQ-078)."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.queue import NowPlayingTrackSchema, QueueItemSchema


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
