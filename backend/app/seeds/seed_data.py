"""Deterministic, idempotent seed script for 4 role test accounts, 10 profiled tracks (t_2001..t_2010), timeline events (evt_5541+), and synthesized royalty-free .wav audio files (Module 4 — Remediated M4-01).

Configuration & CLI Usage:
--------------------------
Seeding requires an explicitly configured `SEED_USER_PASSWORD` (minimum 8 characters,
maximum 72 UTF-8 bytes, non-placeholder) in all environments so that seeded development
and test accounts have known, accessible credentials without ever falling back to
hardcoded or silent random passwords.

1. Development / Testing CLI usage:
   PowerShell:
     $env:SEED_USER_PASSWORD="<your-dev-seed-password>"
     python -m app.seeds.seed_data

   Bash:
     SEED_USER_PASSWORD="<your-dev-seed-password>" python -m app.seeds.seed_data

2. Production Protection:
   Database and audio asset seeding is blocked by default when `APP_ENV=production`.
   To deliberately seed a production environment, both `ALLOW_PRODUCTION_SEED=true`
   and an explicit non-placeholder `SEED_USER_PASSWORD` must be set.
"""

import argparse
import asyncio
import math
import os
from pathlib import Path
import struct
from typing import Any, Dict, List
import wave

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import KNOWN_PLACEHOLDER_SECRETS, get_settings
from app.core.logging import get_logger
from app.core.security import hash_password, validate_password_bytes
from app.db.session import get_session_maker, init_db
from app.models.track import Track, TrackTimelineEvent
from app.models.user import User

logger = get_logger("seeds")

BACKEND_DIR = Path(__file__).resolve().parents[2]
AUDIO_ASSETS_DIR = BACKEND_DIR / "assets" / "audio"

SEED_USERS: List[Dict[str, Any]] = [
    {
        "id": "u_501",
        "email": "host@spotify-intern.local",
        "display_name": "DJ Nova",
        "role": "host",
        "preferences": {"genreAffinity": ["High-Energy EDM", "Synthwave"]},
    },
    {
        "id": "u_502",
        "email": "listener@spotify-intern.local",
        "display_name": "Alex Rivera",
        "role": "listener",
        "preferences": {"genreAffinity": ["High-Energy EDM", "Progressive House"]},
    },
    {
        "id": "u_503",
        "email": "codj@spotify-intern.local",
        "display_name": "Co-DJ Pulse",
        "role": "co_dj",
        "preferences": {"genreAffinity": ["Melodic Techno", "Synthwave"]},
    },
    {
        "id": "u_504",
        "email": "admin@spotify-intern.local",
        "display_name": "Studio Admin",
        "role": "admin",
        "preferences": {"genreAffinity": ["High-Energy EDM", "Lo-Fi Chill"]},
    },
]

