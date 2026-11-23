"""Pydantic schemas for DJ Lounge lifecycle, invite-code join flow, and Host moderation (Module 5/6 — PRD Section 7.3.1, REQ-015, REQ-016, REQ-019, REQ-025, REQ-057)."""

from datetime import datetime, timezone
import math
from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.security import sanitize_text_input
from app.models.lounge import VALID_AI_VERBOSITIES
from app.schemas.track import _format_bpm_value

VALID_MODERATION_ACTIONS = (
    "mute",
    "unmute",
    "remove",
    "lock",
    "unlock",
    "set_role",
)
VALID_ASSIGNABLE_LOUNGE_ROLES = ("listener", "co_dj", "host")


def format_iso_utc(dt: datetime | None) -> str | None:
    """Format a datetime as an ISO-8601 UTC string ending with 'Z' (e.g. '2026-09-18T10:15:00Z') to match PRD Section 7.3.1."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class LoungeCreateRequest(BaseModel):
    """Request payload for POST /api/v1/lounges (PRD Section 7.3.1, REQ-019, REQ-057)."""

    model_config = ConfigDict(extra="forbid")

    seedTrackId: str = Field(..., min_length=1, max_length=64)
    vibeProfile: str = Field(..., min_length=1, max_length=100)
    maxParticipants: int = Field(default=40, ge=2, le=50)
    bpmTolerancePct: int | float = Field(default=8, gt=0, le=50)
    soundclashEnabled: bool = Field(default=True)
    aiVerbosity: str = Field(default="normal")

    @field_validator("seedTrackId")
    @classmethod
    def validate_seed_track_id(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("seedTrackId must be a string.")
        cleaned = sanitize_text_input(value)
        if not cleaned or " " in cleaned or "/" in cleaned or "\\" in cleaned:
            raise ValueError("seedTrackId must be a valid track identifier.")
        return cleaned

    @field_validator("vibeProfile")
    @classmethod
    def validate_vibe_profile(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("vibeProfile must be a string.")
        cleaned = sanitize_text_input(value)
        if len(cleaned) < 2:
            raise ValueError(
                "vibeProfile must contain at least 2 valid characters after sanitization."
            )
        return cleaned

    @field_validator("maxParticipants")
    @classmethod
    def validate_max_participants(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("maxParticipants must be an integer between 2 and 50.")
        if value < 2 or value > 50:
            raise ValueError("maxParticipants must be between 2 and 50.")
        return value

    @field_validator("bpmTolerancePct")
    @classmethod
    def validate_bpm_tolerance_pct(cls, value: int | float) -> int | float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("bpmTolerancePct must be a positive number.")
        fval = float(value)
        if math.isnan(fval) or math.isinf(fval) or fval <= 0.0 or fval > 50.0:
            raise ValueError("bpmTolerancePct must be greater than 0 and at most 50.")
        return _format_bpm_value(fval)

    @field_validator("soundclashEnabled")
    @classmethod
    def validate_soundclash_enabled(cls, value: bool) -> bool:
        if not isinstance(value, bool):
            raise ValueError("soundclashEnabled must be a boolean.")
        return value

    @field_validator("aiVerbosity")
    @classmethod
    def validate_ai_verbosity(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("aiVerbosity must be a string.")
        normalized = value.strip().lower()
        if normalized not in VALID_AI_VERBOSITIES:
            raise ValueError(
                f"aiVerbosity must be one of {list(VALID_AI_VERBOSITIES)}."
            )
        return normalized


class LoungeCreateResponse(BaseModel):
    """Exact response contract for POST /api/v1/lounges (PRD Section 7.3.1, REQ-057)."""

    model_config = ConfigDict(extra="forbid")

    loungeId: str
    inviteCode: str
    status: str
    hostUserId: str
    createdAt: str


class LoungeSettingsSchema(BaseModel):
    """Configured room rules for a DJ Lounge."""

    model_config = ConfigDict(extra="forbid")

    maxParticipants: int
    bpmTolerancePct: int | float
    soundclashEnabled: bool
    aiVerbosity: str


class LoungeParticipantSchema(BaseModel):
    """Active participant representation within a DJ Lounge."""

    model_config = ConfigDict(extra="forbid")

    userId: str
    displayName: str
    role: str
    isMuted: bool = False
    joinedAt: str


class LoungeNowPlayingSchema(BaseModel):
    """Track currently assigned to the Lounge's Now Playing deck."""

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


class LoungeQueueItemSchema(BaseModel):
    """Queued track entry ordered by sparse integer position."""

    model_config = ConfigDict(extra="forbid")

    queueEntryId: str
    trackId: str
    title: str
    artist: str
    bpm: int | float
    camelotKey: str
    energy: float
    valence: float
    position: int
    voteCount: int
    addedByUserId: str


class LoungeModerationStateSchema(BaseModel):
    """Authoritative moderation state for a DJ Lounge (REQ-025)."""

    model_config = ConfigDict(extra="forbid")

    isLocked: bool
    hostUserId: str
    mutedUserIds: List[str]
    removedUserIds: List[str]
    participantRoles: Dict[str, str]


