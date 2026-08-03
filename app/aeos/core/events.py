"""
Event-driven backbone of AEOS.

Everything the platform does starts from an :class:`Event`. Events are what wake
an employee. The :class:`EventBus` is an in-process pub/sub used to decouple
producers (scheduler, webhooks, API) from consumers (workers, loggers,
reflection hooks).

The bus is intentionally transport-agnostic: the default implementation is
in-memory, but the :class:`EventBus` ABC can be re-implemented on top of Redis
Streams / Kafka / RabbitMQ without touching any caller.
"""
from __future__ import annotations

import abc
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List

logger = logging.getLogger("aeos.events")


class EventType(str, Enum):
    """Canonical triggers that can wake an AI Employee."""

    SCHEDULER = "scheduler"          # time-based wake
    WEBHOOK = "webhook"              # external system callback
    MANUAL = "manual"               # human-triggered run
    MENTION = "mention"             # someone mentioned the account
    REPLY = "reply"                 # someone replied to the account
    NOTIFICATION = "notification"   # platform notification
    TIME_TRIGGER = "time_trigger"   # cron-like fixed time
    NEWS_UPDATE = "news_update"     # RSS / news source update

    # lifecycle / observability events (emitted by the platform itself)
    CYCLE_STARTED = "cycle_started"
    CYCLE_COMPLETED = "cycle_completed"
    CYCLE_FAILED = "cycle_failed"
    PHASE_COMPLETED = "phase_completed"
    CAPABILITY_EXECUTED = "capability_executed"


@dataclass(frozen=True)
class Event:
    """An immutable message that flows through the platform.

    Attributes:
        type: What kind of trigger this is.
        employee_id: The employee this event targets (None = broadcast).
        payload: Arbitrary event data (mention text, webhook body, ...).
        source: Human-readable origin of the event.
        event_id: Unique id for tracing.
        created_at: UTC creation timestamp.
    """

    type: EventType
    employee_id: str | None = None
    payload: Dict[str, Any] = field(default_factory=dict)
    source: str = "system"
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "type": self.type.value,
            "employee_id": self.employee_id,
            "payload": self.payload,
            "source": self.source,
            "created_at": self.created_at.isoformat(),
        }


EventHandler = Callable[[Event], None]


class EventBus(abc.ABC):
    """Publish/subscribe contract. Swappable transport."""

    @abc.abstractmethod
    def publish(self, event: Event) -> None: ...

    @abc.abstractmethod
    def subscribe(self, event_type: EventType, handler: EventHandler) -> None: ...

    @abc.abstractmethod
    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None: ...


class InMemoryEventBus(EventBus):
    """Thread-safe in-process event bus.

    Handler exceptions are isolated: one failing subscriber never prevents the
    others from receiving the event (fault isolation).
    """

    def __init__(self) -> None:
        self._handlers: Dict[EventType, List[EventHandler]] = {}
        self._lock = threading.RLock()

    def publish(self, event: Event) -> None:
        with self._lock:
            handlers = list(self._handlers.get(event.type, ()))
        logger.debug("publish %s -> %d handler(s)", event.type.value, len(handlers))
        for handler in handlers:
            try:
                handler(event)
            except Exception:  # noqa: BLE001 - fault isolation is intentional
                logger.exception("event handler failed for %s", event.type.value)

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        with self._lock:
            self._handlers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        with self._lock:
            if handler in self._handlers.get(event_type, []):
                self._handlers[event_type].remove(handler)