# 10 Royalty-Free Tracks forming valid Camelot/BPM segue chains (t_2001 matches PRD Section 7.3.2 verbatim)
SEED_TRACKS: List[Dict[str, Any]] = [
    {
        "id": "t_2001",
        "title": "Night Drive",
        "artist": "Nova Circuit",
        "duration_seconds": 215,
        "audio_asset_url": "/assets/audio/t_2001.wav",
        "bpm": 124.0,
        "camelot_key": "8A",
        "energy": 0.78,
        "valence": 0.65,
        "freq_hz": 220.0,
        "timeline": [
            {
                "id": "evt_5541",
                "ts": 12,
                "type": "sample_credit",
                "text": "Vocal chop sampled from a 1981 soul record.",
            }
        ],
    },
    {
        "id": "t_2002",
        "title": "Subzero Drift",
        "artist": "Kinetix",
        "duration_seconds": 192,
        "audio_asset_url": "/assets/audio/t_2002.wav",
        "bpm": 95.0,
        "camelot_key": "3B",
        "energy": 0.45,
        "valence": 0.40,
        "freq_hz": 196.0,
        "timeline": [
            {
                "id": "evt_5542",
                "ts": 18,
                "type": "trivia",
                "text": "Recorded on a vintage analog tape machine in Stockholm.",
            }
        ],
    },
    {
        "id": "t_2003",
        "title": "Solar Flare",
        "artist": "Nova Circuit",
        "duration_seconds": 204,
        "audio_asset_url": "/assets/audio/t_2003.wav",
        "bpm": 128.0,
        "camelot_key": "9A",
        "energy": 0.82,
        "valence": 0.70,
        "freq_hz": 246.94,
        "timeline": [
            {
                "id": "evt_5543",
                "ts": 15,
                "type": "trivia",
                "text": "Produced during an all-night session in Berlin.",
            },
            {
                "id": "evt_5544",
                "ts": 75,
                "type": "glossary",
                "text": "Uses sidechain compression triggered by a 909 kick drum.",
            },
        ],
    },
    {
        "id": "t_2004",
        "title": "Velvet Pulse",
        "artist": "Aura Syndicate",
        "duration_seconds": 210,
        "audio_asset_url": "/assets/audio/t_2004.wav",
        "bpm": 122.0,
        "camelot_key": "8B",
        "energy": 0.74,
        "valence": 0.68,
        "freq_hz": 261.63,
        "timeline": [
            {
                "id": "evt_5545",
                "ts": 24,
                "type": "sample_credit",
                "text": "Bassline interpolated from a 1979 Chicago funk session.",
            }
        ],
    },
    {
        "id": "t_2005",
        "title": "Midnight Metro",
        "artist": "Chrono System",
        "duration_seconds": 198,
        "audio_asset_url": "/assets/audio/t_2005.wav",
        "bpm": 126.0,
        "camelot_key": "8A",
        "energy": 0.85,
        "valence": 0.72,
        "freq_hz": 220.0,
        "timeline": [
            {
                "id": "evt_5546",
                "ts": 10,
                "type": "trivia",
                "text": "Features field recordings from the Tokyo subway network.",
            }
        ],
    },
    {
        "id": "t_2006",
        "title": "Cobalt Horizon",
        "artist": "Lyra V",
        "duration_seconds": 220,
        "audio_asset_url": "/assets/audio/t_2006.wav",
        "bpm": 125.0,
        "camelot_key": "7A",
        "energy": 0.80,
        "valence": 0.62,
        "freq_hz": 293.66,
        "timeline": [
            {
                "id": "evt_5547",
                "ts": 30,
                "type": "glossary",
                "text": "Built around a Dorian mode arpeggio with analog chorus.",
            }
        ],
    },
    {
        "id": "t_2007",
        "title": "Apex Protocol",
        "artist": "Vector Nine",
        "duration_seconds": 185,
        "audio_asset_url": "/assets/audio/t_2007.wav",
        "bpm": 130.0,
        "camelot_key": "10A",
        "energy": 0.91,
        "valence": 0.76,
        "freq_hz": 329.63,
        "timeline": [
            {
                "id": "evt_5548",
                "ts": 20,
                "type": "sample_credit",
                "text": "Breakbeat layer sliced from a 1993 UK jungle pressing.",
            }
        ],
    },
    {
        "id": "t_2008",
        "title": "Halftime Mirage",
        "artist": "Echo Loom",
        "duration_seconds": 205,
        "audio_asset_url": "/assets/audio/t_2008.wav",
        "bpm": 64.0,
        "camelot_key": "9A",
        "energy": 0.58,
        "valence": 0.55,
        "freq_hz": 164.81,
        "timeline": [
            {
                "id": "evt_5549",
                "ts": 14,
                "type": "glossary",
                "text": "Half-time groove locks at 64 BPM to transition seamlessly from 128 BPM sets.",
            }
        ],
    },
    {
        "id": "t_2009",
        "title": "Lo-Fi Rain",
        "artist": "Sunken Garden",
        "duration_seconds": 176,
        "audio_asset_url": "/assets/audio/t_2009.wav",
        "bpm": 96.0,
        "camelot_key": "3A",
        "energy": 0.35,
        "valence": 0.48,
        "freq_hz": 174.61,
        "timeline": [
            {
                "id": "evt_5550",
                "ts": 8,
                "type": "trivia",
                "text": "Vinyl crackle captured directly from a 1968 turntable preamp.",
            }
        ],
    },
    {
        "id": "t_2010",
        "title": "Aurora Loop",
        "artist": "Solaris 12",
        "duration_seconds": 195,
        "audio_asset_url": "/assets/audio/t_2010.wav",
        "bpm": 128.0,
        "camelot_key": "12A",
        "energy": 0.88,
        "valence": 0.79,
        "freq_hz": 277.18,
        "timeline": [
            {
                "id": "evt_5551",
                "ts": 22,
                "type": "sample_credit",
                "text": "Lead synth patch recreated from an early 1984 polyphonic synthesizer.",
            }
        ],
    },
]

_CACHED_SEED_HASHES: Dict[str, str] = {}


