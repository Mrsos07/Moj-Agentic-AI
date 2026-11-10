"""
مهارة موظف مدمجة في النظام: stock_media — صورة/فيديو Pixabay يطابق موضوع المنشور.

تعمل لكل موظف دون تثبيت (``always_on``). العمل الفعلي لحظة النشر في
``app.aeos.capabilities.handlers._stock_media`` عبر ``app.aeos.employee.stock_media`` — هنا فقط
تُسجَّل في الدورة ليراها المالك ويطفئها المطوّر من اللوحة. لا تقترح أفعالاً على X بنفسها.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List

from app.aeos.skills import builtin as _h

if TYPE_CHECKING:  # pragma: no cover
    from app.aeos.cognition.context import ExecutionContext


class StockMediaSkill(_h.ISkill):
    """يرفق ميديا Pixabay بالمنشور عند النشر؛ لا يحجز أهدافاً ولا يقترح أفعالاً."""

    name = "stock_media"
    always_on = True
    claim_priority = -100
    uses_capabilities: List[str] = []

    def propose(self, context: "ExecutionContext") -> List[_h.ProposedAction]:
        from app.aeos.employee import stock_media

        context.scratch["stock_media_enabled"] = stock_media.available(context)
        return []
