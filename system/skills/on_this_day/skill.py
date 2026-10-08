"""
مهارة موظف مدمجة في النظام: on_this_day — المصدر الوحيد لكودها (system/skills/on_this_day/).

المساعدات المشتركة بين المهارات (فلاتر السبام والمحظورات، حجز الأهداف، الميزانيات…) في
``app.aeos.skills.builtin`` وتُستدعى عبر ``_h.`` — فأي تعديل عليها (أو استبدال في اختبار)
يصل لكل المهارات.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List

from app.aeos.skills import builtin as _h

if TYPE_CHECKING:  # pragma: no cover
    from app.aeos.cognition.context import ExecutionContext


class OnThisDaySkill(_h.ISkill):
    """ينشر أحداثاً تاريخية وقعت «في مثل هذا اليوم» — جوهر حسابات التوثيق.

    حسابات التوثيق التاريخي لا تجد موضوعها في الترندات أو الأخبار (مصادر
    ``_grounded_topic`` كلها آنية)، فكان الحساب إما لا ينشر شيئاً أو ينجرف
    خلف الترندات خارج هويته. هذه المهارة تؤرضن المحتوى بمصدر حقيقي مخصص:
    أحداث اليوم من ويكيبيديا، مع التاريخين الميلادي والهجري، ونافذة أحداث
    تتدوّر عبر منشورات اليوم فلا يتكرر الحدث نفسه.
    """

    name = "on_this_day"
    claim_priority = 45
    uses_capabilities = ["publish_content"]

    def propose(self, context: "ExecutionContext") -> List[_h.ProposedAction]:
        if not _h._on_this_day_mode(context):
            return []
        counters = context.config.runtime_state.counters
        posts_today = counters.get("posts_today", 0)
        target = int(context.config.business_rules.get("daily_post_target", 1))
        if posts_today >= target:
            return []

        events = _h._fetch_on_this_day_events()
        if not events:
            return []  # لا مصدر حقيقي => لا نُلفّق حدثاً من ذاكرة النموذج

        # أحداث قديمة فعلاً: القائمة مرتّبة من الأحدث، فكانت النافذة تعرض أحداث 2008-2009
        # (مستوطنات، انتخابات) فيرفض الحارس الموجز كـ«سياسة معاصرة» (قيس في سجلات
        # liketoday5)، ويكتب التقييم «التزم بحدث قديم» درساً لا تقرأه المهارة أبداً.
        import datetime as _dt

        min_age = int(context.config.business_rules.get("on_this_day_min_age_years", 30))
        old = [e for e in events if e["year"] <= _dt.date.today().year - min_age]
        if len(old) >= 3:
            events = old

        # قصر الأحداث على تخصص الشخصية (حدث سياسي/ديني يتقدم على خبر رياضي
        # عابر): النافذة المتدحرجة فوق قائمة مرتّبة فقط كانت تُسقط منشورَ آخر
        # اليوم على ذيل القائمة (أعاصير/رياضة) فيرفضه الحارس لخروجه عن التخصص
        # وتضيع الدورة. ثم نافذة متدحرجة داخل المتخصص: كل منشور خلال اليوم
        # يعرض أحداثاً مختلفة، وذاكرة النشاط تمنع تكرار ما نُشر فعلاً.
        interests = getattr(context.config.persona, "interests", []) or []
        if interests:
            relevant = [e for e in events if _h.relevance_score(e["text"], interests) >= 1]
            if len(relevant) >= 5:
                events = relevant
            else:
                events = sorted(
                    events,
                    key=lambda e: (_h.relevance_score(e["text"], interests), e["year"]),
                    reverse=True,
                )
        window = 5
        start = (posts_today * window) % max(len(events), 1)
        batch = events[start:start + window] or events[:window]
        listing = "\n".join(f"- سنة {e['year']}: {e['text'][:220]}" for e in batch)

        lang = getattr(context.config.persona, "language", "ar") or "ar"
        # لا تذكر اسم المصدر في الموجز: «(المصدر: ويكيبيديا)» جعل حارس الالتزام
        # يحكم على المصدر بدل الموضوع، ورفضُه تحوّل عبر تقييم الدورة إلى توجيه
        # «تجنب ويكيبيديا» يُحقن في برومبته فيرفض كل موجز تالٍ — حلقة صدى.
        brief = (
            f"تاريخ اليوم: {_h._today_dates_arabic()}.\n"
            f"أحداث حقيقية موثقة وقعت في مثل هذا اليوم:\n"
            f"{listing}\n\n"
            f"اختر من القائمة أعلاه حصراً (لا تأتِ بحدث من خارجها) الحدث الأهم "
            f"الواقع داخل تخصصك وتجاهل ما سواه، واكتب تغريدة توثيقية واحدة "
            f"باللغة {lang} بأسلوب شخصيتك الرسمي السردي: اذكر سنة الحدث، وصُغ "
            "الحدث بدقة وحيادية كسطر تاريخ موثوق. لا أسئلة، لا نقاش، لا رأي شخصي."
        )
        return [_h.ProposedAction(
            capability="publish_content",
            params={"brief": brief, "media_url": "",
                    "pillar": "في مثل هذا اليوم", "format": "insight"},
            rationale=(f"on-this-day documentation post from {len(events)} real "
                       f"Wikipedia events (posts_today={posts_today}<{target})"),
            expected_impact=0.9, urgency=0.85, risk=0.1,
        )]
