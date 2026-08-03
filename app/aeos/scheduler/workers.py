"""
Worker pool — shared executors for a multi-tenant fleet of AI Employees.

A small, fixed pool of worker threads consumes jobs from the queue and asks the
RuntimeManager to run one cycle per job. Because runtimes are temporary, a
handful of workers can serve thousands of employees: an employee only occupies a
worker for the duration of a single cycle.

Scaling out = run more worker processes against a shared (Redis/DB) queue; the
code here is unchanged.
"""
from __future__ import annotations

import logging
import threading
from typing import List

from ..core.interfaces import IJobQueue, IRuntimeManager

logger = logging.getLogger("aeos.workers")


class WorkerPool:
    def __init__(
        self,
        job_queue: IJobQueue,
        runtime_manager: IRuntimeManager,
        num_workers: int = 4,
        max_attempts: int = 3,
    ) -> None:
        self._queue = job_queue
        self._runtime = runtime_manager
        self._num = num_workers
        self._max_attempts = max_attempts
        self._threads: List[threading.Thread] = []
        self._stop = threading.Event()

    def _worker_loop(self, worker_id: int) -> None:
        logger.info("worker-%d started", worker_id)
        while not self._stop.is_set():
            job = self._queue.dequeue(timeout=1.0)
            if job is None:
                continue
            try:
                logger.info("worker-%d running cycle for %s (%s)",
                            worker_id, job.employee_id, job.event.type.value)
                self._runtime.run_cycle(job.employee_id, job.event)
            except Exception:  # noqa: BLE001
                job.attempts += 1
                logger.exception("worker-%d job failed (attempt %d)", worker_id, job.attempts)
                if job.attempts < self._max_attempts:
                    self._queue.enqueue(job)  # retry
        logger.info("worker-%d stopped", worker_id)

    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        for i in range(self._num):
            t = threading.Thread(target=self._worker_loop, args=(i,), daemon=True,
                                 name=f"AEOSWorker-{i}")
            t.start()
            self._threads.append(t)
        logger.info("WorkerPool started with %d workers", self._num)

    def stop(self) -> None:
        self._stop.set()
        self._threads.clear()
