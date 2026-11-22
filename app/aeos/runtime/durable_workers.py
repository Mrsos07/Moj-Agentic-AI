"""
مجمّع العمال الدائم (AEOS_RUNTIME=durable): يستلم من ``DbJobQueue`` ويشغّل دورة واحدة لكل عقد.

لكل مهمة: ميزانية دورة (مهلة/نموذج/أدوات)، خيط نبض يمدّد العقد ويوصل طلب الإلغاء، وسياق
``CURRENT_JOB`` (أي مهمة تُدرج أثناءها تصير ابنتها). النتيجة تُكتب بعد الحفظ الآمن (``run_cycle``
يحفظ قبل أن يعود) ⇒ اعتراف بعد الحفظ. فقدان العقد ⇒ إلغاء تعاوني ولا كتابة.

قاطع الدائرة يوقف الاستلام بعد إخفاقات متتالية (القاعدة/النموذج معطّلان) فتبقى المهام في الطابور بدل
استنزاف محاولاتها. ``stop(drain=True)``: لا استلام جديد، ينتظر الجاري حتى ``drain_seconds`` ثم يلغيه
تعاونياً؛ ما لم يبدأ أثراً خارجياً يعود للطابور.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Optional

from .budget import CURRENT, CycleBudget, configure_llm_bulkhead
from .config import WorkerConfig
from .durable_queue import CURRENT_JOB, DbJobQueue, Lease, StaleLease
from .metrics import CircuitBreaker, RuntimeMetrics

logger = logging.getLogger("aeos.runtime.workers")

SHUTDOWN = "إيقاف الخادم"
LOST_LEASE = "فُقد العقد"


class DurableWorkerPool:
    def __init__(self, queue: DbJobQueue, runtime, cfg: Optional[WorkerConfig] = None,
                 metrics: Optional[RuntimeMetrics] = None) -> None:
        self.queue = queue
        self.runtime = runtime
        self.cfg = cfg or queue.cfg
        self.metrics = metrics or RuntimeMetrics()
        self.breaker = CircuitBreaker(self.cfg.breaker_failures, self.cfg.breaker_cooldown_seconds)
        self._threads: List[threading.Thread] = []
        self._stop = threading.Event()
        self._draining = threading.Event()
        self._wake = threading.Event()
        self._inflight: Dict[str, CycleBudget] = {}
        self._lock = threading.Lock()

    # ───────────────────────── دورة الحياة ─────────────────────────
    def start(self) -> None:
        if self._threads:
            return
        configure_llm_bulkhead(self.cfg.llm_max_concurrency)
        self._stop.clear()
        self._draining.clear()
        for i in range(self.cfg.workers):
            t = threading.Thread(target=self._loop, args=(i,), daemon=True, name=f"AEOSDurableWorker-{i}")
            t.start()
            self._threads.append(t)
        r = threading.Thread(target=self._reclaimer, daemon=True, name="AEOSReclaimer")
        r.start()
        self._threads.append(r)
        logger.info("durable worker pool: %d workers (%s)", self.cfg.workers, self.cfg.worker_id)

    def notify(self) -> None:
        """مهمة جديدة: أيقظ عاملاً بدل انتظار دورة الاستطلاع."""
        self._wake.set()

    def stop(self, drain: bool = True, timeout: Optional[float] = None) -> Dict[str, int]:
        self._draining.set()
        self._wake.set()
        deadline = time.monotonic() + (self.cfg.drain_seconds if timeout is None else timeout)
        if drain:
            while time.monotonic() < deadline and self.inflight():
                time.sleep(0.2)
        cancelled = 0
        with self._lock:
            for b in self._inflight.values():
                b.cancel(SHUTDOWN)
                cancelled += 1
        self._stop.set()
        for t in self._threads:
            t.join(timeout=max(0.5, deadline - time.monotonic() + 5))
        left = self.inflight()
        self._threads.clear()
        logger.info("durable worker pool stopped (cancelled %d, still running %d)", cancelled, left)
        return {"cancelled": cancelled, "still_running": left}

    def inflight(self) -> int:
        with self._lock:
            return len(self._inflight)

    # ───────────────────────── الحلقات ─────────────────────────
    def _reclaimer(self) -> None:
        while not self._stop.wait(self.cfg.reclaim_every_seconds):
            try:
                for item in self.queue.reclaim_expired():
                    self.metrics.inc(f"reclaimed_{item['action']}")
            except Exception:  # noqa: BLE001
                logger.exception("reclaim failed")

    def _loop(self, i: int) -> None:
        while not self._stop.is_set() and not self._draining.is_set():
            if not self.breaker.allow():
                self._wake.wait(self.cfg.poll_seconds)
                continue
            try:
                lease = self.queue.claim(self.cfg.worker_id)
            except Exception:  # noqa: BLE001 - القاعدة معطّلة: لا تدُر حلقة ساخنة
                logger.exception("claim failed")
                self.breaker.record(False)
                self._wake.wait(self.cfg.poll_seconds * 5)
                continue
            if lease is None:
                self._wake.wait(self.cfg.poll_seconds)
                self._wake.clear()
                continue
            self.metrics.inc("claimed")
            self.run_lease(lease)

    # ───────────────────────── تنفيذ مهمة ─────────────────────────
    def run_lease(self, lease: Lease) -> str:
        """شغّل عقداً واحداً حتى نهايته. يعيد الحالة النهائية المكتوبة (أو ``stale``)."""
        remaining = self.cfg.cycle_deadline_seconds
        if lease.deadline_at is not None:
            from datetime import datetime

            remaining = min(remaining, max(1.0, (lease.deadline_at - datetime.utcnow()).total_seconds()))
        budget = CycleBudget.from_config(self.cfg, deadline_seconds=remaining)
        lost = threading.Event()
        stop_hb = threading.Event()

        def heartbeat() -> None:
            while not stop_hb.wait(self.cfg.heartbeat_seconds):
                try:
                    if self.queue.heartbeat(lease):
                        budget.cancel("طلب الإلغاء")
                except StaleLease:
                    lost.set()
                    budget.cancel(LOST_LEASE)
                    self.metrics.inc("stale_leases")
                    return
                except Exception:  # noqa: BLE001 - نبض فائت: العقد يتسع لنبضة أخرى
                    logger.warning("heartbeat failed for %s", lease.job_id, exc_info=True)

        with self._lock:
            self._inflight[lease.job_id] = budget
        hb = threading.Thread(target=heartbeat, daemon=True, name=f"hb-{lease.job_id[:8]}")
        hb.start()
        job_token = CURRENT_JOB.set(lease)
        budget_token = CURRENT.set(budget)
        started = time.monotonic()
        outcome = "failed"
        try:
            report = self.runtime.run_cycle(lease.employee_id, lease.event, lease=lease,
                                            budget=budget, queue=self.queue) or {}
            outcome = self._finish(lease, report, budget, lost)
        except StaleLease:
            outcome = "stale"
            self.metrics.inc("stale_leases")
        except Exception as exc:  # noqa: BLE001 - عطل بنية (قاعدة/استيراد) لا خطأ دورة
            logger.exception("job %s crashed", lease.job_id)
            outcome = self._safe(lambda: self.queue.fail(lease, f"{type(exc).__name__}: {exc}"), "failed")
            self.breaker.record(False)
        finally:
            CURRENT.reset(budget_token)
            CURRENT_JOB.reset(job_token)
            stop_hb.set()
            with self._lock:
                self._inflight.pop(lease.job_id, None)
        self.metrics.cycle(time.monotonic() - started, _outcome_name(outcome))
        return outcome

    def _finish(self, lease: Lease, report: dict, budget: CycleBudget, lost: threading.Event) -> str:
        if lost.is_set() or (report.get("skipped") and report.get("reason") == "stale_lease"):
            return "stale"
        rt = report.get("runtime") or {}
        if rt.get("cancelled"):
            if rt["cancelled"] == SHUTDOWN:
                state = self._safe(lambda: self.queue.fail(lease, "أُوقف الخادم أثناء الدورة",
                                                           retryable=True, report=report), "failed")
                return state.lower()
            self._safe(lambda: self.queue.cancel_finish(lease, rt["cancelled"], report), None)
            return "cancelled"
        if rt.get("timed_out"):
            self._safe(lambda: self.queue.timeout_finish(lease, "انتهت مهلة الدورة", report), None)
            self.breaker.record(True)
            return "timed_out"
        if report.get("error"):
            state = self._safe(lambda: self.queue.fail(lease, str(report["error"]), report=report), "failed")
            self.breaker.record(False)
            return state.lower()
        self.queue.complete(lease, report)
        self.breaker.record(True)
        return "completed"

    @staticmethod
    def _safe(fn, default):
        try:
            return fn()
        except StaleLease:
            return "stale"


def _outcome_name(state: str) -> str:
    s = (state or "").lower()
    return {"queued": "retried", "failed": "failed", "completed": "completed", "cancelled": "cancelled",
            "timed_out": "timed_out", "stale": "stale"}.get(s, s or "failed")
