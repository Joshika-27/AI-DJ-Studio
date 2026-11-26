"""Asynchronous Background Batch Persistence Worker for high-frequency events (REQ-056, PRD Sec 9.1-9.4).

Batches high-frequency persistence writes (chat_messages and queue_events) on a short interval
(default: every 1.5 seconds or 50 events, whichever comes first) using connection pooling.
Guarantees FIFO ordering, safe flushes on lounge cleanup/shutdown, in-flight pending item lookups,
and resilient failure handling with item-level retry and dead-letter isolation without silent data loss.
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
import threading
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.session import make_transient

from app.core.logging import get_logger
from app.db.base import utc_now
from app.db.session import get_session_maker
from app.models.chat import ChatMessage
from app.models.queue import QueueEvent

logger = get_logger("websocket.batch_persistence")

DEFAULT_FLUSH_INTERVAL_SECONDS = 1.5
DEFAULT_MAX_BATCH_SIZE = 50
DEFAULT_MAX_RETRIES = 3


class QueuedBatchItem:
    """Internal wrapper tracking a queued record, its type, and retry state."""

    __slots__ = (
        "item",
        "item_type",
        "lounge_id",
        "item_id",
        "enqueued_at",
        "retries",
    )

    def __init__(
        self,
        item: Union[ChatMessage, QueueEvent],
        item_type: str,
        lounge_id: str,
        item_id: str,
        enqueued_at: datetime,
        retries: int = 0,
    ) -> None:
        self.item = item
        self.item_type = item_type
        self.lounge_id = lounge_id
        self.item_id = item_id
        self.enqueued_at = enqueued_at
        self.retries = retries


class BatchPersistenceWorker:
    """Async background worker batching high-frequency ChatMessage and QueueEvent persistence writes."""

    def __init__(
        self,
        flush_interval_seconds: float = DEFAULT_FLUSH_INTERVAL_SECONDS,
        max_batch_size: int = DEFAULT_MAX_BATCH_SIZE,
        max_retries: int = DEFAULT_MAX_RETRIES,
        session_maker_factory: Optional[Callable[[], async_sessionmaker[AsyncSession]]] = None,
    ) -> None:
        self.flush_interval_seconds = flush_interval_seconds
        self.max_batch_size = max_batch_size
        self.max_retries = max_retries
        self._session_maker_factory = session_maker_factory

        # Thread/coroutine synchronization
        self._lock_obj: Optional[asyncio.Lock] = None
        self._flush_event_obj: Optional[asyncio.Event] = None
        self._sync_lock = threading.Lock()

        # In-memory queue preserving strict global FIFO arrival ordering
        self._queue: deque[QueuedBatchItem] = deque()

        # Fast lookup indexes for un-flushed items
        self._pending_chats: Dict[str, ChatMessage] = {}
        self._pending_events: Dict[str, QueueEvent] = {}

        # In-flight lookup indexes during active commit I/O (guarantees zero lookup gaps)
        self._in_flight_chats: Dict[str, ChatMessage] = {}
        self._in_flight_events: Dict[str, QueueEvent] = {}

        # Dead-letter queue for persistent write failures (guaranteeing zero silent data loss)
        self._dead_letter: deque[Dict[str, Any]] = deque(maxlen=1000)

        # Worker loop lifecycle state
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None

        # Telemetry / metrics counters
        self._flushed_total = 0
        self._flush_cycles = 0
        self._failed_batches = 0
        self._dead_letter_total = 0

    @property
    def _lock(self) -> asyncio.Lock:
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._lock_obj is None or getattr(self._lock_obj, "_loop", None) not in (current_loop, None):
            self._lock_obj = asyncio.Lock()
        return self._lock_obj

    @property
    def _flush_event(self) -> asyncio.Event:
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._flush_event_obj is None or getattr(self._flush_event_obj, "_loop", None) not in (current_loop, None):
            self._flush_event_obj = asyncio.Event()
        return self._flush_event_obj

    def _get_session_maker(self) -> async_sessionmaker[AsyncSession]:
        if self._session_maker_factory is not None:
            return self._session_maker_factory()
        return get_session_maker()

    # --------------------------------------------------------------------------
    # Enqueue Operations
    # --------------------------------------------------------------------------

    def enqueue_chat_message(
        self,
        chat_msg: Union[ChatMessage, Dict[str, Any]],
    ) -> ChatMessage:
        """Enqueue a ChatMessage for batched database persistence (REQ-023, REQ-056).

        Returns the model instance. Immediate broadcast is NOT delayed by this batching.
        """
        if isinstance(chat_msg, dict):
            now_dt = utc_now()
            model_inst = ChatMessage(
                id=chat_msg.get("id") or f"msg_{uuid.uuid4().hex[:10]}",
                lounge_id=chat_msg["lounge_id"],
                user_id=chat_msg.get("user_id"),
                msg_type=chat_msg.get("msg_type", "chat"),
                body=chat_msg["body"],
                sentiment_score=chat_msg.get("sentiment_score"),
                sentiment_bucket=chat_msg.get("sentiment_bucket"),
                source_events=chat_msg.get("source_events", []),
                ts_seconds=chat_msg.get("ts_seconds", 0),
                created_at=chat_msg.get("created_at") or now_dt,
            )
        elif isinstance(chat_msg, ChatMessage):
            model_inst = chat_msg
            if not model_inst.id:
                model_inst.id = f"msg_{uuid.uuid4().hex[:10]}"
            if not model_inst.created_at:
                model_inst.created_at = utc_now()
        else:
            raise TypeError(f"Expected ChatMessage or dict, got {type(chat_msg).__name__}")

        wrapper = QueuedBatchItem(
            item=model_inst,
            item_type="chat",
            lounge_id=model_inst.lounge_id,
            item_id=model_inst.id,
            enqueued_at=utc_now(),
        )

        with self._sync_lock:
            self._queue.append(wrapper)
            self._pending_chats[model_inst.id] = model_inst
            should_flush = len(self._queue) >= self.max_batch_size

        # Trigger early flush if queue capacity threshold reached
        if should_flush:
            self._flush_event.set()

        return model_inst

    def enqueue_queue_event(
        self,
        queue_event: Union[QueueEvent, Dict[str, Any]],
    ) -> QueueEvent:
        """Enqueue a QueueEvent for batched database persistence (REQ-056).

        Returns the model instance.
        """
        if isinstance(queue_event, dict):
            now_dt = utc_now()
            model_inst = QueueEvent(
                id=queue_event.get("id") or f"qev_{uuid.uuid4().hex[:10]}",
                lounge_id=queue_event["lounge_id"],
                user_id=queue_event.get("user_id"),
                event_type=queue_event["event_type"],
                payload=queue_event.get("payload", {}),
                ts=queue_event.get("ts") or int(now_dt.timestamp() * 1000),
                created_at=queue_event.get("created_at") or now_dt,
            )
        elif isinstance(queue_event, QueueEvent):
            model_inst = queue_event
            if not model_inst.id:
                model_inst.id = f"qev_{uuid.uuid4().hex[:10]}"
            if not model_inst.created_at:
                model_inst.created_at = utc_now()
        else:
            raise TypeError(f"Expected QueueEvent or dict, got {type(queue_event).__name__}")

        wrapper = QueuedBatchItem(
            item=model_inst,
            item_type="queue_event",
            lounge_id=model_inst.lounge_id,
            item_id=model_inst.id,
            enqueued_at=utc_now(),
        )

        with self._sync_lock:
            self._queue.append(wrapper)
            self._pending_events[model_inst.id] = model_inst
            should_flush = len(self._queue) >= self.max_batch_size

        # Trigger early flush if queue capacity threshold reached
        if should_flush:
            self._flush_event.set()

        return model_inst

    # --------------------------------------------------------------------------
    # Pending In-Memory Lookups (Ensures Zero Cache Drift & Reaction Safety)
    # --------------------------------------------------------------------------

    def get_pending_chat_message(
        self,
        lounge_id: str,
        message_id: str,
    ) -> Optional[ChatMessage]:
        """Retrieve an un-flushed or in-flight ChatMessage by ID within a lounge.

        Allows immediate reaction validation without failing due to the batch
        persistence window or during active database commit I/O.
        """
        with self._sync_lock:
            msg = self._pending_chats.get(message_id) or self._in_flight_chats.get(message_id)
            if msg is not None and msg.lounge_id == lounge_id:
                return msg
            return None

    def get_pending_chat_messages(self, lounge_id: str) -> List[ChatMessage]:
        """Return all pending and in-flight ChatMessages for the specified lounge in FIFO order."""
        with self._sync_lock:
            in_flight = [
                m for m in self._in_flight_chats.values()
                if m.lounge_id == lounge_id
            ]
            pending = [
                q.item
                for q in self._queue
                if q.item_type == "chat" and q.lounge_id == lounge_id
            ]
            return in_flight + pending  # type: ignore[return-value]

    def get_pending_queue_events(self, lounge_id: str) -> List[QueueEvent]:
        """Return all pending and in-flight QueueEvents for the specified lounge in FIFO order."""
        with self._sync_lock:
            in_flight = [
                e for e in self._in_flight_events.values()
                if e.lounge_id == lounge_id
            ]
            pending = [
                q.item
                for q in self._queue
                if q.item_type == "queue_event" and q.lounge_id == lounge_id
            ]
            return in_flight + pending  # type: ignore[return-value]

    # --------------------------------------------------------------------------
    # Flush Operations
    # --------------------------------------------------------------------------

    async def flush(self, lounge_id: Optional[str] = None) -> int:
        """Drains pending items and persists them in bulk via a single pooled DB transaction.

        If lounge_id is specified, drains only items belonging to that lounge.
        If a bulk commit fails, falls back to item-by-item recovery to isolate bad records
        and re-queues transient failures without silent data loss.
        """
        async with self._lock:
            with self._sync_lock:
                if not self._queue:
                    return 0

                # 1. Drain items from the queue and move to in-flight tracking
                if lounge_id is None:
                    items_to_persist = list(self._queue)
                    self._queue.clear()
                    for q in items_to_persist:
                        if q.item_type == "chat":
                            self._pending_chats.pop(q.item_id, None)
                            self._in_flight_chats[q.item_id] = q.item  # type: ignore[assignment]
                        else:
                            self._pending_events.pop(q.item_id, None)
                            self._in_flight_events[q.item_id] = q.item  # type: ignore[assignment]
                else:
                    items_to_persist = [q for q in self._queue if q.lounge_id == lounge_id]
                    if not items_to_persist:
                        return 0
                    # Retain items belonging to other lounges
                    self._queue = deque([q for q in self._queue if q.lounge_id != lounge_id])
                    for q in items_to_persist:
                        if q.item_type == "chat":
                            self._pending_chats.pop(q.item_id, None)
                            self._in_flight_chats[q.item_id] = q.item  # type: ignore[assignment]
                        else:
                            self._pending_events.pop(q.item_id, None)
                            self._in_flight_events[q.item_id] = q.item  # type: ignore[assignment]

            if not items_to_persist:
                return 0

            # 2. Attempt bulk persistence in a single transaction
            persisted_count = 0
            session_maker = self._get_session_maker()

            chat_models = [q.item for q in items_to_persist if q.item_type == "chat"]
            event_models = [q.item for q in items_to_persist if q.item_type == "queue_event"]

            try:
                async with session_maker() as session:
                    if chat_models:
                        session.add_all(chat_models)
                    if event_models:
                        session.add_all(event_models)
                    await session.commit()
                    persisted_count = len(items_to_persist)
                    with self._sync_lock:
                        for q in items_to_persist:
                            if q.item_type == "chat":
                                self._in_flight_chats.pop(q.item_id, None)
                            else:
                                self._in_flight_events.pop(q.item_id, None)
                    self._flushed_total += persisted_count
                    self._flush_cycles += 1
                    logger.debug(
                        f"Successfully bulk persisted {persisted_count} items "
                        f"({len(chat_models)} chats, {len(event_models)} events)."
                    )
                    return persisted_count
            except Exception as exc:
                self._failed_batches += 1
                logger.warning(
                    f"Bulk persistence commit failed for {len(items_to_persist)} items: {exc}. "
                    "Initiating item-level fallback recovery."
                )

            # 3. Item-level fallback recovery: commit valid items, re-queue transient failures, isolate poison pills
            requeue_items: List[QueuedBatchItem] = []
            for q_item in items_to_persist:
                try:
                    make_transient(q_item.item)
                except Exception:
                    pass

                try:
                    async with session_maker() as item_session:
                        item_session.add(q_item.item)
                        await item_session.commit()
                        persisted_count += 1
                        self._flushed_total += 1
                        with self._sync_lock:
                            if q_item.item_type == "chat":
                                self._in_flight_chats.pop(q_item.item_id, None)
                            else:
                                self._in_flight_events.pop(q_item.item_id, None)
                except Exception as item_err:
                    q_item.retries += 1
                    if q_item.retries < self.max_retries:
                        logger.warning(
                            f"Item '{q_item.item_id}' ({q_item.item_type}) failed to persist (attempt {q_item.retries}/{self.max_retries}): {item_err}. "
                            "Re-queuing for next batch flush."
                        )
                        requeue_items.append(q_item)
                    else:
                        with self._sync_lock:
                            if q_item.item_type == "chat":
                                self._in_flight_chats.pop(q_item.item_id, None)
                            else:
                                self._in_flight_events.pop(q_item.item_id, None)
                        self._record_dead_letter(q_item, str(item_err))

            # 4. Re-queue transiently failed items at front of queue
            if requeue_items:
                with self._sync_lock:
                    for q_item in reversed(requeue_items):
                        self._queue.appendleft(q_item)
                        if q_item.item_type == "chat":
                            self._in_flight_chats.pop(q_item.item_id, None)
                            self._pending_chats[q_item.item_id] = q_item.item  # type: ignore[assignment]
                        else:
                            self._in_flight_events.pop(q_item.item_id, None)
                            self._pending_events[q_item.item_id] = q_item.item  # type: ignore[assignment]

            self._flush_cycles += 1
            return persisted_count

    async def flush_lounge(self, lounge_id: str) -> int:
        """Drains and persists all pending items specifically for the given lounge."""
        return await self.flush(lounge_id=lounge_id)

    def _record_dead_letter(self, q_item: QueuedBatchItem, error_msg: str) -> None:
        """Isolate an unpersistable item into the dead-letter log to prevent silent data loss."""
        self._dead_letter_total += 1
        raw_payload: Any = {}
        if hasattr(q_item.item, "__dict__"):
            raw_payload = {
                k: str(v)
                for k, v in q_item.item.__dict__.items()
                if not k.startswith("_")
            }
        else:
            raw_payload = str(q_item.item)

        record = {
            "id": q_item.item_id,
            "itemType": q_item.item_type,
            "loungeId": q_item.lounge_id,
            "error": error_msg,
            "retries": q_item.retries,
            "failedAt": utc_now().isoformat(),
            "payload": raw_payload,
        }
        self._dead_letter.append(record)
        logger.error(
            f"Item '{q_item.item_id}' permanently failed persistence after {q_item.retries} retries: {error_msg}. "
            "Isolated to dead-letter store.",
            extra={"deadLetter": record},
        )

    # --------------------------------------------------------------------------
    # Background Worker Loop & Lifecycle
    # --------------------------------------------------------------------------

    async def _worker_loop(self) -> None:
        """Continuous background loop triggering flushes on interval or capacity."""
        logger.info(
            f"BatchPersistenceWorker loop started (interval={self.flush_interval_seconds}s, "
            f"max_batch_size={self.max_batch_size})."
        )
        while self._running:
            try:
                try:
                    await asyncio.wait_for(
                        self._flush_event.wait(),
                        timeout=self.flush_interval_seconds,
                    )
                except asyncio.TimeoutError:
                    pass

                self._flush_event.clear()

                if self._queue:
                    await self.flush()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(
                    f"Unexpected error in batch persistence worker loop: {exc}",
                    exc_info=exc,
                )
                await asyncio.sleep(0.5)

    def start(
        self,
        flush_interval_seconds: Optional[float] = None,
        max_batch_size: Optional[int] = None,
    ) -> asyncio.Task:
        """Start the background batch persistence worker task."""
        if flush_interval_seconds is not None:
            self.flush_interval_seconds = flush_interval_seconds
        if max_batch_size is not None:
            self.max_batch_size = max_batch_size

        if self._running and self._worker_task and not self._worker_task.done():
            return self._worker_task

        self._running = True
        self._flush_event.clear()
        self._worker_task = asyncio.create_task(self._worker_loop())
        return self._worker_task

    async def stop(self) -> int:
        """Stop the background worker loop and flush all remaining pending records."""
        if not self._running:
            return 0

        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

        # Final drain flush of all remaining items
        flushed = await self.flush()
        logger.info(f"BatchPersistenceWorker stopped cleanly. Flushed {flushed} remaining items.")
        return flushed

    # --------------------------------------------------------------------------
    # Properties, Status & Testing Utilities
    # --------------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._running and self._worker_task is not None and not self._worker_task.done()

    @property
    def pending_count(self) -> int:
        return len(self._queue)

    @property
    def pending_chat_count(self) -> int:
        return len(self._pending_chats)

    @property
    def pending_event_count(self) -> int:
        return len(self._pending_events)

    def get_dead_letter_items(self) -> List[Dict[str, Any]]:
        """Return a copy of all dead-letter records."""
        return list(self._dead_letter)

    def get_stats(self) -> Dict[str, Any]:
        """Return operational telemetry and metrics."""
        with self._sync_lock:
            pending_count = len(self._queue)
            pending_chats = len(self._pending_chats)
            pending_events = len(self._pending_events)
            in_flight_chats = len(self._in_flight_chats)
            in_flight_events = len(self._in_flight_events)
            dead_letter_count = len(self._dead_letter)

        return {
            "isRunning": self.is_running,
            "pendingCount": pending_count,
            "pendingChatCount": pending_chats,
            "pendingEventCount": pending_events,
            "inFlightChatCount": in_flight_chats,
            "inFlightEventCount": in_flight_events,
            "flushedTotal": self._flushed_total,
            "flushCycles": self._flush_cycles,
            "failedBatches": self._failed_batches,
            "deadLetterTotal": self._dead_letter_total,
            "deadLetterCount": dead_letter_count,
            "flushIntervalSeconds": self.flush_interval_seconds,
            "maxBatchSize": self.max_batch_size,
        }

    def reset_for_testing(self) -> None:
        """Reset all in-memory queues, caches, and counters for deterministic test isolation."""
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
        self._running = False
        self._worker_task = None
        with self._sync_lock:
            self._queue.clear()
            self._pending_chats.clear()
            self._pending_events.clear()
            self._in_flight_chats.clear()
            self._in_flight_events.clear()
            self._dead_letter.clear()
        self._lock_obj = None
        self._flush_event_obj = None
        self._flushed_total = 0
        self._flush_cycles = 0
        self._failed_batches = 0
        self._dead_letter_total = 0


# Shared singleton instance
batch_persistence = BatchPersistenceWorker()
