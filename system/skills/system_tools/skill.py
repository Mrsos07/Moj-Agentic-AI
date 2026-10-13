"""
مهارة موظف مدمجة في النظام: system_tools — استخدام أدوات MCP المتاحة تلقائياً.

تعمل لكل موظف دون تثبيت (``always_on``): في كل دورة تكتشف أدوات القراءة المتاحة له (خوادم
النظام المسموحة للموظفين + أدواته الخاصة) وتصنّفها (بحث / قراءة صفحات / أخبار)، ثم تُعلم
الموظف بها في تعليماته. الاستدعاء الفعلي يحدث عند الكتابة (منشور/ثريد/اقتباس، والردود إن
فعّلها المالك) عبر ``app.aeos.employee.tool_use`` — فلا تُصرف نداءات على اقتراحات قد تُرفض.

لا تقترح أفعالاً على X بنفسها: دورها أن تجعل كل مهارة كتابة أخرى تستفيد من أي أداة تُضاف.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List

from app.aeos.skills import builtin as _h

if TYPE_CHECKING:  # pragma: no cover
    from app.aeos.cognition.context import ExecutionContext


class SystemToolsSkill(_h.ISkill):
    """يكتشف أدوات MCP المتاحة للموظف في كل دورة ويُعلمه بها؛ تُستدعى تلقائياً عند الكتابة."""

    name = "system_tools"
    always_on = True
    claim_priority = -100          # لا يحجز أهدافاً — لا يزاحم مهارات التفاعل
    uses_capabilities: List[str] = []

    def propose(self, context: "ExecutionContext") -> List[_h.ProposedAction]:
        from app.aeos.employee import tool_use

        try:
            tools = tool_use.available(context.config)
        except Exception:  # noqa: BLE001 - الأدوات تحسين لا شرط للدورة
            return []
        context.scratch["available_tools"] = tools
        context.scratch["tool_briefing"] = tool_use.briefing(context.config) if tools else ""
        # إعلام المالك مرة عند تغيّر مجموعة الأدوات (لا كل دورة)
        signature = sorted({f"{t['label']}:{t['role']}" for t in tools})
        flags = context.config.runtime_state.flags
        if signature and flags.get("tools_signature") != signature:
            flags["tools_signature"] = signature
            context.emit_progress(
                "🧰 أدوات صرت أستخدمها تلقائياً قبل الكتابة: "
                + "، ".join(dict.fromkeys(t["label"] for t in tools)), intent="aeos_cycle_thinking")
        elif not signature:
            flags.pop("tools_signature", None)
        return []
