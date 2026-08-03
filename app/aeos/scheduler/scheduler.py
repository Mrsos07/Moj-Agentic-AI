"""
WakeScheduler — converts time into wake events.

A single background loop periodically asks the repository which employees are due
(``next_wake_at <= now``) and enqueues one :class:`ExecutionJob` per due
employee. It does NOT execute cycles — that is the workers' job. This keeps the
scheduler cheap and lets thousands of employees share a small worker pool.

External triggers (mentions, replies, webhooks, manual runs) bypass the timer by
publishing events straight onto the bus, which the dispatcher turns into jobs.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from ..core.events import Event, EventType
from ..core.interfaces import IEmployeeRepository, IJobQueue
from ..runtime.job import ExecutionJob
from .timewindow import compute_next_wake, is_within_active_hours, next_window_start

logger = logging.getLogger("aeos.scheduler")


class WakeScheduler:
    """Timer-driven producer of wake jobs."""

    def __init__(
        self,
        repository: IEmployeeRepository,
        job_queue: IJobQueue,
        event_bus=None,
        poll_interval: float = 20.0,
    ) -> None:
        self._repo = repository
        self._queue = job_queue
        self._bus = event_bus
        self._poll = poll_interval
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

        # subscribe external triggers -> jobs
        if self._bus:
            for et in (EventType.MENTION, EventType.REPLY, EventType.WEBHOOK,
                       EventType.MANUAL, EventType.NOTIFICATION, EventType.NEWS_UPDATE):
                self._bus.subscribe(et, self._on_external_event)

    # ---- external events -> jobs ---------------------------------------
    def _on_external_event(self, event: Event) -> None:
        if not event.employee_id:
            return
        priority = 1 if event.type in (EventType.MENTION, EventType.REPLY) else 2
        self._queue.enqueue(ExecutionJob.from_event(event, priority=priority))
        logger.info("enqueued %s job for %s", event.type.value, event.employee_id)

    # ---- timer loop -----------------------------------------------------
    def _loop(self) -> None:
        logger.info("WakeScheduler started (poll=%ss)", self._poll)
        while not self._stop.is_set():
            try:
                now = datetime.now(timezone.utc)
                due = self._repo.list_due(now.isoformat())
                woke = 0
                for cfg in due:
                    # "when they turn off": skip employees outside their active hours
                    if not is_within_active_hours(cfg.scheduler, now):
                        if hasattr(self._repo, "set_next_wake"):
                            self._repo.set_next_wake(
                                cfg.id, next_window_start(cfg.scheduler, now))
                        continue

                    job = ExecutionJob.from_event(
                        Event(EventType.SCHEDULER, employee_id=cfg.id, source="scheduler"),
                        priority=3,
                    )
                    self._queue.enqueue(job)
                    woke += 1
                    # push next_wake forward immediately to avoid double-enqueue
                    if hasattr(self._repo, "set_next_wake"):
                        self._repo.set_next_wake(
                            cfg.id, compute_next_wake(cfg.scheduler, now))
                if woke:
                    logger.info("scheduled %d due employee(s)", woke)
            except Exception:  # noqa: BLE001
                logger.exception("scheduler loop error")
            self._stop.wait(self._poll)
        logger.info("WakeScheduler stopped")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="AEOSWakeScheduler")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