def _check_production_seed_guard(
    app_env: str | None = None,
    allow_production: bool = False,
) -> bool:
    """Enforce production seeding protection before any database or filesystem operations."""
    resolved_env = (
        app_env
        if app_env is not None
        else (os.environ.get("APP_ENV") or get_settings().APP_ENV)
    )
    is_production = resolved_env.strip().lower() == "production"

    env_override = (
        os.environ.get("ALLOW_PRODUCTION_SEED", "").strip().lower() == "true"
    )
    effective_allow_prod = bool(allow_production or env_override)

    if is_production and not effective_allow_prod:
        raise RuntimeError(
            "Database and audio seeding is blocked in production by default. "
            "Pass allow_production=True or set ALLOW_PRODUCTION_SEED=true to override."
        )
    return is_production


def _resolve_seed_password(
    seed_password: str | None = None,
    app_env: str | None = None,
    allow_production: bool = False,
) -> str:
    """M4-01: Block production seeding by default and require an explicit, validated SEED_USER_PASSWORD in all environments."""
    _check_production_seed_guard(
        app_env=app_env, allow_production=allow_production
    )

    explicit_pw = (
        seed_password
        if seed_password is not None
        else os.environ.get("SEED_USER_PASSWORD")
    )

    if not explicit_pw or not explicit_pw.strip():
        raise RuntimeError(
            "SEED_USER_PASSWORD is not configured. To create accessible seed accounts, "
            "set SEED_USER_PASSWORD in your environment or .env file "
            "(minimum 8 characters, maximum 72 UTF-8 bytes) or pass seed_password explicitly "
            "before running the seed script."
        )

    cleaned_pw = explicit_pw.strip()
    if (
        cleaned_pw.lower() in KNOWN_PLACEHOLDER_SECRETS
        or cleaned_pw.lower().startswith("replace_with_")
        or cleaned_pw.lower().startswith("example_")
    ):
        raise RuntimeError(
            "SEED_USER_PASSWORD cannot be a placeholder or default password. "
            "Configure a strong secret in SEED_USER_PASSWORD before seeding."
        )

    if len(cleaned_pw) < 8:
        raise RuntimeError(
            "SEED_USER_PASSWORD must be at least 8 characters long."
        )

    try:
        validate_password_bytes(cleaned_pw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid SEED_USER_PASSWORD: {exc}") from exc

    return cleaned_pw


def _get_seed_password_hash(password: str) -> str:
    """Hash seed password via bcrypt with per-process caching to avoid redundant hashing across idempotent runs."""
    cached = _CACHED_SEED_HASHES.get(password)
    if cached is None:
        cached = hash_password(password)
        _CACHED_SEED_HASHES[password] = cached
    return cached


def _synthesize_wav_file(
    file_path: Path,
    freq_hz: float,
    bpm: float,
    duration_sec: float = 1.5,
    sample_rate: int = 22050,
) -> None:
    """Synthesize a royalty-free 16-bit PCM mono WAV file with tempo-synced harmonic pulses (REQ-004, REQ-012)."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    num_samples = int(sample_rate * duration_sec)
    beat_period_sec = 60.0 / max(bpm, 1.0)
    frames = bytearray()

    for i in range(num_samples):
        t = i / sample_rate
        beat_phase = (t % beat_period_sec) / beat_period_sec
        envelope = math.exp(-3.5 * beat_phase)
        fundamental = math.sin(2.0 * math.pi * freq_hz * t)
        fifth = 0.45 * math.sin(2.0 * math.pi * (freq_hz * 1.5) * t)
        sub_bass = 0.35 * math.sin(2.0 * math.pi * (freq_hz * 0.5) * t)
        sample_val = int(12000 * envelope * (fundamental + fifth + sub_bass) / 1.8)
        sample_val = max(-32767, min(32767, sample_val))
        frames.extend(struct.pack("<h", sample_val))

    with wave.open(str(file_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(bytes(frames))


def ensure_audio_assets(
    target_dir: Path = AUDIO_ASSETS_DIR,
    overwrite: bool = False,
    app_env: str | None = None,
    allow_production: bool = False,
) -> List[Path]:
    """Explicit seed/setup operation to ensure all 10 synthesized royalty-free .wav files exist on disk (idempotent)."""
    _check_production_seed_guard(
        app_env=app_env, allow_production=allow_production
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    generated_paths: List[Path] = []

    for track_meta in SEED_TRACKS:
        wav_path = target_dir / f"{track_meta['id']}.wav"
        if overwrite or not wav_path.exists() or wav_path.stat().st_size < 1024:
            _synthesize_wav_file(
                file_path=wav_path,
                freq_hz=float(track_meta["freq_hz"]),
                bpm=float(track_meta["bpm"]),
            )
        generated_paths.append(wav_path)

    return generated_paths


async def seed_database(
    session: AsyncSession,
    seed_password: str | None = None,
    app_env: str | None = None,
    allow_production: bool = False,
) -> Dict[str, int]:
    """Idempotently seed the 4 role test accounts, 10 royalty-free tracks, and RAG metadata timelines (M4-01)."""
    resolved_password = _resolve_seed_password(
        seed_password=seed_password,
        app_env=app_env,
        allow_production=allow_production,
    )
    ensure_audio_assets(
        app_env=app_env,
        allow_production=allow_production,
    )
    pw_hash = _get_seed_password_hash(resolved_password)

    users_created = 0
    tracks_created = 0
    events_created = 0

    # 1. Upsert/verify 4 test accounts (u_501..u_504)
    for u_meta in SEED_USERS:
        existing_user = (
            await session.execute(
                select(User).where(
                    (User.id == u_meta["id"]) | (User.email == u_meta["email"])
                )
            )
        ).scalar_one_or_none()

        if existing_user is None:
            session.add(
                User(
                    id=u_meta["id"],
                    email=u_meta["email"],
                    password_hash=pw_hash,
                    display_name=u_meta["display_name"],
                    role=u_meta["role"],
                    preferences=u_meta["preferences"],
                )
            )
            users_created += 1

    await session.flush()

    # 2. Upsert/verify 10 tracks (t_2001..t_2010) and their timeline events (evt_5541+)
    for t_meta in SEED_TRACKS:
        existing_track = (
            await session.execute(select(Track).where(Track.id == t_meta["id"]))
        ).scalar_one_or_none()

        if existing_track is None:
            session.add(
                Track(
                    id=t_meta["id"],
                    title=t_meta["title"],
                    artist=t_meta["artist"],
                    duration_seconds=int(t_meta["duration_seconds"]),
                    audio_asset_url=t_meta["audio_asset_url"],
                    bpm=float(t_meta["bpm"]),
                    camelot_key=t_meta["camelot_key"],
                    energy=float(t_meta["energy"]),
                    valence=float(t_meta["valence"]),
                )
            )
            tracks_created += 1
            await session.flush()

        for evt_meta in t_meta["timeline"]:
            existing_evt = (
                await session.execute(
                    select(TrackTimelineEvent).where(
                        TrackTimelineEvent.id == evt_meta["id"]
                    )
                )
            ).scalar_one_or_none()

            if existing_evt is None:
                session.add(
                    TrackTimelineEvent(
                        id=evt_meta["id"],
                        track_id=t_meta["id"],
                        ts_seconds=int(evt_meta["ts"]),
                        event_type=evt_meta["type"],
                        text_body=evt_meta["text"],
                        payload={"text": evt_meta["text"]},
                    )
                )
                events_created += 1

    await session.commit()

    summary = {
        "usersCreated": users_created,
        "tracksCreated": tracks_created,
        "timelineEventsCreated": events_created,
    }
    logger.info(
        "Database seed check completed",
        extra={"event": "db.seed.completed", "payload": summary},
    )
    return summary


async def run_cli_seed(
    seed_password: str | None = None,
    app_env: str | None = None,
    allow_production: bool = False,
) -> Dict[str, int]:
    """Initialize schema and run idempotent database + audio asset seeding from CLI (M4-01)."""
    _resolve_seed_password(
        seed_password=seed_password,
        app_env=app_env,
        allow_production=allow_production,
    )
    await init_db()
    session_maker = get_session_maker()
    async with session_maker() as session:
        return await seed_database(
            session,
            seed_password=seed_password,
            app_env=app_env,
            allow_production=allow_production,
        )


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.seeds.seed_data",
        description=(
            "Idempotently seed AI DJ Studio development accounts (u_501..u_504), "
            "10 royalty-free tracks (t_2001..t_2010), RAG timelines, and .wav audio files. "
            "Requires SEED_USER_PASSWORD to be set in the environment or .env file."
        ),
    )
    parser.add_argument(
        "--allow-production",
        action="store_true",
        default=False,
        help="Explicitly override the production seeding block (still requires SEED_USER_PASSWORD).",
    )
    return parser


if __name__ == "__main__":
    cli_args = _build_cli_parser().parse_args()
    asyncio.run(run_cli_seed(allow_production=cli_args.allow_production))
