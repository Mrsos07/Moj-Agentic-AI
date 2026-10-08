"""
مهارة موظف مدمجة في النظام: trend_watcher — المصدر الوحيد لكودها (system/skills/trend_watcher/).

المساعدات المشتركة بين المهارات (فلاتر السبام والمحظورات، حجز الأهداف، الميزانيات…) في
``app.aeos.skills.builtin`` وتُستدعى عبر ``_h.`` — فأي تعديل عليها (أو استبدال في اختبار)
يصل لكل المهارات.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List

from app.aeos.skills import builtin as _h

if TYPE_CHECKING:  # pragma: no cover
    from app.aeos.cognition.context import ExecutionContext


class TrendWatcherSkill(_h.ISkill):
    """Proposes researching a source URL provided via a news event."""

    name = "trend_watcher"
    claim_priority = 5
    uses_capabilities = ["research_topic", "store_knowledge"]

    def propose(self, context: "ExecutionContext") -> List[_h.ProposedAction]:
        payload = context.event.payload or {}
        url = payload.get("url") or payload.get("link")
        if url:
            return [_h.ProposedAction(
                capability="research_topic",
                params={"url": url, "topic": payload.get("title", "")},
                rationale="news/trend source available for research",
                expected_impact=0.5, urgency=0.5, risk=0.1,
            )]
        return []
