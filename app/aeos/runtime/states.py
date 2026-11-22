"""
دورة حياة مهمة العامل (``execution_jobs.status``) — الانتقالات المسموحة فقط.

    CREATED → QUEUED → LEASED → RUNNING ⇄ WAITING → COMPLETED | FAILED | CANCELLED | TIMED_OUT
                ↑         │         │
                └─────────┴─────────┘  (إعادة الطابور: فقدان العقد قبل أي أثر خارجي، أو إعادة محاولة)

كل تغيير حالة يمر بـ ``check`` ويُنفَّذ في الطابور بشرط الحالة الحالية ورمز السياج (fencing token)
في جملة UPDATE واحدة — فلا انتقال غير مسموح ولا انتقال من عامل فقد عقده.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, FrozenSet


class JobState(str, Enum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    LEASED = "LEASED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


S = JobState
TERMINAL: FrozenSet[JobState] = frozenset({S.COMPLETED, S.FAILED, S.CANCELLED, S.TIMED_OUT})
#: حالات يملك فيها عامل عقداً (مهمة واحدة نشطة لكل موظف — فهرس فريد جزئي في القاعدة)
ACTIVE: FrozenSet[JobState] = frozenset({S.LEASED, S.RUNNING, S.WAITING})

TRANSITIONS: Dict[JobState, FrozenSet[JobState]] = {
    S.CREATED: frozenset({S.QUEUED, S.CANCELLED}),
    S.QUEUED: frozenset({S.LEASED, S.CANCELLED, S.TIMED_OUT}),
    S.LEASED: frozenset({S.RUNNING, S.QUEUED, S.CANCELLED, S.FAILED, S.TIMED_OUT}),
    S.RUNNING: frozenset({S.WAITING, S.COMPLETED, S.FAILED, S.CANCELLED, S.TIMED_OUT, S.QUEUED}),
    S.WAITING: frozenset({S.RUNNING, S.COMPLETED, S.FAILED, S.CANCELLED, S.TIMED_OUT, S.QUEUED}),
    S.COMPLETED: frozenset(),
    S.FAILED: frozenset(),
    S.CANCELLED: frozenset(),
    S.TIMED_OUT: frozenset(),
}


class InvalidTransition(RuntimeError):
    pass


def check(current: str, target: str) -> None:
    a, b = JobState(current), JobState(target)
    if b not in TRANSITIONS[a]:
        raise InvalidTransition(f"انتقال غير مسموح: {a.value} → {b.value}")


def sources_of(target: JobState) -> FrozenSet[JobState]:
    """الحالات التي يُسمح منها بالانتقال إلى ``target`` (شرط WHERE في الطابور)."""
    return frozenset(s for s, outs in TRANSITIONS.items() if target in outs)
