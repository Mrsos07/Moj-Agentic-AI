"""
مهارة موظف مدمجة في النظام: viral_content_creator — المصدر الوحيد لكودها (system/skills/viral_content_creator/).

المساعدات المشتركة بين المهارات (فلاتر السبام والمحظورات، حجز الأهداف، الميزانيات…) في
``app.aeos.skills.builtin`` وتُستدعى عبر ``_h.`` — فأي تعديل عليها (أو استبدال في اختبار)
يصل لكل المهارات.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List

from app.aeos.skills import builtin as _h

if TYPE_CHECKING:  # pragma: no cover
    from app.aeos.cognition.context import ExecutionContext


class ViralContentCreatorSkill(_h.ISkill):
    """Creates original, timely 'viral' content about trending topics the employee
    saw on its timeline within its field of interest.

    Picks the most relevant recent topic from the timeline and asks the content
    capability to write an engaging, shareable original post about it (grounded
    in the persona). Gated by the daily post target to avoid spamming.
    """

    name = "viral_content_creator"
    claim_priority = 35
    uses_capabilities = ["publish_content"]

    def propose(self, context: "ExecutionContext") -> List[_h.ProposedAction]:
        counters = context.config.runtime_state.counters
        posts_today = counters.get("posts_today", 0)
        target = int(context.config.business_rules.get("daily_post_target", 1))
        if posts_today >= target:
            return []

        # حسابات «في مثل هذا اليوم» لا تطارد الترندات — مهارة on_this_day تتولى النشر
        if _h._on_this_day_mode(context):
            return []

        rules = context.config.business_rules or {}
        require_grounded_topic = bool(rules.get("require_grounded_topic", True))
        enforce_scope = bool(rules.get("enforce_interest_scope", True))

        # تفضيل التفاعل = خفض أولوية النشر لا إلغاؤه (انظر _engagement_first_damping)
        damping = _h._engagement_first_damping(context)

        topic = _h._grounded_topic(context)
        if require_grounded_topic and not topic:
            return []  # بلا موضوع رائج/حقيقي لا ننشر أي شيء
        if not topic:
            return []

        interests = getattr(context.config.persona, "interests", []) or []
        if enforce_scope and interests:
            hay = f"{topic.get('title','')} {topic.get('summary','')}"
            min_rel = int(rules.get("min_relevance_score", 1))
            if _h._rel(context, hay, interests) < min_rel:
                return []

        lang = getattr(context.config.persona, "language", "ar") or "ar"
        dialect = getattr(context.config.persona, "dialect", "") or ""
        brief = (
            "اكتب منشوراً أصلياً قصيراً وقوياً قابلاً للانتشار على X "
            f"باللغة {lang}{(' وبلهجة ' + dialect) if dialect else ''} "
            f"ضمن اختصاصك فقط حول: '{topic['title']}'. "
            "ابدأ بـHook لافت، ثم موقف واضح أو Insight ذكي يدفع للتفاعل."
        )
        if topic["summary"]:
            brief += f" الخلفية المختصرة: {topic['summary']}"
        brief += (
            " اجعل النبرة بشرية واقعية ومباشرة، وتجنب العموميات والنسخ الإخباري. "
            "استخدم الميديا المرتبطة بالموضوع إن توفرت."
        )
        # مشاركة في هاشتاق الترند: ضمّن الهاشتاق نصّاً ليظهر المنشور داخل نقاشه
        if topic.get("hashtag"):
            brief += (
                f" مهم جداً: ضمّن الهاشتاق {topic['hashtag']} كما هو حرفياً في نهاية "
                "المنشور للمشاركة في الترند الحالي."
            )
        brief += _h._strategy_brief_suffix(context)

        plan = _h._content_plan(context)
        parts = int(plan.get("parts", 1) or 1)
        capability = "publish_thread" if parts > 1 else "publish_content"
        params = {
            "brief": brief, "media_url": topic["media_url"],
            "pillar": plan.get("pillar", ""), "format": plan.get("format", ""),
        }
        if parts > 1:
            params["parts"] = parts
        return [_h.ProposedAction(
            capability=capability,
            params=params,
            rationale=f"viral content grounded in {topic['source']} topic (with media={bool(topic['media_url'])})",
            expected_impact=round(min(0.85 * damping, 0.95), 3),
            urgency=round(0.65 * damping, 3), risk=0.25,
        )]
