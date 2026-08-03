"""
In-memory priority job queue implementing :class:`IJobQueue`.

Thread-safe, priority-ordered (lower ``priority`` value runs first). This is the
default backend; swap for a Redis/DB-backed queue to get durability and
multi-process scale without touching producers or workers.
"""
from __future__ import annotations

import heapq
import threading
from typing import List, Optional

from ..core.interfaces import IJobQueue
from ..runtime.job import ExecutionJob


class InMemoryJobQueue(IJobQueue):
    def __init__(self) -> None:
        self._heap: List[ExecutionJob] = []
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)

    def enqueue(self, job: ExecutionJob) -> None:
        with self._not_empty:
            heapq.heappush(self._heap, job)
            self._not_empty.notify()

    def dequeue(self, timeout: float = 1.0) -> Optional[ExecutionJob]:
        with self._not_empty:
            if not self._heap:
                self._not_empty.wait(timeout)
            if not self._heap:
                return None
            return heapq.heappop(self._heap)

    def size(self) -> int:
        with self._lock:
            return len(self._heap)
