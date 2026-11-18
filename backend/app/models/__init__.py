"""ORM models registry for AI DJ Studio (PRD Section 7.2.2)."""

from app.models.chat import (
    VALID_MESSAGE_TYPES,
    VALID_SENTIMENT_BUCKETS,
    ChatMessage,
)
from app.models.interaction import Interaction
from app.models.lounge import (
    VALID_AI_VERBOSITIES,
    VALID_LOUNGE_STATUSES,
    DJLounge,
)
from app.models.queue import (
    VALID_QUEUE_EVENT_TYPES,
    QueueEntry,
    QueueEvent,
)
from app.models.soundclash import (
    VALID_BATTLE_STATUSES,
    SoundclashBattle,
    SoundclashCandidate,
)
from app.models.track import (
    VALID_CAMELOT_KEYS,
    VALID_TIMELINE_EVENT_TYPES,
    Track,
    TrackTimelineEvent,
)
from app.models.user import VALID_USER_ROLES, User

__all__ = [
    "ChatMessage",
    "DJLounge",
    "Interaction",
    "QueueEntry",
    "QueueEvent",
    "SoundclashBattle",
    "SoundclashCandidate",
    "Track",
    "TrackTimelineEvent",
    "User",
    "VALID_AI_VERBOSITIES",
    "VALID_BATTLE_STATUSES",
    "VALID_CAMELOT_KEYS",
    "VALID_LOUNGE_STATUSES",
    "VALID_MESSAGE_TYPES",
    "VALID_QUEUE_EVENT_TYPES",
    "VALID_SENTIMENT_BUCKETS",
    "VALID_TIMELINE_EVENT_TYPES",
    "VALID_USER_ROLES",
]
