"""
مهارة موظف مدمجة في النظام: unslop_guard — كتابة بلا علامات آلية لكل الموظفين.

تعمل لكل موظف دون تثبيت (``always_on``). العمل الفعلي يحدث عند الكتابة في
``app.aeos.capabilities.handlers`` عبر ``app.aeos.employee.unslop`` (منشور، رد، اقتباس، ثريد،
منشور مجدول) — هنا فقط تُسجَّل في الدورة ليراها المالك ويطفئها المطوّر من اللوحة.

لا تقترح أفعالاً على X بنفسها.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List

from app.aeos.skills import builtin as _h

if TYPE_CHECKING:  # pragma: no cover
    from app.aeos.cognition.context import ExecutionContext


class UnslopGuardSkill(_h.ISkill):
    """يطبّق unslop على كل ما يكتبه الموظف؛ لا يحجز أهدافاً ولا يقترح أفعالاً."""

    name = "unslop_guard"
    always_on = True
    claim_priority = -100          # لا يحجز أهدافاً — لا يزاحم مهارات التفاعل
    uses_capabilities: List[str] = []

    def propose(self, context: "ExecutionContext") -> List[_h.ProposedAction]:
        from app.aeos.employee import unslop

        context.scratch["unslop_enabled"] = unslop.enabled(context.config)
        return []
