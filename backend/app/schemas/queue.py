"""Queue Management and 3-Zone State Machine Pydantic Schemas (Module 7 — REQ-020, REQ-021, REQ-054, REQ-055, REQ-070)."""

from datetime import datetime, timezone
import math
from typing import Any, Dict, List, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.auth import sanitize_text_input
from app.schemas.track import _format_bpm_value


def format_iso_utc(dt: datetime | None) -> str | None:
    """Format a datetime as an ISO-8601 UTC string ending in 'Z'."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ==============================================================================
# Zone 1: Now Playing Zone Schema
# ==============================================================================

class NowPlayingTrackSchema(BaseModel):
    """Active track in the Lounge's Now Playing zone."""

    model_config = ConfigDict(extra="forbid")

    trackId: str
    title: str
    artist: str
    durationSeconds: int
    audioAssetUrl: str
    bpm: int | float
    camelotKey: str
    energy: float
    valence: float
    startedAt: str | None = None


# ==============================================================================
# Zone 2: Next Up Queue Entry Schema
# ==============================================================================

class QueueItemSchema(BaseModel):
    """Queued track in Next Up zone with sparse integer position and vote tally."""

    model_config = ConfigDict(extra="forbid")

    queueEntryId: str
    trackId: str
    title: str
    artist: str
    bpm: int | float
    camelotKey: str
    energy: float
    valence: float
    durationSeconds: int
    audioAssetUrl: str
    position: int
    voteCount: int
    addedByUserId: str
    insertionSeq: int
    createdAt: str


# ==============================================================================
# Zone 3: Soundclash Stage Zone Schema (Data Structure Only — Module 7)
# ==============================================================================

class SoundclashStageSchema(BaseModel):
    """Soundclash Stage state representation (Module 7: structural placeholder only)."""

    model_config = ConfigDict(extra="forbid")

    status: str = "idle"
    candidateTrackIds: List[str] = Field(default_factory=list)
    activeBattleId: str | None = None


# ==============================================================================
# Full 3-Zone Lounge Queue State Schema
# ==============================================================================

class ThreeZoneQueueStateSchema(BaseModel):
    """Authoritative 3-zone room queue state snapshot (REQ-020, REQ-055)."""

    model_config = ConfigDict(extra="forbid")

    loungeId: str
    status: str
    nowPlaying: NowPlayingTrackSchema | None = None
    nextUp: List[QueueItemSchema] = Field(default_factory=list)
    soundclashStage: SoundclashStageSchema = Field(default_factory=SoundclashStageSchema)
    version: int
    updatedAt: str


# ==============================================================================
# Request Schemas
# ==============================================================================

class QueueAddRequest(BaseModel):
    """Request payload to add a track to Next Up queue (REQ-020)."""

    model_config = ConfigDict(extra="forbid")

    trackId: str

    @field_validator("trackId")
    @classmethod
    def validate_track_id(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("trackId must be a string.")
        cleaned = sanitize_text_input(value)
        if not cleaned or " " in cleaned or "/" in cleaned or "\\" in cleaned:
            raise ValueError("trackId must be a valid non-empty track identifier.")
        return cleaned


class QueueVoteRequest(BaseModel):
    """Request payload to upvote a track in Next Up queue (REQ-021)."""

    model_config = ConfigDict(extra="forbid")

    trackId: str

    @field_validator("trackId")
    @classmethod
    def validate_track_id(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("trackId must be a string.")
        cleaned = sanitize_text_input(value)
        if not cleaned or " " in cleaned or "/" in cleaned or "\\" in cleaned:
            raise ValueError("trackId must be a valid non-empty track identifier.")
        return cleaned


class QueueOverrideRequest(BaseModel):
    """Request payload for Host/Co-DJ manual queue overrides (REQ-015, REQ-070)."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["promote", "remove", "force_segue"]
    trackId: str | None = None
    targetPosition: int | None = None

    @field_validator("trackId")
    @classmethod
    def validate_track_id(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("trackId must be a string.")
        cleaned = sanitize_text_input(value)
        if not cleaned or " " in cleaned or "/" in cleaned or "\\" in cleaned:
            raise ValueError("trackId must be a valid track identifier.")
        return cleaned

    @field_validator("targetPosition")
    @classmethod
    def validate_target_position(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("targetPosition must be an integer.")
        if value <= 0:
            raise ValueError("targetPosition must be a strictly positive integer.")
        return value

    def model_post_init(self, __context: Any) -> None:
        if self.action in ("promote", "remove") and not self.trackId:
            raise ValueError(f"trackId is required when action is '{self.action}'.")


# ==============================================================================
# Response Schemas
# ==============================================================================

class QueueResponse(BaseModel):
    """Response returned upon queue operations."""

    model_config = ConfigDict(extra="forbid")

    loungeId: str
    queue: List[QueueItemSchema]
    nowPlaying: NowPlayingTrackSchema | None = None
    version: int
