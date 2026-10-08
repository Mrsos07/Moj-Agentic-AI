"""
مهارة موظف مدمجة في النظام: swipe_file — المصدر الوحيد لكودها (system/skills/swipe_file/).

المساعدات المشتركة بين المهارات (فلاتر السبام والمحظورات، حجز الأهداف، الميزانيات…) في
``app.aeos.skills.builtin`` وتُستدعى عبر ``_h.`` — فأي تعديل عليها (أو استبدال في اختبار)
يصل لكل المهارات.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List

from app.aeos.skills import builtin as _h

if TYPE_CHECKING:  # pragma: no cover
    from app.aeos.cognition.context import ExecutionContext


class SwipeFileSkill(_h.ISkill):
    """Bookmark exceptional in-field posts as raw material for future content.

    كل صانع محتوى محترف يملك «ملف إلهام»: يحفظ الطرح القوي ليبني عليه لاحقاً.
    نحفظ فقط ما جاء من «أهم البوستات» (أداء عالٍ فعلاً) وضمن التخصص.
    """

    name = "swipe_file"
    claim_priority = 50
    uses_capabilities = ["bookmark_post"]

    def propose(self, context: "ExecutionContext") -> List[_h.ProposedAction]:
        rules = context.config.business_rules or {}
        if int(rules.get("daily_bookmarks_target", 4)) <= 0:
            return []
        per_cycle = int(rules.get("max_bookmarks_per_cycle", 1))
        if per_cycle <= 0:
            return []
        engaged = _h._engaged_urls(context)
        interests = getattr(context.config.persona, "interests", []) or []
        eligible: List[dict] = []
        for t in (context.scratch.get("top_posts") or [])[:10]:
            url = (t.get("url") or "").strip()
            text = (t.get("text") or "").strip()
            if not url or url in engaged or len(text) < 60:
                continue
            if _h._is_ad_or_spam_text(text) or _h._violates_persona_donts(text, context):
                continue
            if interests and _h._rel(context, text, interests, t) < int(
                    rules.get("min_relevance_score", 1)):
                continue
            eligible.append(t)

        actions: List[_h.ProposedAction] = []
        for t in _h.claim_targets(context, eligible, per_cycle, owner="swipe_file"):
            actions.append(_h.ProposedAction(
                capability="bookmark_post",
                params={"tweet_url": (t.get("url") or "").strip(),
                        "original_text": (t.get("text") or "").strip(),
                        "handle": t.get("handle", "")},
                rationale="save a high-performing in-field post as future raw material",
                expected_impact=0.3, urgency=0.25, risk=0.02,
            ))
        return actions
