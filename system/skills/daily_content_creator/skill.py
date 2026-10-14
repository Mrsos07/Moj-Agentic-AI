"""
مهارة موظف مدمجة في النظام: daily_content_creator — المصدر الوحيد لكودها (system/skills/daily_content_creator/).

المساعدات المشتركة بين المهارات (فلاتر السبام والمحظورات، حجز الأهداف، الميزانيات…) في
``app.aeos.skills.builtin`` وتُستدعى عبر ``_h.`` — فأي تعديل عليها (أو استبدال في اختبار)
يصل لكل المهارات.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List

from app.aeos.skills import builtin as _h

if TYPE_CHECKING:  # pragma: no cover
    from app.aeos.cognition.context import ExecutionContext


class DailyContentCreatorSkill(_h.ISkill):
    """Proposes an original post ONLY when there is a real, grounded topic.

    Never posts generic filler: if there is no trending topic (Google Trends /
    Trend Detector) and no relevant timeline tweet, it proposes nothing. When a
    topic exists it grounds the post on it and attaches the source media.
    """

    name = "daily_content_creator"
    claim_priority = 40
    uses_capabilities = ["publish_content"]

    def propose(self, context: "ExecutionContext") -> List[_h.ProposedAction]:
        # ── توجيه مباشر من المستخدم عبر الشات («انشر عن …» / «تكلم عن …») ──
        # أمر صريح من صاحب الحساب: ينفَّذ فوراً بأسلوب الشخصية، ويتجاوز حدود
        # النشر اليومي وفحص نطاق الاهتمامات (المستخدم هو المرجع الأعلى).
        payload = context.event.payload or {}
        directed = str(payload.get("directed_topic") or "").strip()
        media_brief = str(payload.get("media_brief") or "").strip()
        if directed and media_brief:
            # المنسّق أعدّ رسالة إعلامية (بحث عميق): يكتب منها بزاويته، لا نشر أعمى
            lang = getattr(context.config.persona, "language", "ar") or "ar"
            dialect = getattr(context.config.persona, "dialect", "") or ""
            brief = (
                f"كلّفك المنسّق العام بالكتابة عن: «{directed}».\n"
                "أُرفقت لك أدناه رسالة إعلامية أعدّها فريق التحرير من بحث عميق في المصادر الحديثة. "
                f"اكتب منشور X أصلياً واحداً باللغة {lang}"
                + (f" وبلهجة {dialect}" if dialect else "")
                + " بأسلوب شخصيتك: Hook قوي، ثم معلومة أو اثنتان مقتبسة من الرسالة، ثم موقف/Insight "
                "من زاويتك. لا تلخّص الرسالة كلها ولا تنسخ عنوانها."
            )
            items = payload.get("media_brief_items") or []
            if items:
                # للمخرج البصري وسجل الدورة: الموضوع مُبحوث مسبقاً (لا بحث جديد)
                context.scratch["research"] = {"query": directed[:160], "items": items}
                context.scratch.setdefault("research_log", []).append({
                    "query": directed[:160], "sources": list(dict.fromkeys(i.get("source", "") for i in items)),
                    "items": len(items), "kind": "media_brief"})
            if directed.startswith("#"):
                brief += f" مهم: ضمّن الوسم {directed} كما هو حرفياً في المنشور."
            return [_h.ProposedAction(
                capability="publish_content",
                params={"brief": brief, "media_brief": media_brief, "media_url": "", "owner_directed": True},
                rationale="coordinator-directed topic with a researched media brief",
                expected_impact=0.97, urgency=0.95, risk=0.08,
            )]
        if directed:
            lang = getattr(context.config.persona, "language", "ar") or "ar"
            dialect = getattr(context.config.persona, "dialect", "") or ""
            dialect_part = (' وبلهجة ' + dialect) if dialect else ''
            brief = (
                "أرسل لك صاحب الحساب الطلب التالي مباشرةً — وقد يتضمّن خبراً أو "
                "عنواناً منسوخاً بالإضافة إلى كلام توجيه:\n"
                f"«{directed}»\n\n"
                "أولاً استخرج الموضوع/الخبر الجوهري من هذا الطلب (تجاهل كلمات "
                "التوجيه مثل: انشر، جهّز، ابيك، محتوى قوي). ثم ابحث فيه واكتب "
                f"منشور X أصلياً قوياً بصوت إنساني طبيعي باللغة {lang}{dialect_part} "
                "بأسلوب شخصيتك: ابدأ بـHook قوي، ثم زبدة الموضوع وموقف/Insight "
                "واضح يدفع للتفاعل. لا تنسخ العنوان حرفياً، وقدّم زاوية وتحليلاً "
                "يضيف قيمة حقيقية."
            )
            if directed.startswith("#"):
                brief += f" مهم: ضمّن الوسم {directed} كما هو حرفياً في المنشور."
            return [_h.ProposedAction(
                capability="publish_content",
                params={"brief": brief, "media_url": "", "owner_directed": True,
                        "research_query": directed[:200]},
                rationale="user-directed topic from chat (explicit owner command)",
                expected_impact=0.95, urgency=0.95, risk=0.1,
            )]

        counters = context.config.runtime_state.counters
        posts_today = counters.get("posts_today", 0)
        target = int(context.config.business_rules.get("daily_post_target", 1))
        if posts_today >= target:
            return []

        # حسابات «في مثل هذا اليوم»: النشر حصراً عبر مهارة on_this_day —
        # الترندات/الأخبار تجرّ الحساب خارج هويته التوثيقية (حدث فعلاً).
        if _h._on_this_day_mode(context):
            return []

        rules = context.config.business_rules or {}
        require_grounded_topic = bool(rules.get("require_grounded_topic", True))
        enforce_scope = bool(rules.get("enforce_interest_scope", True))

        # تفضيل التفاعل عند وجود فرص قوية = خفض أولوية النشر، لا إلغاؤه
        damping = _h._engagement_first_damping(context)

        # اختيار تحريري لا آلي: الموظف يراجع المرشّحين كرئيس تحرير ويختار
        # الأهم/الأشوق لجمهور تخصصه مع زاوية معالجة وخُطّاف — لا أول موضوع يطابق الكلمات.
        topic = _h._editorial_pick(context)
        if require_grounded_topic and not topic:
            return []  # لا موضوع حقيقي => لا ننشر محتوى عاماً
        if not topic:
            return []
        if topic.get("why"):
            context.scratch["editorial_pick"] = {
                "title": topic["title"][:120], "why": topic["why"],
                "angle": topic.get("angle", ""),
            }
            context.emit_progress(
                f"🗞️ **اخترت موضوع المنشور كرئيس تحرير:** «{topic['title'][:90]}» — "
                f"{topic['why']}"
            )

        interests = getattr(context.config.persona, "interests", []) or []
        if enforce_scope and interests:
            hay = f"{topic.get('title','')} {topic.get('summary','')}"
            min_rel = int(rules.get("min_relevance_score", 1))
            if _h._rel(context, hay, interests) < min_rel:
                return []
        focus = "، ".join(interests) if interests else (
            context.top_goal_description() or "مجال اهتمام الجمهور")
        lang = getattr(context.config.persona, "language", "ar") or "ar"
        dialect = getattr(context.config.persona, "dialect", "") or ""
        plan = _h._content_plan(context)

        brief = (
            f"اكتب منشور X أصلي بصوت إنساني طبيعي باللغة {lang}"
            f"{(' وبلهجة ' + dialect) if dialect else ''} "
            f"ضمن مجال ({focus}) حول موضوع حقيقي: '{topic['title']}'."
        )
        if topic["summary"]:
            brief += f" الخلفية المختصرة: {topic['summary']}"
        if topic.get("angle"):
            brief += f"\nزاوية المعالجة المطلوبة (التزم بها): {topic['angle']}"
        if topic.get("hook"):
            brief += (f"\nخُطّاف مقترح للسطر الأول (حسّنه إن استطعت، لا تضعفه): "
                      f"«{topic['hook']}»")
        brief += (
            "\nقواعد الصنعة: السطر الأول خُطّاف ≤ 12 كلمة (رقم/مفارقة/سؤال جريء)؛ "
            "فكرة واحدة فقط؛ موقف صريح أو معلومة ملموسة لا يعرفها أغلب الجمهور؛ "
            "خاتمة تفتح نقاشاً (رأي يُختلف عليه أو سؤال محدد)؛ لا مقدمات إنشائية "
            "ولا إعادة سرد للخبر كما هو. اكتب كصانع محتوى محترف له شخصية ورأي. "
            "إن وُجدت ميديا مرتبطة استخدمها لدعم المنشور."
        )
        # مشاركة في هاشتاق الترند: ضمّن الهاشتاق نصّاً ليظهر المنشور داخل نقاشه
        if topic.get("hashtag"):
            brief += (
                f" مهم جداً: ضمّن الهاشتاق {topic['hashtag']} كما هو حرفياً في نهاية "
                "المنشور للمشاركة في الترند الحالي."
            )
        brief += _h._strategy_brief_suffix(context)

        # الصيغة تحدّد القدرة: ثريد (parts>1) أو تغريدة مفردة
        parts = int(plan.get("parts", 1) or 1)
        capability = "publish_thread" if parts > 1 else "publish_content"
        params = {
            "brief": brief,
            "media_url": topic["media_url"],
            "pillar": plan.get("pillar", ""),
            "format": plan.get("format", ""),
            "research_query": topic["title"][:200],
        }
        if parts > 1:
            params["parts"] = parts
        return [_h.ProposedAction(
            capability=capability,
            params=params,
            rationale=(f"grounded {plan.get('format', 'post')} from {topic['source']} "
                       f"on pillar '{plan.get('pillar', '')}' "
                       f"(posts_today={posts_today}<{target})"),
            expected_impact=round(min(0.78 * damping + (0.06 if parts > 1 else 0), 0.95), 3),
            urgency=round(0.6 * damping, 3), risk=0.2,
        )]
