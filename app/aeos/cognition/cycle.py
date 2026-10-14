"""
CognitiveCycle — runs the ordered phases for exactly one decision cycle.

This is the "brain loop". It executes each phase, emits phase/cycle lifecycle
events on the bus, isolates phase failures, and returns a structured report of
what the employee observed, decided and did.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Dict, List

from ..core.events import Event, EventType
from .phases import ICognitivePhase

if TYPE_CHECKING:
    from .context import ExecutionContext

logger = logging.getLogger("aeos.cognition.cycle")


class CognitiveCycle:
    """Executes the phase pipeline for a single :class:`ExecutionContext`."""

    def __init__(self, phases: List[ICognitivePhase], event_bus=None) -> None:
        self._phases = phases
        self._bus = event_bus

    def run(self, context: "ExecutionContext") -> Dict[str, Any]:
        eid = context.config.id
        started = time.time()
        if self._bus:
            self._bus.publish(Event(EventType.CYCLE_STARTED, employee_id=eid,
                                    payload={"cycle_id": context.cycle_id}))

        phase_timings: Dict[str, int] = {}
        for phase in self._phases:
            p_start = time.time()
            try:
                phase.run(context)
            except Exception:  # noqa: BLE001 - one phase must not kill the cycle
                logger.exception("phase '%s' failed for employee %s", phase.name, eid)
                context.scratch.setdefault("phase_errors", []).append(phase.name)
            finally:
                phase_timings[phase.name] = int((time.time() - p_start) * 1000)
                if self._bus:
                    self._bus.publish(Event(EventType.PHASE_COMPLETED, employee_id=eid,
                                            payload={"phase": phase.name}))

        duration_ms = int((time.time() - started) * 1000)
        report = {
            "cycle_id": context.cycle_id,
            "employee_id": eid,
            "event_type": context.event.type.value,
            "thought": context.scratch.get("thought"),
            "analysis": context.scratch.get("analysis"),
            "observations": context.scratch.get("observations"),
            # هوية صانع المحتوى: خطة الدورة وأداء المحتوى المقيس فعلاً
            "content_plan": context.scratch.get("content_plan"),
            "performance_report": context.scratch.get("performance_report"),
            "emotion_state": context.scratch.get("emotion_state"),
            "critique": context.scratch.get("critique"),
            "plan": context.scratch.get("plan_preview"),
            "decisions": context.scratch.get("decision_log"),
            "initial_decisions": context.scratch.get("initial_decisions"),
            "execution_plan": context.scratch.get("execution_plan"),
            "guard_report": context.scratch.get("guard_report"),
            "results": context.scratch.get("execution_results"),
            "directed_note": (context.scratch.get("directed_engagement_note")
                              or context.scratch.get("directed_mission_note")),
            "evaluation": context.scratch.get("evaluation"),
            "lesson": context.scratch.get("lesson"),
            "help_request": context.scratch.get("help_request"),
            "phase_errors": context.scratch.get("phase_errors", []),
            # الأدوات والمهارات الفعلية في هذه الدورة: البحث قبل الكتابة + أدوات MCP المستدعاة
            "decision_trace": context.scratch.get("decision_trace") or [],
            "decision_review": context.scratch.get("decision_review"),
            "suspended_arms": context.scratch.get("suspended_arms") or [],
            "research": context.scratch.get("research_log") or [],
            "tool_calls": context.scratch.get("tool_use") or [],
            "available_tools": sorted({f"{t['label']}.{t['tool']}" for t in context.scratch.get("available_tools") or []}),
            "phase_timings_ms": phase_timings,
            "duration_ms": duration_ms,
        }

        if self._bus:
            self._bus.publish(Event(EventType.CYCLE_COMPLETED, employee_id=eid, payload=report))
        return report
