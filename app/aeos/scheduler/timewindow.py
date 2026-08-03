"""
Active-hours / working-window logic — "when the employees turn on and off".

An employee only wakes during its configured active hours (in its own timezone).
Outside that window it is effectively "asleep": the scheduler will not enqueue it,
and the runtime schedules the next wake at the start of the next active window.

Supports:
- normal windows           (start=8,  end=23)  -> 08:00–23:00
- 24h operation            (start==end)         -> always active
- overnight/wrap windows   (start=22, end=6)    -> 22:00–06:00 next day
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..employee.models import SchedulerConfig

logger = logging.getLogger("aeos.timewindow")


def _tz(name: str):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - fall back to UTC if tz data missing
        return timezone.utc


def is_within_active_hours(sched: "SchedulerConfig", now_utc: datetime | None = None) -> bool:
    """Whether ``now`` falls inside the employee's active window."""
    now_utc = now_utc or datetime.now(timezone.utc)
    start, end = sched.active_hours_start, sched.active_hours_end
    if start == end:
        return True  # 24h operation

    local = now_utc.astimezone(_tz(sched.timezone))
    hour = local.hour + local.minute / 60.0

    if start < end:
        return start <= hour < end
    # overnight wrap (e.g. 22 -> 6)
    return hour >= start or hour < end


def next_window_start(sched: "SchedulerConfig", from_utc: datetime | None = None) -> datetime:
    """The UTC time of the next moment the window opens (>= from)."""
    from_utc = from_utc or datetime.now(timezone.utc)
    tz = _tz(sched.timezone)
    local = from_utc.astimezone(tz)

    candidate = local.replace(hour=sched.active_hours_start, minute=0,
                              second=0, microsecond=0)
    if candidate <= local:
        candidate = candidate + timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def compute_next_wake(sched: "SchedulerConfig", from_utc: datetime | None = None) -> datetime:
    """Compute the next wake time honouring interval AND active hours.

    - If disabled: pushes far into the future (won't be picked up).
    - Normal case: now + interval, but if that lands outside the window, snap to
      the next window start.
    """
    from_utc = from_utc or datetime.now(timezone.utc)
    if not sched.enabled:
        return from_utc + timedelta(days=3650)

    candidate = from_utc + timedelta(minutes=max(1, sched.interval_minutes))
    if is_within_active_hours(sched, candidate):
        return candidate
    return next_window_start(sched, candidate)
