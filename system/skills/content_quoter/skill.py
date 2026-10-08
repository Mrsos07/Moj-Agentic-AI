"""
مهارة موظف مدمجة في النظام: content_quoter — المصدر الوحيد لكودها (system/skills/content_quoter/).

المساعدات المشتركة بين المهارات (فلاتر السبام والمحظورات، حجز الأهداف، الميزانيات…) في
``app.aeos.skills.builtin`` وتُستدعى عبر ``_h.`` — فأي تعديل عليها (أو استبدال في اختبار)
يصل لكل المهارات.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List

from app.aeos.skills import builtin as _h

if TYPE_CHECKING:  # pragma: no cover
    from app.aeos.cognition.context import ExecutionContext


class ContentQuoterSkill(_h.ISkill):
    """Quote-tweet the strongest in-field post with the employee's own take.

    الاقتباس أقوى من إعادة النشر الصامتة: يبني صوت الحساب وموقفه بدل نقل كلام
    غيره، ويجذب جمهور الحساب الأصلي إلى النقاش. نختار أفضل عنصر في مجمّع
    التفاعل (أعلى صلة، ومنشورات الأداء العالي أولاً) ونطلب تعليقاً يضيف زاوية.
    """

    name = "content_quoter"
    claim_priority = 60
    uses_capabilities = ["quote_post"]

    def propose(self, context: "ExecutionContext") -> List[_h.ProposedAction]:
        rules = context.config.business_rules or {}
        if int(rules.get("daily_quotes_target", 2)) <= 0:
            return []
        per_cycle = int(rules.get("max_quotes_per_cycle", 1))
        if per_cycle <= 0:
            return []
        pool = _h._engagement_pool(context)
        if not pool:
            return []
        # اقتبس ما يستحق تعليقاً فعلاً: نص كافٍ وصلة عالية بالتخصص.
        # العتبة قاعدة عمل لا رقم مدفون: 80 حرفاً كانت تُلغي الاقتباس عملياً على
        # المحتوى العربي (تغريدات المجال تقع غالباً في 60-80 حرفاً)، فقيست
        # المحاكاة صفر اقتباس في 40 دورة رغم أن القدرة مفعّلة وميزانيتها كاملة.
        min_chars = int(rules.get("min_quote_chars", 60))
        candidates = _h.claim_targets(context, [
            t for t in pool
            if len((t.get("text") or "").strip()) >= min_chars
            and int(t.get("_rel", 0) or 0) >= 1
        ], per_cycle, owner="content_quoter")
        plan = _h._content_plan(context)
        return [_h.ProposedAction(
            capability="quote_post",
            params={
                "tweet_url": t.get("url", ""),
                "original_text": t.get("text", ""),
                "handle": t.get("handle", ""),
                "pillar": plan.get("pillar", ""),
            },
            rationale=(f"quote {t.get('handle', '')} to add our own angle "
                       f"(rel={t.get('_rel', 0)})"),
            expected_impact=round(min(0.66 + 0.03 * int(t.get("_rel", 0) or 0), 0.9), 3),
            urgency=0.5, risk=0.25,
        ) for t in candidates]
