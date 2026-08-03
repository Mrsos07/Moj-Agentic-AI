"""
ExecutionJob — a unit of work handed to a shared worker.

Produced by the scheduler / API / webhooks, consumed by workers. Carries the
wake event and the target employee. Kept tiny and serializable so it can live in
memory now and in Redis/DB later without change.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..core.events import Event, EventType


@dataclass(order=True)
class ExecutionJob:
    # ``order=True`` + sort_index first => priority queue by (priority, created)
    sort_index: tuple = field(init=False, repr=False)

    employee_id: str
    event: Event
    priority: int = 3            # 1 (highest) .. 5 (lowest)
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    attempts: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "sort_index", (self.priority, self.created_at.timestamp()))

    @classmethod
    def from_event(cls, event: Event, priority: int = 3) -> "ExecutionJob":
        return cls(employee_id=event.employee_id or "", event=event, priority=priority)
