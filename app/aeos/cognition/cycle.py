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
from ..runtime.budget import BudgetExceeded, Cancelled
from .phases import ICognitivePhase

if TYPE_CHECKING:
    from .context import ExecutionContext

logger = logging.getLogger("aeos.cognition.cycle")

#: مراحل تحسينية تُتخطّى عند ضغط الميزانية (حدّ مُستنفد أو مهلة قريبة) — لا يعتمد عليها التنفيذ.
#: الحارس والقرار والتنفيذ والحفظ لا تُتخطّى للميزانية أبداً (الحارس يفشل مغلقاً إن تعذّر).
OPTIONAL_UNDER_PRESSURE = frozenset({"emotion", "critique", "graph_memory"})


def skip_reason(phase, context: "ExecutionContext", budget) -> str:
    """سبب صريح لتخطّي مرحلة، أو "" لتشغيلها. الشروط تطابق ما كانت المراحل تفعله بلا أثر."""
    name = getattr(phase, "name", "")
    if name == "guard" and not (context.scratch.get("decisions") or []):
        return "لا قرارات لمراجعتها"
    if budget is not None and name in OPTIONAL_UNDER_PRESSURE:
        if budget.exhausted:
            return f"ميزانية الدورة مستنفدة ({budget.exhausted})"
        if budget.under_pressure():
            return f"المهلة المتبقية {int(budget.remaining() or 0)} ث"
    return ""


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
        phase_skips: Dict[str, str] = {}
        phase_outputs: Dict[str, List[str]] = {}
        stop_reason = ""
        checkpoint = getattr(context, "checkpoint", None)
        budget = getattr(context, "budget", None)
        for phase in self._phases:
            # نقطة فحص تعاونية بين المراحل: إلغاء/فقدان عقد/مهلة ⇒ تتوقف المراحل الباقية (عدا الحفظ)
            if budget is not None and not stop_reason:
                try:
                    budget.check()
                except Cancelled as exc:
                    stop_reason = f"cancelled: {exc}"
                except BudgetExceeded as exc:
                    stop_reason = f"{exc.limit}: {exc}"
            skip = (f"توقفت الدورة ({stop_reason})" if stop_reason and not getattr(phase, "always_run", False)
                    else skip_reason(phase, context, budget))
            if skip:
                phase_skips[phase.name] = skip
                if callable(checkpoint):
                    checkpoint("phase", name=phase.name, total=len(self._phases))
                continue
            before = set(context.scratch)
            p_start = time.time()
            try:
                phase.run(context)
            except Exception:  # noqa: BLE001 - one phase must not kill the cycle
                logger.exception("phase '%s' failed for employee %s", phase.name, eid)
                context.scratch.setdefault("phase_errors", []).append(phase.name)
            finally:
                phase_timings[phase.name] = int((time.time() - p_start) * 1000)
                written = sorted(set(context.scratch) - before)
                if written:
                    phase_outputs[phase.name] = written
                if callable(checkpoint):
                    checkpoint("phase", name=phase.name, total=len(self._phases))
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
            "images": context.scratch.get("image_log") or [],
            "tool_calls": context.scratch.get("tool_use") or [],
            "available_tools": sorted({f"{t['label']}.{t['tool']}" for t in context.scratch.get("available_tools") or []}),
            "phase_timings_ms": phase_timings,
            # لماذا لم تعمل مرحلة، وما كتبته كل مرحلة في الدورة (قابلية التدقيق)
            "phase_skips": phase_skips,
            "unknown_actions": context.scratch.get("unknown_actions") or [],
            "phase_outputs": phase_outputs,
            "stop_reason": stop_reason or None,
            "duration_ms": duration_ms,
        }

        if self._bus:
            self._bus.publish(Event(EventType.CYCLE_COMPLETED, employee_id=eid, payload=report))
        return report
