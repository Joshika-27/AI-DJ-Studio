"""Pydantic schemas for Track catalog, Acoustic Profile (PRD Section 7.3.2), and Admin upload validation (Module 4 — Remediated M4-02, M4-03)."""

from datetime import datetime
import math
import re
from typing import Any, Dict, List
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.security import sanitize_text_input
from app.models.track import (
    Track,
    TrackTimelineEvent,
    VALID_CAMELOT_KEYS,
    VALID_TIMELINE_EVENT_TYPES,
)

ALLOWED_AUDIO_EXTENSIONS = (".wav", ".mp3", ".ogg")
_LOCAL_AUDIO_FILENAME_REGEX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_WINDOWS_DRIVE_REGEX = re.compile(r"(?:^|[\\/])[A-Za-z]:")


def _format_bpm_value(bpm: float) -> int | float:
    """Format whole-number BPMs as int (e.g. 124.0 -> 124) to match PRD Section 7.3.2 while preserving decimals."""
    rounded = round(float(bpm), 2)
    return int(rounded) if rounded.is_integer() else rounded


class AcousticProfileSchema(BaseModel):
    """Acoustic feature vector schema enforcing BPM > 0, 24 Camelot keys, and energy/valence in [0.0, 1.0] (REQ-030, REQ-049)."""

    model_config = ConfigDict(extra="forbid")

    bpm: int | float = Field(..., description="Beats per minute (> 0)")
    camelotKey: str = Field(..., description="24-key Camelot wheel code (1A-12A, 1B-12B)")
    energy: float = Field(..., description="Acoustic energy score (0.0 to 1.0)")
    valence: float = Field(..., description="Musical positivity/valence score (0.0 to 1.0)")

    @field_validator("bpm")
    @classmethod
    def validate_bpm(cls, value: int | float) -> int | float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("BPM must be a positive number.")
        if math.isnan(value) or math.isinf(value) or float(value) <= 0.0:
            raise ValueError("BPM must be greater than zero.")
        if float(value) > 400.0:
            raise ValueError("BPM cannot exceed 400.")
        return _format_bpm_value(float(value))

    @field_validator("camelotKey")
    @classmethod
    def validate_camelot_key(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("camelotKey must be a string.")
        normalized = value.strip().upper()
        if normalized not in VALID_CAMELOT_KEYS:
            raise ValueError(
                f"Invalid camelotKey '{value}'. Must match one of the 24 valid Camelot wheel codes: {list(VALID_CAMELOT_KEYS)}."
            )
        return normalized

    @field_validator("energy")
    @classmethod
    def validate_energy(cls, value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("energy must be a number between 0.0 and 1.0.")
        fval = float(value)
        if math.isnan(fval) or math.isinf(fval) or fval < 0.0 or fval > 1.0:
            raise ValueError("energy must be between 0.0 and 1.0.")
        return round(fval, 4)

    @field_validator("valence")
    @classmethod
    def validate_valence(cls, value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("valence must be a number between 0.0 and 1.0.")
        fval = float(value)
        if math.isnan(fval) or math.isinf(fval) or fval < 0.0 or fval > 1.0:
            raise ValueError("valence must be between 0.0 and 1.0.")
        return round(fval, 4)


class TimelineEventResponseSchema(BaseModel):
    """Exact timeline entry contract specified in PRD Section 7.3.2 (ts, type, text)."""

    model_config = ConfigDict(extra="forbid")

    ts: int = Field(..., ge=0, description="Timestamp offset in seconds")
    type: str = Field(..., description="Metadata event type: sample_credit | trivia | glossary")
    text: str = Field(..., min_length=1, description="Grounded metadata fact text")

    @classmethod
    def from_orm_event(cls, event: TrackTimelineEvent) -> "TimelineEventResponseSchema":
        text_val = event.text_body
        if not text_val and isinstance(event.payload, dict):
            text_val = str(event.payload.get("text", ""))
        return cls(
            ts=event.ts_seconds,
            type=event.event_type,
            text=text_val,
        )


class TimelineEventUploadSchema(BaseModel):
    """Validated input schema for uploading a track timeline metadata entry (REQ-034, REQ-046, REQ-072)."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, max_length=64)
    ts: int = Field(..., ge=0, le=7200)
    type: str = Field(...)
    text: str = Field(..., min_length=1, max_length=1000)

    @field_validator("id")
    @classmethod
    def validate_event_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = sanitize_text_input(value)
        if not cleaned or " " in cleaned or "/" in cleaned:
            raise ValueError(
                "Timeline event id must be a non-empty identifier without spaces or slashes."
            )
        return cleaned

    @field_validator("type")
    @classmethod
    def validate_event_type(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("Timeline event type must be a string.")
        normalized = value.strip().lower()
        if normalized not in VALID_TIMELINE_EVENT_TYPES:
            raise ValueError(
                f"Invalid timeline event type '{value}'. Allowed types: {list(VALID_TIMELINE_EVENT_TYPES)}."
            )
        return normalized

    @field_validator("text")
    @classmethod
    def validate_and_sanitize_text(cls, value: str) -> str:
        sanitized = sanitize_text_input(value)
        if len(sanitized) < 2:
            raise ValueError(
                "Timeline event text must contain at least 2 valid characters after sanitization."
            )
        return sanitized


class TrackAcousticProfileResponse(BaseModel):
    """Exact response contract for GET /api/v1/tracks/{trackId}/acoustic-profile (PRD Section 7.3.2, REQ-058)."""

    model_config = ConfigDict(extra="forbid")

    trackId: str
    title: str
    artist: str
    acoustic: AcousticProfileSchema
    timeline: List[TimelineEventResponseSchema]

    @classmethod
    def from_orm_track(cls, track: Track) -> "TrackAcousticProfileResponse":
        ordered_events = sorted(
            track.timeline_events or [],
            key=lambda e: (e.ts_seconds, e.id),
        )
        return cls(
            trackId=track.id,
            title=track.title,
            artist=track.artist,
            acoustic=AcousticProfileSchema(
                bpm=_format_bpm_value(track.bpm),
                camelotKey=track.camelot_key,
                energy=track.energy,
                valence=track.valence,
            ),
            timeline=[
                TimelineEventResponseSchema.from_orm_event(evt)
                for evt in ordered_events
            ],
        )


class TrackCatalogItemResponse(BaseModel):
    """Catalog track representation returned by GET /api/v1/tracks and POST /api/v1/admin/tracks."""

    model_config = ConfigDict(extra="forbid")

    trackId: str
    title: str
    artist: str
    durationSeconds: int
    audioAssetUrl: str
    acoustic: AcousticProfileSchema
    timeline: List[TimelineEventResponseSchema]
    createdAt: datetime

    @classmethod
    def from_orm_track(cls, track: Track) -> "TrackCatalogItemResponse":
        ordered_events = sorted(
            track.timeline_events or [],
            key=lambda e: (e.ts_seconds, e.id),
        )
        return cls(
            trackId=track.id,
            title=track.title,
            artist=track.artist,
            durationSeconds=track.duration_seconds,
            audioAssetUrl=track.audio_asset_url,
            acoustic=AcousticProfileSchema(
                bpm=_format_bpm_value(track.bpm),
                camelotKey=track.camelot_key,
                energy=track.energy,
                valence=track.valence,
            ),
            timeline=[
                TimelineEventResponseSchema.from_orm_event(evt)
                for evt in ordered_events
            ],
            createdAt=track.created_at,
        )


class TrackCatalogListResponse(BaseModel):
    """Response envelope for GET /api/v1/tracks."""

    model_config = ConfigDict(extra="forbid")

    tracks: List[TrackCatalogItemResponse]
    total: int


class AdminTrackUploadRequest(BaseModel):
    """Payload for POST /api/v1/admin/tracks (REQ-046, REQ-049, REQ-072, Remediated M4-02, M4-03)."""

    model_config = ConfigDict(extra="forbid")

    trackId: str | None = Field(default=None, max_length=64)
    title: str = Field(..., min_length=1, max_length=200)
    artist: str = Field(..., min_length=1, max_length=200)
    durationSeconds: int = Field(default=180, gt=0, le=7200)
    audioAssetUrl: str = Field(
        default="/assets/audio/t_2001.wav", min_length=1, max_length=500
    )
    acoustic: AcousticProfileSchema
    timeline: List[TimelineEventUploadSchema] = Field(default_factory=list, max_length=50)

    @model_validator(mode="before")
    @classmethod
    def support_flat_or_nested_acoustic_fields(cls, data: Any) -> Any:
        """Allow acoustic fields to be passed either nested under 'acoustic' or flat at top level while forbidding unknown fields."""
        if not isinstance(data, dict):
            return data
        flat_keys = {"bpm", "camelotKey", "energy", "valence"}
        if "acoustic" not in data and flat_keys.issubset(set(data.keys())):
            copied: Dict[str, Any] = dict(data)
            copied["acoustic"] = {k: copied.pop(k) for k in flat_keys}
            return copied
        return data

    @field_validator("trackId")
    @classmethod
    def validate_track_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = sanitize_text_input(value)
        if not cleaned or " " in cleaned or "/" in cleaned:
            raise ValueError(
                "trackId must be a valid non-empty identifier without spaces or slashes."
            )
        return cleaned

    @field_validator("title", "artist")
    @classmethod
    def validate_and_sanitize_text_fields(cls, value: str) -> str:
        sanitized = sanitize_text_input(value)
        if not sanitized:
            raise ValueError("Field must contain valid characters after sanitization.")
        return sanitized

    @field_validator("audioAssetUrl")
    @classmethod
    def validate_audio_asset_url(cls, value: str) -> str:
        """M4-03: Reject literal, single-encoded, or double-encoded path traversal, UNC/Windows drive paths, and unsupported extensions."""
        if not isinstance(value, str):
            raise ValueError("audioAssetUrl must be a string.")
        raw = value.strip()
        if not raw or raw.startswith("//") or raw.startswith("\\\\"):
            raise ValueError(
                "audioAssetUrl cannot be empty or a UNC network path."
            )

        decoded_once = unquote(raw)
        decoded_twice = unquote(decoded_once)

        for candidate in (raw, decoded_once, decoded_twice):
            if (
                "\x00" in candidate
                or "\\" in candidate
                or "<" in candidate
                or ">" in candidate
                or " " in candidate
                or ".." in candidate
                or _WINDOWS_DRIVE_REGEX.search(candidate)
            ):
                raise ValueError(
                    "audioAssetUrl contains invalid characters, encoded traversal, or drive/UNC path segments."
                )

        if raw.startswith("/assets/audio/"):
            if "%" in raw:
                raise ValueError(
                    "Local audioAssetUrl cannot contain percent-encoded characters."
                )
            filename = raw[len("/assets/audio/") :]
            if (
                not filename
                or "/" in filename
                or "?" in filename
                or "#" in filename
                or ":" in filename
                or not _LOCAL_AUDIO_FILENAME_REGEX.match(filename)
            ):
                raise ValueError(
                    "Local audioAssetUrl must reference a single valid filename directly under '/assets/audio/'."
                )
            if not filename.lower().endswith(ALLOWED_AUDIO_EXTENSIONS):
                raise ValueError(
                    f"Audio asset must use a supported extension: {list(ALLOWED_AUDIO_EXTENSIONS)}."
                )
            return f"/assets/audio/{filename}"

        if raw.startswith("http://") or raw.startswith("https://"):
            parsed = urlparse(raw)
            decoded_path = unquote(unquote(parsed.path or ""))
            if (
                not parsed.netloc
                or not parsed.path
                or ".." in parsed.path
                or ".." in decoded_path
                or "\\" in decoded_path
                or "\x00" in decoded_path
            ):
                raise ValueError("External audioAssetUrl must be a valid HTTP(S) URL.")
            if not parsed.path.lower().endswith(ALLOWED_AUDIO_EXTENSIONS):
                raise ValueError(
                    f"External audio asset URL must end with a supported extension: {list(ALLOWED_AUDIO_EXTENSIONS)}."
                )
            return raw

        raise ValueError(
            "audioAssetUrl must be a valid '/assets/audio/<file>' path or HTTP(S) audio URL."
        )

    @model_validator(mode="after")
    def validate_timeline_consistency(self) -> "AdminTrackUploadRequest":
        """M4-02: Enforce unique timeline event IDs within the request and ensure ts <= durationSeconds."""
        seen_ids: set[str] = set()
        for evt in self.timeline:
            if evt.ts > self.durationSeconds:
                raise ValueError(
                    f"Timeline event timestamp ({evt.ts}s) cannot exceed track durationSeconds ({self.durationSeconds}s)."
                )
            if evt.id is not None:
                if evt.id in seen_ids:
                    raise ValueError(
                        f"Duplicate timeline event id '{evt.id}' within the same upload request."
                    )
                seen_ids.add(evt.id)
        return self
