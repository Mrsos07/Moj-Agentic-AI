"""
حدود الدورة الواحدة: الإلغاء، المهلة، نداءات النموذج ورموزه وتكلفته، نداءات الأدوات.

الميزانية تُربط بالخيط الذي يشغّل الدورة عبر ``CURRENT`` (ContextVar)، فيقرؤها
``RoutedLLMProvider`` لكل نداء — من سياق الدورة أو من خادم المحتوى (المزوّد نفسه) — بلا تعديل
مواضع الاستدعاء. تجاوز حدّ ⇒ ``BudgetExceeded``؛ الإلغاء أو فقدان العقد ⇒ ``Cancelled``.
"""
from __future__ import annotations

import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class Cancelled(RuntimeError):
    """طُلب إلغاء الدورة (المالك، الإيقاف، فقدان العقد)."""


class BudgetExceeded(RuntimeError):
    """تجاوزت الدورة حداً (مهلة، نداءات نموذج، رموز، أدوات، تكلفة)."""

    def __init__(self, limit: str, detail: str = "") -> None:
        super().__init__(f"{limit}: {detail}" if detail else limit)
        self.limit = limit


@dataclass
class CycleBudget:
    deadline: Optional[float] = None          # time.monotonic()
    total_seconds: Optional[float] = None     # طول المهلة كاملة (لحساب «الضغط» نسبياً)
    max_llm_calls: int = 0
    max_llm_tokens: int = 0
    max_tool_calls: int = 0
    max_cost_usd: float = 0.0
    cost_per_1k_tokens_usd: float = 0.0
    llm_calls: int = 0
    llm_tokens: int = 0
    tool_calls: int = 0
    cancel_event: threading.Event = field(default_factory=threading.Event)
    cancel_reason: str = ""
    exhausted: str = ""                       # أول حدّ تُجووز
    #: يسأل المصدر الدائم «هل طُلب الإلغاء؟» (القاعدة) — كل ثانية على الأكثر، فلا ينتظر الإلغاء النبض التالي
    poll: Optional[Any] = None
    poll_every: float = 1.0
    _last_poll: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ── الإلغاء والمهلة ──
    def cancel(self, reason: str) -> None:
        if not self.cancel_event.is_set():
            self.cancel_reason = reason
            self.cancel_event.set()

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def under_pressure(self, cap_seconds: float = 90.0, share: float = 0.15) -> bool:
        """المهلة المتبقية أقل من min(90 ث، 15% من المهلة كاملة)."""
        rem = self.remaining()
        if rem is None:
            return False
        limit = min(cap_seconds, share * self.total_seconds) if self.total_seconds else cap_seconds
        return rem < limit

    def remaining(self) -> Optional[float]:
        return None if self.deadline is None else self.deadline - time.monotonic()

    def check(self) -> None:
        """نقطة فحص تعاونية: بين المراحل وقبل كل أثر خارجي وكل نداء نموذج."""
        if self.poll is not None and not self.cancelled and time.monotonic() - self._last_poll >= self.poll_every:
            self._last_poll = time.monotonic()
            try:
                reason = self.poll()
            except Exception:  # noqa: BLE001 - تعذّر السؤال: النبض يبقى المسار الاحتياطي
                reason = None
            if reason:
                self.cancel(reason)
        if self.cancelled:
            raise Cancelled(self.cancel_reason or "cancelled")
        rem = self.remaining()
        if rem is not None and rem <= 0:
            self._exhaust("deadline")
            raise BudgetExceeded("deadline", "انتهت مهلة الدورة")

    def _exhaust(self, limit: str) -> None:
        if not self.exhausted:
            self.exhausted = limit

    # ── المحاسبة ──
    def charge_llm(self, prompt_chars: int, output_chars: int = 0) -> None:
        self.check()
        with self._lock:
            tokens = (prompt_chars + output_chars) // 4 + 1
            if self.max_llm_calls and self.llm_calls + 1 > self.max_llm_calls:
                self._exhaust("llm_calls")
                raise BudgetExceeded("llm_calls", f"حد نداءات النموذج {self.max_llm_calls}")
            if self.max_llm_tokens and self.llm_tokens + tokens > self.max_llm_tokens:
                self._exhaust("llm_tokens")
                raise BudgetExceeded("llm_tokens", f"حد الرموز {self.max_llm_tokens}")
            if self.max_cost_usd and self.cost_per_1k_tokens_usd and \
                    (self.llm_tokens + tokens) / 1000 * self.cost_per_1k_tokens_usd > self.max_cost_usd:
                self._exhaust("cost")
                raise BudgetExceeded("cost", f"حد التكلفة {self.max_cost_usd}$")
            self.llm_calls += 1
            self.llm_tokens += tokens

    def add_output(self, output_chars: int) -> None:
        with self._lock:
            self.llm_tokens += output_chars // 4

    def charge_tool(self) -> None:
        self.check()
        with self._lock:
            if self.max_tool_calls and self.tool_calls + 1 > self.max_tool_calls:
                self._exhaust("tool_calls")
                raise BudgetExceeded("tool_calls", f"حد الأدوات {self.max_tool_calls}")
            self.tool_calls += 1

    def snapshot(self) -> Dict[str, Any]:
        return {"llm_calls": self.llm_calls, "llm_tokens_est": self.llm_tokens, "tool_calls": self.tool_calls,
                "cost_usd_est": round(self.llm_tokens / 1000 * self.cost_per_1k_tokens_usd, 4),
                "exhausted": self.exhausted or None, "cancelled": self.cancel_reason or None,
                "remaining_s": None if self.deadline is None else round(self.remaining() or 0, 1)}

    @classmethod
    def from_config(cls, cfg, deadline_seconds: Optional[float] = None) -> "CycleBudget":
        secs = cfg.cycle_deadline_seconds if deadline_seconds is None else deadline_seconds
        return cls(deadline=time.monotonic() + secs if secs else None, total_seconds=secs or None,
                   max_llm_calls=cfg.max_llm_calls, max_llm_tokens=cfg.max_llm_tokens,
                   max_tool_calls=cfg.max_tool_calls, max_cost_usd=cfg.max_cost_usd,
                   cost_per_1k_tokens_usd=cfg.cost_per_1k_tokens_usd)


#: ميزانية الدورة الجارية في هذا الخيط (None = بلا حدود: الوضع القديم)
CURRENT: ContextVar[Optional[CycleBudget]] = ContextVar("aeos_cycle_budget", default=None)

#: حاجز تزامن نداءات النموذج عبر كل العمال (bulkhead) — يُضبط من WorkerConfig
_llm_slots: Optional[threading.BoundedSemaphore] = None


def configure_llm_bulkhead(n: int) -> None:
    global _llm_slots
    _llm_slots = threading.BoundedSemaphore(max(1, int(n))) if n else None


class llm_slot:
    """``with llm_slot():`` — ينتظر مكاناً في حاجز النموذج، ويحترم إلغاء الدورة ومهلتها."""

    def __enter__(self):
        sem = _llm_slots
        self._sem = sem
        if sem is None:
            return self
        b = CURRENT.get()
        while not sem.acquire(timeout=0.5):
            if b is not None:
                b.check()
        return self

    def __exit__(self, *exc):
        if self._sem is not None:
            self._sem.release()
        return False