class LoungeSummaryResponse(BaseModel):
    """Summary representation of a DJ Lounge returned in GET /api/v1/lounges."""

    model_config = ConfigDict(extra="forbid")

    loungeId: str
    inviteCode: str
    status: str
    hostUserId: str
    hostDisplayName: str
    vibeProfile: str
    currentTrackId: str | None
    nowPlayingTitle: str | None = None
    nowPlayingArtist: str | None = None
    maxParticipants: int
    participantCount: int
    peakParticipants: int
    isLocked: bool
    settings: LoungeSettingsSchema
    createdAt: str
    endedAt: str | None = None


class LoungeListResponse(BaseModel):
    """Response envelope for GET /api/v1/lounges."""

    model_config = ConfigDict(extra="forbid")

    lounges: List[LoungeSummaryResponse]
    total: int


class LoungeDetailResponse(BaseModel):
    """Complete DJ Lounge state returned by GET /api/v1/lounges/{id}."""

    model_config = ConfigDict(extra="forbid")

    loungeId: str
    inviteCode: str
    status: str
    hostUserId: str
    hostDisplayName: str
    vibeProfile: str
    currentTrackId: str | None
    nowPlaying: LoungeNowPlayingSchema | None
    nextUp: List[LoungeQueueItemSchema]
    soundclash: Dict[str, Any]
    sentimentScore: float
    participants: List[LoungeParticipantSchema]
    participantCount: int
    peakParticipants: int
    isLocked: bool
    moderationState: LoungeModerationStateSchema
    settings: LoungeSettingsSchema
    createdAt: str
    endedAt: str | None = None


class LoungeSnapshotSchema(BaseModel):
    """Initial room state snapshot returned upon joining via invite code."""

    model_config = ConfigDict(extra="forbid")

    nowPlaying: LoungeNowPlayingSchema | None
    nextUp: List[LoungeQueueItemSchema]
    soundclash: Dict[str, Any]
    sentimentScore: float
    participants: List[LoungeParticipantSchema]
    settings: LoungeSettingsSchema
    moderationState: LoungeModerationStateSchema


class LoungeJoinResponse(BaseModel):
    """Response payload for POST /api/v1/lounges/join/{inviteCode} (REQ-019)."""

    model_config = ConfigDict(extra="forbid")

    loungeId: str
    inviteCode: str
    status: str
    wsUrl: str
    participantCount: int
    snapshot: LoungeSnapshotSchema


class LoungeStartResponse(BaseModel):
    """Response payload for POST /api/v1/lounges/{id}/start."""

    model_config = ConfigDict(extra="forbid")

    loungeId: str
    status: str
    currentTrackId: str | None


class LoungeEndSummarySchema(BaseModel):
    """Session summary persisted when a Host ends a DJ Lounge (REQ-015)."""

    model_config = ConfigDict(extra="forbid")

    durationSeconds: int
    peakConcurrentParticipants: int
    totalQueueEntries: int


class LoungeEndResponse(BaseModel):
    """Response payload for POST /api/v1/lounges/{id}/end (REQ-015)."""

    model_config = ConfigDict(extra="forbid")

    loungeId: str
    status: str
    endedAt: str
    summary: LoungeEndSummarySchema


class LoungeModerationRequest(BaseModel):
    """Request payload for POST /api/v1/lounges/{id}/moderation (REQ-025)."""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(..., description="mute | unmute | remove | lock | unlock | set_role")
    targetUserId: str | None = Field(default=None, max_length=64)
    newRole: str | None = Field(default=None, max_length=20)
    locked: bool | None = Field(default=None)

    @field_validator("action")
    @classmethod
    def validate_action(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("Moderation action must be a string.")
        normalized = value.strip().lower()
        if normalized not in VALID_MODERATION_ACTIONS:
            raise ValueError(
                f"Invalid moderation action '{value}'. Allowed actions: {list(VALID_MODERATION_ACTIONS)}."
            )
        return normalized

    @field_validator("targetUserId")
    @classmethod
    def validate_target_user_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = sanitize_text_input(value)
        if not cleaned or " " in cleaned or "/" in cleaned:
            raise ValueError("targetUserId must be a valid user identifier.")
        return cleaned

    @field_validator("newRole")
    @classmethod
    def validate_new_role(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in VALID_ASSIGNABLE_LOUNGE_ROLES:
            raise ValueError(
                f"Invalid newRole '{value}'. Must be one of {list(VALID_ASSIGNABLE_LOUNGE_ROLES)}."
            )
        return normalized

    @model_validator(mode="after")
    def validate_action_requirements(self) -> "LoungeModerationRequest":
        if self.action in {"mute", "unmute", "remove", "set_role"} and not self.targetUserId:
            raise ValueError(
                f"targetUserId is required for moderation action '{self.action}'."
            )
        if self.action == "set_role" and not self.newRole:
            raise ValueError(
                "newRole is required when moderation action is 'set_role'."
            )
        return self


class LoungeModerationResponse(BaseModel):
    """Response payload for POST /api/v1/lounges/{id}/moderation (REQ-025)."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    loungeId: str
    action: str
    moderationState: LoungeModerationStateSchema
