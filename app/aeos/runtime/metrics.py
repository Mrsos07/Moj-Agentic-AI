"""
قياسات العمال (داخل العملية): الإنتاجية، تأخر الطابور، مدة الدورة p50/p95، نسبة الفشل، والعدّادات.
تُعرض في ``/api/aeos/runtime`` (للمشرف). نافذة متدحرجة: آخر 1000 دورة / 15 دقيقة.
"""
from __future__ import annotations

import threading
import time
from collections import Counter, deque
from typing import Any, Deque, Dict, Optional, Tuple


class RuntimeMetrics:
    WINDOW_S = 900

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counters: Counter = Counter()
        self._cycles: Deque[Tuple[float, float, str]] = deque(maxlen=1000)   # (ended_at, seconds, outcome)
        self.started_at = time.time()

    def inc(self, name: str, n: int = 1) -> None:
        with self._lock:
            self.counters[name] += n

    def cycle(self, seconds: float, outcome: str) -> None:
        with self._lock:
            self._cycles.append((time.time(), seconds, outcome))
            self.counters[f"cycles_{outcome}"] += 1

    @staticmethod
    def _q(values, p: float) -> Optional[float]:
        if not values:
            return None
        s = sorted(values)
        return round(s[min(len(s) - 1, int(len(s) * p))], 2)

    def snapshot(self, queue_stats: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            recent = [c for c in self._cycles if now - c[0] <= self.WINDOW_S]
            counters = dict(self.counters)
        durations = [c[1] for c in recent]
        done = [c for c in recent if c[2] in ("completed", "failed", "timed_out")]
        failed = [c for c in done if c[2] != "completed"]
        span = min(self.WINDOW_S, max(1.0, now - self.started_at))
        return {
            "window_s": self.WINDOW_S,
            "throughput_per_min": round(len(recent) / span * 60, 2),
            "cycle_p50_s": self._q(durations, 0.5), "cycle_p95_s": self._q(durations, 0.95),
            "failure_rate": round(len(failed) / len(done), 3) if done else 0.0,
            "recent_cycles": len(recent), "counters": counters,
            **({"queue": queue_stats} if queue_stats is not None else {}),
        }


class CircuitBreaker:
    """إخفاقات متتالية ⇒ مفتوح (لا استلام) لمدة التبريد ⇒ نصف مفتوح (مهمة تجريبية) ⇒ مغلق عند النجاح."""

    def __init__(self, failures: int, cooldown_s: float) -> None:
        self.threshold = max(1, failures)
        self.cooldown = cooldown_s
        self._fails = 0
        self._opened_at: Optional[float] = None
        self._probe = False
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self._opened_at is None:
                return "closed"
            return "half_open" if time.monotonic() - self._opened_at >= self.cooldown else "open"

    def allow(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return True
            if time.monotonic() - self._opened_at < self.cooldown or self._probe:
                return False
            self._probe = True          # مهمة تجريبية واحدة
            return True

    def record(self, ok: bool) -> None:
        with self._lock:
            if ok:
                self._fails, self._opened_at, self._probe = 0, None, False
                return
            self._fails += 1
            if self._probe or self._fails >= self.threshold:
                self._opened_at, self._probe = time.monotonic(), False
