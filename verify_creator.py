"""
verify_creator.py — تحقّق من "عقل صانع المحتوى" في الموظف الذكي.

يشغّل فحوصاً حقيقية على المنطق كاملاً **بلا أي نشر على X**: استراتيجية المحتوى
(محاور/صيغ/تدوير)، التعلّم من الأداء الفعلي، محاذاة الأهداف الحقيقية، إصلاح خطأ
«إما ينشر أو يتفاعل»، اقتراح الثريد، أدوات المبدع الجديدة، وقراءة العدّادات من
واجهة X.

    python verify_creator.py

للتجربة الحقيقية على حساب (تنشر فعلاً!) استخدم:
    python verify_creator.py --live <cookie_label> --handle <account>
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.aeos.cognition.context import ExecutionContext           # noqa: E402
from app.aeos.core.events import Event, EventType                 # noqa: E402
from app.aeos.employee import strategy                            # noqa: E402
from app.aeos.employee.models import (                            # noqa: E402
    EmployeeConfig, Goal, Persona,
)

_results = []


def check(name: str, passed: bool, detail: str = "") -> bool:
    _results.append((name, passed, detail))
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    return passed


# ─────────────────────────── fixtures ───────────────────────────
def make_config(**rules) -> EmployeeConfig:
    cfg = EmployeeConfig(
        id="verify-creator", user_id=None, platform="x",
        account_username="mazen_edu", cookie_label="mazen_edu",
        persona=Persona(
            display_name="مازن", role="صانع محتوى تقني", tone="ودّي",
            language="ar", interests=["الذكاء الاصطناعي", "أدوات التعليم"],
            donts=["الرياضة"],
        ),
        goals=[
            Goal(id="g1", description="زيادة المتابعين إلى 10 آلاف",
                 metric="followers", target=10000, priority=1),
            Goal(id="g2", description="رفع التفاعل مع المجتمع",
                 metric="engagement", priority=3),
        ],
        installed_skills=["daily_content_creator", "timeline_engager",
                          "content_quoter", "swipe_file"],
        mcp_servers=["x", "playwright_x", "content", "memory", "image"],
        business_rules={
            "daily_post_target": 2, "max_posts_per_day": 3,
            "max_actions_per_cycle": 5, "max_likes_per_cycle": 2,
            "max_replies_per_cycle": 1, "max_retweets_per_cycle": 1,
            "max_follows_per_cycle": 1, "llm_decision_review": False,
            "min_relevance_score": 1, **rules,
        },
    )
    return cfg


def make_context(cfg: EmployeeConfig, with_signals: bool = True) -> ExecutionContext:
    from app.aeos.capabilities.registry import CapabilityRegistry
    from app.aeos.capabilities.handlers import default_capabilities
    from app.aeos.memory.manager import MemoryManager
    from app.aeos.mcp.registry import MCPRegistry

    caps = CapabilityRegistry()
    for cap in default_capabilities():
        caps.register(cap)
    ctx = ExecutionContext(
        config=cfg, event=Event(EventType.SCHEDULER, employee_id=cfg.id, source="verify"),
        memory=MemoryManager(), mcp=MCPRegistry(), capabilities=caps,
        workspace=None, llm=None, cycle_id="verify",
    )
    if with_signals:
        ctx.scratch["top_posts"] = [
            {"url": "https://x.com/a/status/1", "handle": "@edu_ai",
             "text": "الذكاء الاصطناعي صار يصحّح الاختبارات تلقائياً ويوفّر ساعات "
                     "على المعلم كل أسبوع، وهذه أدوات التعليم الأكثر استخداماً فعلياً.",
             "media": "https://pbs.twimg.com/media/x?name=small"},
            {"url": "https://x.com/b/status/2", "handle": "@teach_tools",
             "text": "أدوات التعليم المدعومة بالذكاء الاصطناعي: مقارنة عملية بين "
                     "خمس منصات استخدمتها مع طلابي هذا الفصل.", "media": ""},
        ]
        ctx.scratch["timeline"] = [
            {"url": "https://x.com/c/status/3", "handle": "@ai_daily",
             "text": "نموذج جديد يفهم أدوات التعليم ويولّد خطط دروس بالذكاء الاصطناعي.",
             "media": ""},
        ]
        ctx.scratch["mentions"] = [
            {"handle": "@sara_edu", "text": "ممكن مقارنة أدوات التعليم بالذكاء الاصطناعي؟",
             "url": "https://x.com/sara_edu/status/9"},
        ]
    return ctx


# ─────────────────────────── checks ───────────────────────────
def check_strategy_rotation() -> None:
    cfg = make_config()
    plan = strategy.choose_plan(cfg)
    check("خطة المحتوى تُنتج محوراً وصيغة",
          bool(plan.get("pillar")) and bool(plan.get("format")),
          f"{plan.get('pillar')} / {plan.get('format_label')}")
    check("محاور المحتوى مشتقّة من اهتمامات الشخصية",
          strategy.content_pillars(cfg) == ["الذكاء الاصطناعي", "أدوات التعليم"],
          str(strategy.content_pillars(cfg)))

    seen_formats, seen_pillars = [], []
    for i in range(8):
        plan = strategy.choose_plan(cfg)
        seen_formats.append(plan["format"])
        seen_pillars.append(plan["pillar"])
        strategy.record_published(cfg, url=f"https://x.com/me/status/{i}",
                                  text=f"post {i}", pillar=plan["pillar"],
                                  format_id=plan["format"], parts=plan["parts"])
    check("الصيغ تتناوب عبر المنشورات (لا نغمة واحدة)",
          len(set(seen_formats)) >= 4, f"{len(set(seen_formats))} صيغة مختلفة: {seen_formats}")
    check("المحاور تتناوب أيضاً", len(set(seen_pillars)) >= 2, str(seen_pillars))

    def _max_streak(seq):
        best = run = 1
        for a, b in zip(seq, seq[1:]):
            run = run + 1 if a == b else 1
            best = max(best, run)
        return best

    # حارس ضد ارتداد خطأ حقيقي: عندما تتساوى طوابع الوقت بالثانية كان ترتيب
    # «الأحدث» ينقلب فتُطبَّق عقوبة التكرار مقلوبة ويتجمّد الحساب على محور واحد.
    check("لا يتجمّد على محور واحد (عقوبة التكرار بالاتجاه الصحيح)",
          _max_streak(seen_pillars) <= 2, f"أطول تكرار متتالٍ = {_max_streak(seen_pillars)}")
    check("لا يتجمّد على صيغة واحدة", _max_streak(seen_formats) <= 2,
          f"أطول تكرار متتالٍ = {_max_streak(seen_formats)}")
    check("الثريد ضمن الصيغ المتاحة",
          any(strategy.format_spec(f)["parts"] > 1 for f in strategy.enabled_formats(cfg)),
          str(strategy.enabled_formats(cfg)))


def check_learning_from_performance() -> None:
    """الأهم: هل يفضّل فعلاً ما نجح؟ نقيس الاتجاهين لنستبعد المصادفة."""
    def _with(thread_eng: int, insight_eng: int) -> str:
        cfg = make_config(content_formats=["thread", "insight"])
        measured = []
        for i in range(3):
            url = f"https://x.com/me/status/th{i}"
            strategy.record_published(cfg, url=url, text="ثريد", pillar="الذكاء الاصطناعي",
                                      format_id="thread", parts=4)
            measured.append({"url": url, "likes": thread_eng, "reposts": 0,
                             "replies": 0, "views": thread_eng * 10,
                             "engagement": thread_eng})
        for i in range(3):
            url = f"https://x.com/me/status/in{i}"
            strategy.record_published(cfg, url=url, text="زبدة", pillar="أدوات التعليم",
                                      format_id="insight", parts=1)
            measured.append({"url": url, "likes": insight_eng, "reposts": 0,
                             "replies": 0, "views": insight_eng * 10,
                             "engagement": insight_eng})
        strategy.update_metrics(cfg, measured)
        report = strategy.performance_report(cfg)
        return strategy.choose_plan(cfg)["format"], report

    fmt_thread_wins, report = _with(thread_eng=300, insight_eng=2)
    check("يختار الصيغة الأنجح (ثريد 300 مقابل زبدة 2)", fmt_thread_wins == "thread",
          f"اختار {fmt_thread_wins}")
    check("تقرير الأداء يرتّب الصيغ صحيحاً",
          next(iter(report["formats"])) == "thread", str(report["formats"]))
    check("تقرير الأداء يحسب عدد المنشورات المقيسة", report["samples"] == 6,
          f"samples={report['samples']}")

    # انقلاب الأداء: الأنجح يجب أن يغلب **على مدى عدة اختيارات** لا في اختيار
    # واحد. سقف التتابع (max_same_choice_streak) يفرض تنويعاً مقصوداً: بعد
    # تكرار صيغة مرتين يُجرَّب غيرها مرة، ثم يعود الأنجح. القياس على اختيار
    # واحد يخفي هذا السلوك ويصادم مطلب «لا تكرار».
    def _choices_over(n: int, thread_eng: int, insight_eng: int) -> list:
        cfg = make_config(content_formats=["thread", "insight"])
        measured = []
        for i in range(3):
            url = f"https://x.com/me/status/th{i}"
            strategy.record_published(cfg, url=url, text="ثريد",
                                      pillar="الذكاء الاصطناعي",
                                      format_id="thread", parts=4)
            measured.append({"url": url, "likes": thread_eng, "reposts": 0,
                             "replies": 0, "views": thread_eng * 10})
        strategy.update_metrics(cfg, measured)
        out = []
        for i in range(n):
            fmt = strategy.choose_plan(cfg)["format"]
            out.append(fmt)
            url = f"https://x.com/me/status/live{i}"
            strategy.record_published(cfg, url=url, text="x", pillar="أدوات التعليم",
                                      format_id=fmt, parts=1)
            eng = insight_eng if fmt == "insight" else thread_eng
            strategy.update_metrics(cfg, [{"url": url, "likes": eng, "reposts": 0,
                                           "replies": 0, "views": eng * 10}])
        return out

    picks = _choices_over(6, thread_eng=1, insight_eng=250)
    check("الأنجح يغلب حين ينقلب الأداء (زبدة 250 مقابل ثريد 1)",
          picks.count("insight") > picks.count("thread"), str(picks))
    check("ولا يتكرّر أكثر من سقف التتابع مرتين متتاليتين",
          "insight" in picks and max(
              (len(list(g)) for _, g in __import__("itertools").groupby(picks)),
              default=0) <= 2, str(picks))

    # الاستكشاف: صيغة لم تُجرَّب يجب أن تُختبر ولو كان لغيرها أداء متوسط
    cfg = make_config(content_formats=["insight", "story"], strategy_exploration=0.9)
    url = "https://x.com/me/status/only"
    strategy.record_published(cfg, url=url, text="x", pillar="الذكاء الاصطناعي",
                              format_id="insight", parts=1)
    strategy.update_metrics(cfg, [{"url": url, "engagement": 9, "likes": 9}])
    check("الاستكشاف يجرّب الصيغة غير المُختبَرة",
          strategy.choose_plan(cfg)["format"] == "story",
          "اختار " + strategy.choose_plan(cfg)["format"])

    # المشاهدات وحدها تكفي للتعلّم (حساب حقيقي: صفر إعجاب مع مئات المشاهدات)
    cfg_views = make_config(content_formats=["thread", "insight"])
    measured = []
    for i in range(2):
        url = f"https://x.com/me/status/v{i}"
        strategy.record_published(cfg_views, url=url, text="ثريد", pillar="الذكاء الاصطناعي",
                                  format_id="thread", parts=4)
        measured.append({"url": url, "likes": 0, "reposts": 0, "replies": 0,
                         "views": 5000})
    for i in range(2):
        url = f"https://x.com/me/status/w{i}"
        strategy.record_published(cfg_views, url=url, text="زبدة", pillar="أدوات التعليم",
                                  format_id="insight", parts=1)
        measured.append({"url": url, "likes": 0, "reposts": 0, "replies": 0,
                         "views": 60})
    strategy.update_metrics(cfg_views, measured)
    rep = strategy.performance_report(cfg_views)
    check("المشاهدات وحدها تعطي إشارة تعلّم (بلا أي إعجاب)",
          rep["formats"].get("thread", {}).get("avg_engagement", 0) >
          rep["formats"].get("insight", {}).get("avg_engagement", 0),
          str(rep["formats"]))
    check("يفضّل الأعلى مشاهدات حين تتساوى التفاعلات بالصفر",
          strategy.choose_plan(cfg_views)["format"] == "thread", "")

    # أفضل ساعات النشر تُستخرج فعلاً
    cfg2 = make_config()
    strategy.update_metrics(cfg2, [
        {"url": "https://x.com/me/status/h1", "engagement": 100,
         "created_at": "2026-07-01T20:00:00.000Z"},
        {"url": "https://x.com/me/status/h2", "engagement": 3,
         "created_at": "2026-07-01T04:00:00.000Z"},
    ])
    hours = strategy.performance_report(cfg2)["best_hours"]
    check("يستخرج أفضل ساعة نشر من الأداء",
          bool(hours) and hours[0]["hour"] == 20, str(hours))


def check_goal_alignment() -> None:
    cfg = make_config()
    ctx = make_context(cfg, with_signals=False)
    thread = ctx.goal_alignment("publish_thread")
    like = ctx.goal_alignment("like_post")
    check("محاذاة الأهداف تفضّل الثريد على الإعجاب لهدف «زيادة المتابعين»",
          thread > like, f"thread={thread} like={like}")

    engagement_only = make_config()
    engagement_only.goals = [Goal(id="g", description="رفع التفاعل والردود",
                                  metric="engagement", priority=1)]
    ctx2 = make_context(engagement_only, with_signals=False)
    check("وتتغيّر فعلاً بتغيّر الهدف (تفاعل ⇒ الرد أعلى من المتابعة)",
          ctx2.goal_alignment("reply_to_user") > ctx2.goal_alignment("follow_user"),
          f"reply={ctx2.goal_alignment('reply_to_user')} "
          f"follow={ctx2.goal_alignment('follow_user')}")

    no_goals = make_config()
    no_goals.goals = []
    ctx3 = make_context(no_goals, with_signals=False)
    check("بلا أهداف لا محاذاة (صفر)", ctx3.goal_alignment("publish_content") == 0.0)


def check_publish_and_engage_together() -> None:
    """الخطأ البنيوي القديم: وجود فرص تفاعل كان يُلغي النشر كلياً."""
    from app.aeos.skills.builtin import (
        DailyContentCreatorSkill, TimelineEngagerSkill, ContentQuoterSkill,
        SwipeFileSkill, _has_strong_engagement_candidates,
    )
    from app.aeos.cognition.phases import StrategyPhase
    from app.aeos.decision.engine import WeightedUtilityDecisionEngine

    cfg = make_config()
    ctx = make_context(cfg)
    StrategyPhase().run(ctx)
    check("توجد فرص تفاعل قوية في هذه الدورة",
          _has_strong_engagement_candidates(ctx), "")

    content = DailyContentCreatorSkill().propose(ctx)
    check("مهارة المحتوى لم تعد تصمت عند وجود فرص تفاعل", bool(content),
          f"{len(content)} اقتراح: {content[0].capability if content else '-'}")

    # الترتيب ليس تفصيلاً: المهارات تحجز أهدافها من حوض واحد، فالأفعال النادرة
    # (اقتباس/حفظ) يجب أن تسبق الوفيرة (إعجاب) وإلا لم تجد هدفاً أبداً. نستدعيها
    # بترتيب ``claim_priority`` نفسه الذي يستعمله المُشغّل الحقيقي.
    engagers = sorted(
        [TimelineEngagerSkill(), ContentQuoterSkill(), SwipeFileSkill()],
        key=lambda s: s.claim_priority, reverse=True,
    )
    proposals = list(content)
    for skill in engagers:
        proposals += skill.propose(ctx)
    caps = {p.capability for p in proposals}
    check("الأدوات الجديدة تُقترح فعلاً (اقتباس + حفظ مرجع)",
          "quote_post" in caps and "bookmark_post" in caps, str(sorted(caps)))

    decisions = WeightedUtilityDecisionEngine().decide(proposals, ctx)
    chosen = [d.action.capability for d in decisions]
    published = [c for c in chosen if c in ("publish_content", "publish_thread")]
    engaged = [c for c in chosen if c in ("reply_to_user", "like_post",
                                          "retweet_post", "quote_post",
                                          "follow_user", "bookmark_post")]
    check("القرار ينفّذ النشر والتفاعل في الدورة نفسها",
          bool(published) and bool(engaged), f"chosen={chosen}")
    check("لا منشورَين في دورة واحدة (تغريدة أو ثريد لا الاثنين)",
          len(published) <= 1, f"published={published}")
    check("الشرح يوضّح محاذاة الأهداف",
          any("goal_fit" in d.explanation for d in decisions),
          next((d.explanation for d in decisions), ""))


def check_thread_proposal() -> None:
    from app.aeos.skills.builtin import DailyContentCreatorSkill

    cfg = make_config(content_formats=["thread"])
    ctx = make_context(cfg)
    from app.aeos.cognition.phases import StrategyPhase
    StrategyPhase().run(ctx)
    plan = ctx.scratch.get("content_plan") or {}
    proposals = DailyContentCreatorSkill().propose(ctx)
    check("عند صيغة الثريد يُقترح publish_thread",
          bool(proposals) and proposals[0].capability == "publish_thread",
          f"{proposals[0].capability if proposals else '-'} parts={plan.get('parts')}")
    check("عدد أجزاء الثريد يُمرَّر للقدرة",
          bool(proposals) and int(proposals[0].params.get("parts", 0)) > 1,
          str(proposals[0].params.get("parts") if proposals else None))
    check("المحور والصيغة يُمرَّران للتسجيل في سجل الأداء",
          bool(proposals) and proposals[0].params.get("pillar")
          and proposals[0].params.get("format") == "thread", "")
    check("كتلة الاستراتيجية تُدرَج في البريف",
          bool(proposals) and "استراتيجية الحساب" in proposals[0].params.get("brief", ""), "")

    off = make_config(allow_threads=False)
    check("يمكن تعطيل الثريدات بقاعدة عمل",
          all(strategy.format_spec(f)["parts"] == 1 for f in strategy.enabled_formats(off)),
          str(strategy.enabled_formats(off)))


def check_weak_topic_filter() -> None:
    """حارس ارتداد: ترويسة قسم في X كانت تُصبح «موضوع» منشور فيرفضه الحارس."""
    from app.aeos.skills.builtin import _grounded_topic, _is_weak_topic

    check("يرفض ترويسة قسم X كموضوع",
          _is_weak_topic("الاقتصاد و الأعمال متداول", "خلفية طويلة " * 20))
    check("يرفض عنواناً قصيراً بلا خلفية", _is_weak_topic("النفط", "قصير"))
    check("يقبل موضوعاً حقيقياً بخلفية",
          not _is_weak_topic("تصاعد التوتر في العلاقات الأمريكية الإيرانية",
                             "تفاصيل حقيقية " * 10))

    cfg = make_config()
    ctx = make_context(cfg, with_signals=False)
    ctx.scratch["x_trends"] = [{"name": "الاقتصاد و الأعمال متداول", "category": ""}]
    ctx.scratch["trend_tweets"] = [
        {"text": "الذكاء الاصطناعي وأدوات التعليم في الاقتصاد الرقمي", "media": ""},
    ]
    check("لا يبني منشوراً على ترويسة قسم", _grounded_topic(ctx) is None,
          str(_grounded_topic(ctx)))


def check_cycle_limits_are_real() -> None:
    """السقف المُعلَن في الواجهة يجب أن يكون السقف الفعلي في محرّك القرار."""
    from app.aeos.decision.engine import WeightedUtilityDecisionEngine, cycle_limit
    from app.aeos.skills.base import ProposedAction

    def _likes(n: int):
        return [ProposedAction(
            capability="like_post",
            params={"tweet_url": f"https://x.com/u/status/{i}", "original_text": "x"},
            rationale="t", expected_impact=0.6, urgency=0.5, risk=0.05,
        ) for i in range(n)]

    cfg = make_config(max_likes_per_cycle=3, max_actions_per_cycle=6,
                      llm_decision_review=False)
    ctx = make_context(cfg, with_signals=False)
    chosen = WeightedUtilityDecisionEngine().decide(_likes(5), ctx)
    check("سقف «إعجاب/دورة = 3» يُنفّذ ثلاثة فعلاً",
          len([d for d in chosen if d.action.capability == "like_post"]) == 3,
          f"{len(chosen)} إجراء")

    cfg1 = make_config(max_likes_per_cycle=1, max_actions_per_cycle=6,
                       llm_decision_review=False)
    ctx1 = make_context(cfg1, with_signals=False)
    chosen1 = WeightedUtilityDecisionEngine().decide(_likes(5), ctx1)
    check("وسقف 1 يبقى واحداً", len(chosen1) == 1, f"{len(chosen1)} إجراء")

    cfg0 = make_config(max_likes_per_cycle=0, max_actions_per_cycle=6,
                       llm_decision_review=False, daily_likes_target=5)
    ctx0 = make_context(cfg0, with_signals=False)
    chosen0 = [d for d in WeightedUtilityDecisionEngine().decide(_likes(3), ctx0)
               if d.action.capability == "like_post"]
    check("وصفر يعطّل النوع تماماً", not chosen0, f"{len(chosen0)} إجراء")

    cfg_cap = make_config(max_likes_per_cycle=5, max_actions_per_cycle=2,
                          llm_decision_review=False)
    ctx_cap = make_context(cfg_cap, with_signals=False)
    check("«إجراءات/دورة» يبقى الحد الكلي فوق الجميع",
          len(WeightedUtilityDecisionEngine().decide(_likes(5), ctx_cap)) == 2, "")

    rules = {"max_quotes_per_cycle": 2, "max_posts_per_cycle": 1}
    check("سقف الاقتباس يُقرأ من القواعد", cycle_limit("quote_post", rules) == 2)
    check("التغريدة والثريد يتشاركان سقف النشر",
          cycle_limit("publish_thread", rules) == cycle_limit("publish_content", rules) == 1)

    # المهارات تقترح بعدد السقف لا اقتراحاً واحداً
    from app.aeos.skills.builtin import ContentQuoterSkill, SwipeFileSkill

    cfg_q = make_config(max_quotes_per_cycle=2, max_bookmarks_per_cycle=2)
    ctx_q = make_context(cfg_q)
    ctx_q.scratch["top_posts"] = ctx_q.scratch["top_posts"] + [
        {"url": f"https://x.com/z{i}/status/{i}", "handle": f"@z{i}",
         "text": "تحليل معمّق عن الذكاء الاصطناعي وأدوات التعليم مع أرقام واقعية "
                 "من تجربة فصل دراسي كامل.", "media": ""} for i in range(3)
    ]
    check("مهارة الاقتباس تقترح بعدد سقفها",
          len(ContentQuoterSkill().propose(ctx_q)) == 2,
          f"{len(ContentQuoterSkill().propose(ctx_q))} اقتراح")
    check("مهارة حفظ المراجع تقترح بعدد سقفها",
          len(SwipeFileSkill().propose(ctx_q)) == 2,
          f"{len(SwipeFileSkill().propose(ctx_q))} اقتراح")


def check_quality_gate() -> None:
    """بوابة الجودة: ترفض القوالب الفارغة وإعادة صياغة التوجيه وتكرار المطلع."""
    from app.aeos.capabilities.handlers import (
        _autofix_post_text, _hook_style, _quality_issues,
    )

    cfg = make_config()
    ctx = make_context(cfg, with_signals=False)
    ctx.scratch["recent_posts"] = ["الذكاء الاصطناعي يغيّر التعليم بشكل جذري اليوم"]

    cases = {
        "cliche": "في عالم متسارع تتغير أدوات التعليم بسرعة كبيرة ويجب أن نتابع كل جديد فيها باستمرار.",
        "meta": "إليك التغريدة التي طلبتها عن أدوات الذكاء الاصطناعي في التعليم وتأثيرها الكبير جداً.",
        "too_short": "أدوات التعليم جيدة.",
        "repeated_opening": "الذكاء الاصطناعي يغيّر قواعد اللعبة في تصحيح الاختبارات ويوفّر ساعتين أسبوعياً لكل معلم.",
    }
    for key, text in cases.items():
        issues = _quality_issues(text, ctx)
        check(f"يرفض: {key}", key in issues, str(issues))

    brief = "اكتب منشوراً عن أدوات الذكاء الاصطناعي في التعليم وأثرها على المعلمين"
    echo = "منشور عن أدوات الذكاء الاصطناعي في التعليم وأثرها على المعلمين اليوم"
    check("يرفض إعادة صياغة التوجيه نفسه",
          "echo_brief" in _quality_issues(echo, ctx, brief),
          str(_quality_issues(echo, ctx, brief)))

    good = ("ثلاث مدارس في الرياض قلّصت زمن تصحيح الاختبارات من ست ساعات إلى أربعين "
            "دقيقة بأداة تصحيح آلي واحدة. المشكلة لم تكن التقنية بل تدريب المعلم عليها.")
    check("يقبل محتوى ملموساً جيداً", not _quality_issues(good, ctx),
          str(_quality_issues(good, ctx)))

    from app.aeos.capabilities.handlers import (
        _BLOCKING_QUALITY_ISSUES, _HARD_QUALITY_ISSUES,
    )
    check("تكرار المطلع يطلب إعادة توليد لكنه لا يمنع النشر نهائياً",
          "repeated_opening" in _HARD_QUALITY_ISSUES
          and "repeated_opening" not in _BLOCKING_QUALITY_ISSUES)

    fixed = _autofix_post_text("نص جيد #أول #ثاني #ثالث ...")
    check("الإصلاح الميكانيكي يترك هاشتاقاً واحداً", fixed.count("#") == 1, fixed)
    check("ويُنهي النص بجملة تامة", not fixed.endswith("..."), fixed)

    styles = {_hook_style(make_context(make_config(), with_signals=False))}
    for i in range(1, 6):
        c = make_config()
        c.runtime_state.cycles_completed = i
        styles.add(_hook_style(make_context(c, with_signals=False)))
    check("أنماط المطالع تتناوب عبر الدورات", len(styles) >= 5,
          f"{len(styles)} نمط مختلف")


def check_media_quality() -> None:
    """الصور: رفض الشعارات العامة، وثبات النمط البصري، ومنع النص في الصورة."""
    from app.aeos.capabilities.handlers import _image_prompt, _is_weak_media

    weak = [
        "https://lh3.googleusercontent.com/abc",
        "https://ssl.gstatic.com/logo.png",
        "https://news.google.com/x.png",
        "https://cdn.site.com/assets/logo.svg",
        "https://x.com/user/status/123",
        "https://site.com/article.html",
        "https://pbs.twimg.com/profile_images/1/a.jpg",
    ]
    for url in weak:
        check(f"يرفض ميديا ضعيفة: {url.split('/')[2]}", _is_weak_media(url))
    check("يقبل وسائط منشور حقيقية",
          not _is_weak_media("https://pbs.twimg.com/media/abc?name=large"))

    cfg = make_config(image_style="cinematic", image_prompt_extra="navy palette")
    ctx = make_context(cfg, with_signals=False)
    prompt = _image_prompt("منشور عن أثر التصعيد على أسواق الطاقة", ctx)
    check("النمط البصري المختار يدخل البرومبت", "cinematic" in prompt, prompt[:80])
    check("والإضافة المخصّصة أيضاً", "navy palette" in prompt)
    check("ويُمنع النص داخل الصورة صراحةً", "NO text" in prompt)


def check_browser_slot() -> None:
    """العمليات التفاعلية لا تنتظر قفل المتصفح للأبد (سبب «انتهت المهلة»)."""
    import threading
    import time as _t

    from app.aeos.mcp.servers.playwright_x_mcp import _browser_slot
    from app.services.x_bridge import browser_lock

    with _browser_slot(1.0):
        pass
    check("يحجز المتصفح فوراً عندما يكون حراً", True)

    done = threading.Event()

    def _hold():
        with browser_lock:
            done.wait(6)

    threading.Thread(target=_hold, daemon=True).start()
    _t.sleep(0.3)
    started = _t.time()
    failed_fast, message = False, ""
    try:
        with _browser_slot(1.5):
            pass
    except RuntimeError as exc:
        failed_fast = (_t.time() - started) < 3.0
        message = str(exc)
    done.set()
    check("يفشل سريعاً عند انشغال المتصفح بدل الانتظار الأبدي", failed_fast,
          f"{_t.time() - started:.1f}s")
    check("والرسالة تشرح السبب وتقترح حلاً",
          "مشغول" in message and "الموظف" in message, message[:80])


def check_no_false_success_on_status_page() -> None:
    """حارس ارتداد: تأكيد النشر لا يجوز أن يعتمد على «/status/» في الاقتباس/الرد.

    كان الاقتباس يُبلّغ بالنجاح دون نشر فعلي: صفحة الاقتباس تبدأ على رابط
    ``/status/`` فيُطابق الملاذ الأخير في ``_confirm_submitted`` ويعيد True دائماً.
    """
    from app.x.modules.x_post import _confirm_submitted

    class _StubLocator:
        def count(self):
            return 0

        @property
        def first(self):
            return self

        def is_visible(self):
            return False

        def inner_text(self):
            return ""

        def locator(self, _sel):
            return _StubLocator()

    class _StubPage:
        """صفحة تغريدة بلا محرّر مفتوح ولا توست — أي: لم يُنشر شيء."""
        url = "https://x.com/someone/status/123"

        def locator(self, _sel):
            return _StubLocator()

        def get_by_test_id(self, _tid):
            return _StubLocator()

        def wait_for_timeout(self, _ms):
            return None

    # الحالة الواقعية للنجاح الكاذب: محرّر X لم يكن مودالاً (فتح صفحة compose)،
    # فلا إشارة «انغلاق المحرّر» ولا توست — ولم يبقَ إلا رابط /status/ المخادع.
    page = _StubPage()
    check("بلا الملاذ: لا نجاح كاذب على صفحة تغريدة",
          _confirm_submitted(page, timeout_ms=300, saw_dialog_before=False,
                             allow_status_url_fallback=False) is False)
    check("مع الملاذ: يبقى سلوك النشر العادي كما هو (توافق رجعي)",
          _confirm_submitted(page, timeout_ms=300, saw_dialog_before=False,
                             allow_status_url_fallback=True) is True)
    check("انغلاق المحرّر بعد أن كان مفتوحاً يبقى دليل نجاح مشروعاً",
          _confirm_submitted(page, timeout_ms=300, saw_dialog_before=True,
                             allow_status_url_fallback=False) is True)

    import inspect

    from app.x.modules import x_actions

    src = inspect.getsource(x_actions.quote_tweet)
    check("الاقتباس يؤكّد الإرسال ولا يفترضه",
          "_confirm_submitted" in src and "allow_status_url_fallback=False" in src)
    check("وللاقتباس فولباك لوحة المفاتيح كالنشر",
          "_submit_via_keyboard" in src)
    check("ويفشل صريحاً إن لم يتأكّد الإرسال",
          "لم يتم تأكيد نشر الاقتباس" in src)

    # الرد: محرّر المودال وزرّه لا المحرّر المضمّن خلف القناع (mask intercepts pointer events)
    src = inspect.getsource(x_actions.reply_to_tweet)
    check("الرد يكتب وينشر ويتأكّد داخل نطاق محرّر واحد",
          "_open_reply_composer" in src and "_click_reply_publish(scope)" in src
          and "scope=scope" in src and "_click_first_visible(page, publish" not in src)
    check("الرد لا يفقد النص إن فشل fill (كان Ctrl+A بلا كتابة)",
          "tb.type(reply_text" in src)
    check("نطاق الرد هو المودال الحاوي للمحرّر",
          "div[role='dialog']:has(" in inspect.getsource(x_actions._reply_scope))

    # كل إجراء يغيّر حالة على X يجب أن يتحقّق من أثره لا أن يفترضه
    for fn, marker in (
        (x_actions.like_tweet, "unlike"),
        (x_actions.repost_tweet, "unretweet"),
        (x_actions.bookmark_tweet, "removeBookmark"),
        (x_actions.follow_user, "unfollow"),
        (x_actions.reply_to_tweet, "_reply_submitted"),
    ):
        body = inspect.getsource(fn)
        check(f"{fn.__name__} يتحقّق من نجاحه فعلياً",
              marker in body and "raise" in body, marker)


def check_x_error_screen_recovery() -> None:
    """شاشة X «Something went wrong… Try again» (failedScript=vendor عبر البروكسي) تُشفى بالضغط.

    قيس 2026-10-26: دورة كاملة بلا فعل لأن كل قراءات X انتظرت 45 ث شاشة لا تتغير أبداً."""
    from app.aeos.mcp.servers import playwright_x_mcp as PX
    mod = sys.modules[type(PX.PlaywrightXMCP()).__module__]

    class _Loc:
        def __init__(self, page, kind):
            self.page, self.kind = page, kind

        @property
        def first(self):
            return self

        def wait_for(self, state="visible", timeout=0):
            if not self.page.content_ok:
                raise TimeoutError("Locator.wait_for: Timeout")

        def count(self):
            return 1 if (self.kind == "btn" and not self.page.content_ok) else 0

        def is_visible(self):
            return self.count() > 0

        def click(self, timeout=0):
            self.page.clicks += 1
            if self.page.clicks >= self.page.heal_after:
                self.page.content_ok = True

    class _Page:
        url = "https://x.com/home?failedScript=vendor"

        def __init__(self, heal_after):
            self.content_ok, self.clicks, self.heal_after = False, 0, heal_after

        def locator(self, sel):
            return _Loc(self, "cards")

        def get_by_role(self, role, name=None):
            return _Loc(self, "btn")

    page = _Page(heal_after=2)
    mod._wait_cards(page, 'article[data-testid="tweet"]', 20_000)
    check("شاشة خطأ X تُشفى بـ«Try again» (ضغطتان) بدل انتظار المهلة", page.content_ok and page.clicks == 2)
    page = _Page(heal_after=99)
    try:
        mod._wait_cards(page, 'article[data-testid="tweet"]', 1_500, retries=3)
        ok = False
    except TimeoutError as exc:
        ok = "Something went wrong" in str(exc) and page.clicks == 3
    check("وإن لم تُشفَ: فشل واضح بسببه بعد 3 محاولات (لا ضغط بلا حد)", ok)


def check_reply_modal_race() -> None:
    """مودال الرد: القناع يغطي الصفحة فوراً والمودال ارتفاعه 0 حتى يُحمَّل محتواه (قيس 2026-10-28
    عبر البروكسي). كان النطاق يُعاد «الصفحة» فيُنقر المحرّر المضمّن خلف القناع ← Timeout 30 ث."""
    from app.x.modules import x_actions as XA

    class _L:
        def __init__(self, page, sel, inside=None):
            self.page, self.sel, self.inside = page, sel, inside

        first = property(lambda self: self)
        last = property(lambda self: self)

        def locator(self, sel):
            return _L(self.page, sel, inside=self.sel)

        def _present(self):
            st = self.page.st
            if self.sel == "#layers [data-testid='mask']":
                return st["mask"]
            if self.sel.startswith("div[role='dialog']"):
                return st["dialog_tb"]
            if self.sel == XA._REPLY_TB:
                return st["dialog_tb"] if self.inside else (st["dialog_tb"] or st["inline_tb"])
            return False

        def count(self):
            return 1 if self._present() else 0

        def is_visible(self):
            if self.sel.startswith("div[role='dialog']"):
                return False                     # ارتفاع 0 أثناء التحميل
            return self._present()

    class _P:
        def __init__(self, **st):
            self.st = st

        def locator(self, sel):
            return _L(self, sel)

    p = _P(mask=True, dialog_tb=True, inline_tb=True)
    scope = XA._reply_scope(p)
    check("مودال ارتفاعه 0 لكن محرّره موجود ← النطاق هو المودال لا الصفحة",
          scope is not p and getattr(scope, "sel", "").startswith("div[role='dialog']"))
    p = _P(mask=True, dialog_tb=False, inline_tb=True)
    check("قناع مودال بلا محرّر بعد ← لا يُختار المحرّر المضمّن خلف القناع",
          XA._reply_scope(p) is p and XA._modal_layer_open(p))
    p = _P(mask=False, dialog_tb=False, inline_tb=True)
    check("بلا مودال ← المحرّر المضمّن تحت التغريدة (سلوك صحيح)",
          XA._reply_scope(p) is p and not XA._modal_layer_open(p))


def check_reply_fits_x_limit() -> None:
    """الرد لا يتجاوز حد X: رد من 3 نقاط تجاوز 280 فعطّل X زر «ردّ» (قيس 2026-10-28، العدّاد ‎-13)."""
    from app.aeos.capabilities import handlers as H
    from app.aeos.capabilities.base import CapabilityRequest
    from app.aeos.mcp.base import MCPResponse
    from app.x.modules import x_actions as XA
    from app.x.modules.x_text import fit, weighted_len

    failed = ("1) الزيارة هنا ليست بروتوكولاً فقط؛ هي رسالة جاهزية على الحد الجنوبي، حيث تُقاس الوقائع لا "
              "الشعارات.\n2) هذا هو الفارق بين دولةٍ تُثبت حضورها بالميدان، وبين ميليشيا تعيش على التضليل "
              "والاختباء.\n3) طبعاً، من يفهم الرسائل العسكرية يدرك لماذا يُستفز خصوم الأمن كلما ظهرت "
              "القيادة بين المرابطين.")
    check("عدّ X: العربية حرف، الإيموجي حرفان، الرابط 23",
          weighted_len("مرحبا") == 5 and weighted_len("👍") == 2
          and weighted_len("https://example.com/a/very/long/path") == 23)
    short = fit(failed, 270)
    check("القصّ عند نهاية جملة تامة (لا نص مبتور) وضمن الحد",
          weighted_len(short) <= 270 and short.endswith(".") and "3)" not in short, short[-40:])

    class _MCP:
        def __init__(self):
            self.sent = []

        def call(self, req):
            if req.tool == "generate":
                return MCPResponse.ok(text=failed)
            self.sent.append(dict(req.params))
            return MCPResponse.ok()

    ctx = make_context(make_config(unslop=False), with_signals=False)
    ctx.mcp = _MCP()
    orig = H._record_interaction
    H._record_interaction = lambda *a, **k: None
    try:
        res = H.ReplyToUserCapability().execute(CapabilityRequest(
            name="reply_to_user", params={"tweet_url": "https://x.com/a/status/1",
                                          "original_text": "زيارة رئيس الأركان للحد الجنوبي"}), ctx)
    finally:
        H._record_interaction = orig
    sent = (ctx.mcp.sent or [{}])[0].get("reply_text", "")
    check("رد مولَّد أطول من الحد ← يُرسَل مقصوصاً ضمن 270", res.success and 0 < weighted_len(sent) <= 270,
          f"{weighted_len(sent)}")
    try:
        XA.reply_to_tweet("x.json", "https://x.com/a/status/1", failed + " " + failed, headless=True)
        msg = ""
    except ValueError as e:
        msg = str(e)
    check("طبقة النشر تفشل فوراً بسبب واضح إن وصلها نص فوق الحد (لا انتظار دقيقتين)",
          "أطول من حد X" in msg, msg)


def check_handle_change_followed() -> None:
    """المالك غيّر يوزر الحساب على X (nxjrjrjdj ← nxjrjrjdj2): الهوية والأداء يُقرآن من اليوزر الحالي."""
    import tempfile

    from app.aeos.mcp.servers import playwright_x_mcp as PX
    from app.aeos.services import identity_store as IS

    mod = sys.modules[type(PX.PlaywrightXMCP()).__module__]

    class _Link:
        first = property(lambda self: self)

        def wait_for(self, state="attached", timeout=0):
            pass

        def get_attribute(self, name):
            return "/nxjrjrjdj2"

    class _Page:
        def locator(self, sel):
            return _Link()

    check("اليوزر الحالي يُقرأ من رابط «الملف الشخصي» في قائمة X", mod._own_handle(_Page()) == "nxjrjrjdj2")
    orig = IS._dir
    tmp = tempfile.mkdtemp()
    IS._dir = lambda eid: __import__("pathlib").Path(tmp) / eid
    try:
        snap = IS.save_x_profile("e1", "nxjrjrjdj", {"name": "صقر", "handle": "nxjrjrjdj2"}, "live")
    finally:
        IS._dir = orig
    check("البطاقة تحفظ اليوزر الحالي وتسجّل القديم", snap["handle"] == "nxjrjrjdj2"
          and snap.get("handle_changed_from") == "nxjrjrjdj", str(snap))


def check_mcp_usage_plan() -> None:
    """خطة استخدام خادم MCP: لا وسائط مخترعة ولا أدوات كتابة، والردود تُختصر لسطر مقروء."""
    import json

    from app.aeos.employee import tool_use as T

    c = {"slug": "trendsmcp", "tools": [
        {"name": "trends___get_top_trends", "inputSchema": {"properties": {"type": {}, "country": {}},
                                                            "required": ["type"]}},
        {"name": "trends___get_growth", "inputSchema": {"properties": {"keyword": {}, "source": {}},
                                                        "required": ["keyword", "source"]}},
        {"name": "delete_all", "inputSchema": {"properties": {}}}]}
    plan = T._valid_plan(c, {"tools": [
        {"tool": "trends___get_top_trends", "phase": "observe", "args": {"type": "TikTok", "hack": 1}},
        {"tool": "trends___get_growth", "phase": "observe", "args": {}},
        {"tool": "trends___get_growth", "phase": "research", "topic_arg": "keyword", "args": {"source": "x"}},
        {"tool": "delete_all", "phase": "observe", "args": {}}]})
    check("الخطة تُسقط الوسائط المخترعة", plan and plan[0]["args"] == {"type": "TikTok"}, str(plan))
    check("الخطة تُسقط أداة ينقصها وسيط مطلوب وأدوات الكتابة",
          [(p["tool"], p["phase"]) for p in plan] == [("trends___get_top_trends", "observe"),
                                                      ("trends___get_growth", "research")], str(plan))
    check("خطة فاسدة من النموذج ← لا شيء", T._valid_plan(c, "garbage") == [])
    wrap = lambda b: json.dumps({"statusCode": 200, "body": json.dumps(b)})  # noqa: E731
    check("لوحة فارغة ← لا إشارة", T._compact(wrap({"count": 0, "data": []})) == "")
    check("لوحة ترند ← سطر مرتّب", T._compact(wrap({"data": [[1, "ترند"], [2, "الرياض"]]})) == "1) ترند، 2) الرياض")
    cut = wrap({"sources": [{"source": "YouTube", "items": [{"title": "تطورات الحوثي اليوم"},
                                                            {"title": "عنوان يُقطع في منتصفه"}]}]})[:-30]
    check("رد مقطوع ← العناوين", T._compact(cut) == "تطورات الحوثي اليوم", T._compact(cut))


def check_specialty_trends_drive_cycle() -> None:
    """ترندات التخصص (أدوات MCP): بحث X يبحث بها، وتصير مرشّح منشور حتى بلا تطابق كلمات."""
    from app.aeos.capabilities.base import CapabilityRequest
    from app.aeos.capabilities.handlers import BrowseSpecialtyNewsCapability
    from app.aeos.mcp.base import MCPResponse
    from app.aeos.skills.builtin import _grounded_candidates

    cfg = make_config()
    cfg.persona.interests = ["politics"]
    ctx = make_context(cfg, with_signals=False)
    seen = {}

    class _MCP:
        def call(self, req):
            seen.update(req.params)
            return MCPResponse.ok(tweets=[{"text": "اعتراض مسيّرة حوثية جديدة اليوم", "url": "https://x.com/a/status/1"}])

    ctx.mcp = _MCP()
    ctx.scratch["specialty_trends"] = [{"topic": "اليمن والحوثيون", "x_query": "مسيّرة حوثية", "why": "تصعيد",
                                        "headlines": ["اعتراض وتدمير طائرة مسيرة أطلقها الحوثيون باتجاه مكة المكرمة"]}]
    ctx.scratch["trend_x_queries"] = ["مسيّرة حوثية"]
    BrowseSpecialtyNewsCapability().execute(CapabilityRequest(name="browse_specialty_news", params={}), ctx)
    check("بحث X يبحث بموضوع ترند التخصص أولاً", '"مسيّرة حوثية"' in seen.get("query", ""), seen.get("query"))
    check("تغريدات موضوع الترند تمر فلتر الصلة", len(ctx.scratch.get("specialty_news") or []) == 1)
    cands = _grounded_candidates(ctx)
    st = next((c for c in cands if c["source"] == "specialty_trend"), None)
    check("موضوع ترند التخصص مرشّح منشور مع تغريدات X عنه",
          st is not None and "اعتراض مسيّرة حوثية" in st["summary"], str(st)[:200])


def check_stock_media() -> None:
    """مهارة stock_media: بحث Pixabay بخطة النموذج، اختيار مطابق أو لا شيء، وبلا تكرار للملف."""
    import re
    import tempfile
    from pathlib import Path

    from app.aeos.employee import stock_media as SM
    from app.aeos.mcp.base import MCPResponse

    cfg = make_config()
    ctx = make_context(cfg, with_signals=False)
    calls = []

    class _MCP:
        def has(self, name):
            return name == "pixabay"

        def call(self, req):
            calls.append((req.tool, dict(req.params)))
            if req.tool == "search_videos":
                return MCPResponse.ok(results=[{"id": 9, "kind": "video", "url": "https://cdn.pixabay.com/v.mp4",
                                                "tags": "missile, night", "duration": 300, "size": 1}])
            return MCPResponse.ok(results=[
                {"id": 1, "kind": "image", "url": "https://pixabay.com/get/a.jpg", "tags": "beach, sunset", "width": 1920},
                {"id": 2, "kind": "image", "url": "https://pixabay.com/get/b.jpg", "tags": "missile, air defense, night", "width": 1920},
                {"id": 3, "kind": "image", "url": "https://pixabay.com/get/c.jpg", "tags": "small", "width": 300}])

    class _LLM:
        available = True

        def complete_json(self, system, prompt, temperature=0.2):
            if "queries" in system:
                return {"queries": ["air defense missile night", "صاروخ"], "kind": "video", "avoid": ["flags"]}
            return {"choice": 2 if "missile, air defense" in prompt.split("2.")[1][:60] else 1}

    ctx.mcp, ctx.llm = _MCP(), _LLM()
    orig_avail, orig_path = SM.available, SM._USED_PATH
    SM.available = lambda c: True
    SM._USED_PATH = Path(tempfile.mkdtemp()) / "used.json"
    try:
        url = SM.find(ctx, "اعتراض صاروخ باليستي فوق الرياض الليلة")
        check("فيديو أطول من 60ث يُستبعد ← رجوع لصورة", any(t == "search_images" for t, _ in calls))
        check("الصورة المطابقة للموضوع تُختار (لا الأولى ولا الصغيرة)", url.endswith("b.jpg"), url)
        check("كلمات البحث إنجليزية فقط (الحروف غير اللاتينية تُحذف)",
              all(re.fullmatch(r"[A-Za-z0-9 \-]*", p.get("q", "")) for _, p in calls), str(calls)[:200])
        url2 = SM.find(ctx, "اعتراض صاروخ باليستي فوق الرياض الليلة")
        check("الملف نفسه لا يتكرر للموظف", url2 != url, url2)
        cfg.business_rules["stock_media"] = "off"
        SM.available = orig_avail
        check("stock_media=off ← لا بحث", SM.enabled(cfg) is False)
    finally:
        SM.available, SM._USED_PATH = orig_avail, orig_path
        cfg.business_rules.pop("stock_media", None)


def check_emotion_comes_from_graph_memory() -> None:
    """الحالة العاطفية يجب أن تُبنى على حقائق الذاكرة المعرفية لا على التعليمات."""
    from app.aeos.cognition.phases import EmotionPhase

    FACTS = [
        "@sara_edu قالت إن المنشور رائع وطلبت تفاصيل عملية أكثر عن أدوات التصحيح.",
        "@critic99 وصف الطرح بأنه سطحي ولا يناسب المعلمين فعلياً.",
        "فشل إجراء reply_to_user بسبب انتهاء صلاحية الكوكيز.",
    ]

    class _Graph:
        available = True
        seen_queries = []

        def search_facts(self, employee_id, queries, limit=12, **kw):
            _Graph.seen_queries = list(queries)
            return list(FACTS)

    class _LLM:
        available = True
        last_prompt = ""
        last_system = ""
        reply = {}

        def complete_json(self, system="", prompt="", **kw):
            _LLM.last_system, _LLM.last_prompt = system, prompt
            return dict(_LLM.reply)

    cfg = make_config()
    cfg.persona.instructions = ("التزم دائماً بمجال الذكاء الاصطناعي وأدوات التعليم "
                                "ولا تخرج عنه في أي منشور أو تفاعل")
    cfg.persona.dos = ["اكتب بأسلوب ودّي ومختصر"]
    cfg.persona.donts = ["لا تتحدث في الرياضة إطلاقاً"]
    ctx = make_context(cfg)
    ctx.llm = _LLM()

    _LLM.reply = {
        "mood": "متيقّظ ومتعاطف",
        "tone_adjustment": "ابدأ بالخطوة العملية مباشرة",
        "behavior_rules": [
            "الجمهور طلب تفاصيل عملية ⇒ ابدأ بالخطوة لا بالمقدمة",
            "التزم دائماً بمجال الذكاء الاصطناعي وأدوات التعليم ولا تخرج عنه",
        ],
        "avoid": ["الطرح السطحي الذي انتُقد", "لا تتحدث في الرياضة إطلاقاً"],
        "based_on": [1, 2, 99],
        "evidence": "طلب سارة للتفاصيل ونقد @critic99 للسطحية",
    }
    phase = EmotionPhase(_Graph())
    phase.run(ctx)
    state = ctx.scratch.get("emotion_state") or {}

    check("الحالة العاطفية أُنتجت من حقائق الرسم", bool(state.get("mood")),
          str(state.get("mood")))
    check("التعليمات الثابتة لا تدخل برومبت الحالة إطلاقاً",
          cfg.persona.instructions not in _LLM.last_prompt
          and "لا تتحدث في الرياضة" not in _LLM.last_prompt, "")
    check("الحقائق تدخل البرومبت مرقّمة",
          "[1]" in _LLM.last_prompt and FACTS[0][:30] in _LLM.last_prompt)
    check("يُطلب إسناد الحالة إلى أرقام الحقائق",
          "based_on" in _LLM.last_system)
    check("قاعدة سلوك مكرّرة من التعليمات تُرفَض",
          all("ولا تخرج عنه" not in r for r in state.get("behavior_rules", [])),
          str(state.get("behavior_rules")))
    check("وقاعدة مستنتَجة من حقيقة تُقبَل",
          any("ابدأ بالخطوة" in r for r in state.get("behavior_rules", [])),
          str(state.get("behavior_rules")))
    check("«تجنّب» المنسوخة من المحظورات تُرفَض",
          all("الرياضة" not in a for a in state.get("avoid", [])),
          str(state.get("avoid")))
    check("الإسناد يُنظَّف من الأرقام غير الموجودة", state.get("based_on") == [1, 2],
          str(state.get("based_on")))
    check("الحقائق المُستنَد إليها تُحفظ مع الحالة",
          len(state.get("facts_used") or []) == 2, str(state.get("facts_used")))
    check("سبب الحالة يظهر في تعليمات التوليد",
          "سببها من تجربتك" in ctx.persona_prompt(), "")
    check("الاستعلام يسأل الرسم عن إشارات الجمهور",
          any("إشارات الجمهور" in q for q in _Graph.seen_queries),
          str(_Graph.seen_queries))

    # لا حقائق ⇒ لا حالة عاطفية إطلاقاً (لا يجوز اختلاقها من التعليمات)
    class _EmptyGraph:
        available = True

        def search_facts(self, *a, **k):
            return []

    ctx2 = make_context(make_config())
    ctx2.llm = _LLM()
    EmotionPhase(_EmptyGraph()).run(ctx2)
    check("بلا حقائق لا توجد حالة عاطفية", ctx2.scratch.get("emotion_state") is None)

    # حالة كلها صدى للتعليمات ⇒ تُسقَط بالكامل
    _LLM.reply = {
        "mood": "", "tone_adjustment": "",
        "behavior_rules": ["التزم دائماً بمجال الذكاء الاصطناعي وأدوات التعليم ولا تخرج عنه"],
        "avoid": ["لا تتحدث في الرياضة إطلاقاً"], "based_on": [], "evidence": "",
    }
    ctx3 = make_context(cfg)
    ctx3.llm = _LLM()
    EmotionPhase(_Graph()).run(ctx3)
    check("حالة كلها إعادة صياغة للتعليمات تُسقَط",
          ctx3.scratch.get("emotion_state") is None,
          str(ctx3.scratch.get("emotion_state")))


def check_fact_dedup_and_diversity() -> None:
    """حقائق الرسم: إسقاط إعادة الصياغة، ومنع مصدرٍ واحد من احتلال النتائج."""
    from app.aeos.memory.graphiti_store import _drop_near_duplicates

    same_event = [
        "عماد المقرن تلقى إشارة جمهور من @ml17ll_ قال فيها: فزعتكم لها.",
        "@ml17ll_ أرسل إشارة جمهور إلى عماد المقرن وقال: «فزعتكم لها».",
    ]
    out = _drop_near_duplicates(same_event)
    check("صياغتان لنفس الحقيقة تُدمجان", len(out) == 1, f"{len(out)} بعد الدمج")

    distinct = [
        "@sara_edu طلبت تفاصيل عملية عن أدوات التصحيح الآلي.",
        "فشل إجراء reply_to_user بسبب انتهاء صلاحية الكوكيز.",
        "الثريدات التحليلية حقّقت تفاعلاً أعلى من التغريدة المفردة.",
    ]
    check("الحقائق المختلفة لا تُدمج خطأً",
          len(_drop_near_duplicates(distinct)) == 3,
          f"{len(_drop_near_duplicates(distinct))}/3")

    # مصدر واحد مكرّر بصياغات متباعدة لا يجوز أن يحتلّ كل النتائج
    dominating = [
        f"@one_guy أرسل إشارة جمهور رقم {i} إلى الحساب عن موضوع مختلف تماماً "
        f"يتضمن كلمات فريدة {'ألف باء' * i}"
        for i in range(1, 7)
    ] + ["@other_guy علّق بأن الطرح ممتاز ومفيد جداً للمتخصصين في المجال."]
    kept = _drop_near_duplicates(dominating)
    from_one = [k for k in kept if "@one_guy" in k]
    check("سقف الحقائق لكل مصدر يُحترم", len(from_one) <= 3,
          f"{len(from_one)} حقيقة من مصدر واحد")
    check("ويبقى مكان لمصادر أخرى",
          any("@other_guy" in k for k in kept), str(len(kept)))

    # التصريف العربي (ال/بال) كان يُخفي التكرار: «منصة» ≠ «بالمنصة»
    bookkeeping = [
        "عماد المقرن يستخدم منصة x في الدورة 150.",
        "عماد المقرن مرتبط بالمنصة X في دورة رقم 144.",
    ]
    check("تطبيع سوابق التعريف يمسك التكرار المُصرَّف",
          len(_drop_near_duplicates(bookkeeping)) == 1,
          f"{len(_drop_near_duplicates(bookkeeping))} بعد الدمج")

    # عند التكرار يجب أن تبقى الحقيقة الأغنى معلومةً لا الأسبق ترتيباً
    poor_first = [
        "عماد يعمل على منصة X في دورات متعددة.",
        "عماد يعمل على منصة X في دورات متعددة، وأتم إجراءات ناجحة تشمل الردود "
        "وإعادة النشر، مع تفاعل محدود من الجمهور على حساباته.",
    ]
    merged = _drop_near_duplicates(poor_first)
    check("يُحتفظ بالحقيقة الأغنى عند التكرار",
          len(merged) == 1 and "تفاعل محدود" in merged[0],
          f"{len(merged)} → {merged[0][:60] if merged else '-'}")


def check_kuzu_access_is_serialized() -> None:
    """حارس ارتداد: كل وصول إلى Kuzu مُسلسل، وإلا انهارت العملية كلها.

    Kuzu امتداد أصلي: تزامن قراءة مع كتابة يُنتج Access Violation
    (0xC0000005) يقتل الباك-إند بلا استثناء بايثون. حدث 15 مرة في يوم واحد.
    """
    import inspect

    from app.aeos.memory import graphiti_store as gs

    for fn_name in ("search_facts", "recent_facts", "stats", "ingest"):
        src = inspect.getsource(getattr(gs.GraphitiStore, fn_name))
        check(f"{fn_name} يُسلسل وصوله إلى Kuzu", "self._lock()" in src)

    driver_src = inspect.getsource(gs.GraphitiStore._open_driver)
    check("تواز Graphiti الداخلي مُقيَّد بواحد",
          "max_concurrent_queries=1" in driver_src)

    ingest_src = inspect.getsource(gs.GraphitiStore.ingest)
    check("الكتابة تُعلَّم لكشف الموت المفاجئ",
          "_mark_write_start" in ingest_src and "_mark_write_end" in ingest_src)

    init_src = inspect.getsource(gs.GraphitiStore._initialize)
    check("البدء يفحص علامة الموت وسط الكتابة",
          "_recover_from_hard_crash" in init_src)

    recover_src = inspect.getsource(gs.GraphitiStore._recover_from_hard_crash)
    check("والقاعدة نصف المكتوبة تُحجر لا تُستخدم",
          "_quarantine_db" in recover_src)


def check_capabilities_registered() -> None:
    from app.aeos.capabilities.handlers import default_capabilities
    from app.aeos.mcp.servers.x_mcp import XPlatformMCP
    from app.aeos.mcp.servers.playwright_x_mcp import PlaywrightXMCP

    names = {c.name for c in default_capabilities()}
    expected = {"publish_thread", "quote_post", "schedule_post", "bookmark_post",
                "unfollow_user", "delete_own_post", "read_profile",
                "read_own_metrics", "generate_image"}
    missing = expected - names
    check("كل قدرات صانع المحتوى مُسجّلة", not missing, f"مفقودة: {missing or 'لا شيء'}")

    x_tools = set(XPlatformMCP().tools)
    check("أدوات X الجديدة موصولة (ثريد/اقتباس/جدولة)",
          {"post_thread", "quote", "schedule_post"} <= x_tools, "")
    check("قراءة أداء المنشورات موصولة",
          "read_my_posts" in set(PlaywrightXMCP().tools), "")


def check_metrics_parser() -> None:
    from app.aeos.mcp.servers.playwright_x_mcp import _parse_metrics, _to_int

    check("تحويل الأرقام: 1.2K / ٣٤ / 12 ألف / 1,203",
          (_to_int("1.2K"), _to_int("٣٤"), _to_int("12 ألف"), _to_int("1,203"))
          == (1200, 34, 12000, 1203),
          str((_to_int("1.2K"), _to_int("٣٤"), _to_int("12 ألف"), _to_int("1,203"))))

    en = _parse_metrics({"group_label": "3 replies, 5 reposts, 42 likes, 1,203 views"})
    check("قراءة العدّادات من واجهة إنجليزية",
          (en["replies"], en["reposts"], en["likes"], en["views"]) == (3, 5, 42, 1203),
          str(en))

    ar = _parse_metrics({"group_label": "٣ ردود، ٥ إعادات نشر، ٤٢ إعجاب، ١٢٠٠ مشاهدة"})
    check("قراءة العدّادات من واجهة عربية",
          ar["likes"] == 42 and ar["views"] == 1200 and ar["replies"] == 3, str(ar))

    per = _parse_metrics({"per_button": {"like": "17 likes", "reply": "2 replies"}})
    check("فولباك aria-label لكل زر", per["likes"] == 17 and per["replies"] == 2, str(per))


def check_ledger_and_graph_episode() -> None:
    from app.aeos.cognition.phases import GraphMemoryPhase

    cfg = make_config()
    ctx = make_context(cfg)
    strategy.record_published(cfg, url="https://x.com/me/status/50", text="منشور",
                              pillar="الذكاء الاصطناعي", format_id="thread", parts=4)
    stats = strategy.update_metrics(cfg, [
        {"url": "https://x.com/me/status/50", "likes": 30, "reposts": 4,
         "replies": 2, "views": 900, "engagement": 44},
    ])
    check("سجل الأداء يُحدَّث بأرقام حقيقية", stats["updated"] == 1, str(stats))
    expected = strategy.engagement_score(
        {"likes": 30, "reposts": 4, "replies": 2, "views": 900})
    top = strategy.top_performers(cfg, 1)
    check("أفضل المنشورات تُستخرج بدرجة أداء محسوبة",
          bool(top) and top[0]["engagement"] == expected,
          f"{top} (المتوقع {expected})")
    check("سجل الأداء يُخزَّن في أعلام الحالة (يُحفظ مع الموظف)",
          "post_performance" in cfg.runtime_state.flags, "")

    # الحلقة المعرفية يجب أن تحمل الأداء والخطة (تعلّم لا مجرد سجل)
    captured = {}

    class _Store:
        available = True

        def ingest(self, employee_id, payload, name="", source_description=""):
            captured["payload"] = payload
            return None

    ctx.scratch["content_plan"] = strategy.choose_plan(cfg)
    ctx.scratch["performance_report"] = strategy.performance_report(cfg)
    ctx.scratch["execution_results"] = [
        {"capability": "publish_thread", "success": True,
         "output": {"text": "رأس الثريد"}},
    ]
    ctx.scratch["evaluation"] = {"actions": 1, "successes": 1, "failures": 0}
    ctx.scratch["lesson"] = "الثريد أفضل من التغريدة المفردة في الشرح"
    GraphMemoryPhase(_Store()).run(ctx)
    payload = captured.get("payload") or {}
    check("الذاكرة المعرفية تتغذّى بخطة المحتوى وأدائه",
          "خطة_المحتوى" in payload and "أداء_محتواي" in payload,
          str(list(payload.keys())))


def check_image_policy() -> None:
    from app.aeos.capabilities.handlers import _maybe_generate_image
    from app.aeos.mcp.base import MCPResponse

    class _ImgMCP:
        def has(self, name):
            return name == "image"

        def call(self, request):
            return MCPResponse.ok(url="https://example.com/generated.png")

    cfg = make_config()
    ctx = make_context(cfg, with_signals=False)
    ctx.mcp = _ImgMCP()
    check("auto: لا تُولّد صورة إن وُجدت ميديا حقيقية",
          _maybe_generate_image(ctx, "نص", "https://pbs.twimg.com/media/x") == "")
    check("auto: تُولّد صورة عند غياب الميديا",
          _maybe_generate_image(ctx, "نص", "") == "https://example.com/generated.png")
    check("العدّاد اليومي للصور يزيد",
          cfg.runtime_state.counters.get("generated_images_today") == 1,
          str(cfg.runtime_state.counters))

    cfg.runtime_state.counters["generated_images_today"] = 3
    check("الحد اليومي للصور يُحترم (تكلفة مضبوطة)",
          _maybe_generate_image(ctx, "نص", "") == "")

    off = make_config(generate_images="off")
    ctx_off = make_context(off, with_signals=False)
    ctx_off.mcp = _ImgMCP()
    check("يمكن تعطيل توليد الصور", _maybe_generate_image(ctx_off, "نص", "") == "")


def check_delete_guard() -> None:
    from app.aeos.capabilities.base import CapabilityRequest
    from app.aeos.capabilities.handlers import DeleteOwnPostCapability

    cfg = make_config()
    ctx = make_context(cfg, with_signals=False)
    res = DeleteOwnPostCapability().execute(
        CapabilityRequest(name="delete_own_post",
                          params={"tweet_url": "https://x.com/someone_else/status/77"}),
        ctx,
    )
    check("يرفض حذف منشور لا يملكه الحساب", not res.success, str(res.error)[:80])


# ─────────────────────────── live (real X) ───────────────────────────
def run_live(cookie_label: str, handle: str) -> None:
    """تجربة حقيقية: ثريد فعلي + قراءة أداء فعلية على حساب محدّد."""
    from app.aeos.mcp.base import MCPRequest
    from app.aeos.mcp.servers.playwright_x_mcp import PlaywrightXMCP
    from app.aeos.mcp.servers.x_mcp import XPlatformMCP

    print(f"\n=== تجربة حقيقية على @{handle} (label={cookie_label}) ===")
    px = PlaywrightXMCP()
    res = px.call(MCPRequest(employee_id="live", tool="read_my_posts",
                             params={"handle": handle, "limit": 8},
                             context={"cookie_label": cookie_label}))
    if not check("قراءة أداء منشورات الحساب من X", res.success, str(res.error)[:160]):
        return
    posts = res.data.get("posts", [])
    check("وصلت أرقام أداء حقيقية", bool(posts), f"{len(posts)} منشوراً")
    for p in posts[:5]:
        print(f"      · إعجاب {p['likes']} | إعادة {p['reposts']} | رد {p['replies']} "
              f"| مشاهدات {p['views']} → {p['text'][:70]}")
    check("العدّادات ليست كلها أصفاراً (الاستخراج يعمل)",
          any(p["likes"] or p["views"] or p["replies"] for p in posts), "")

    parts = [
        "اختبار قدرة الثريد في الموظف الذكي: هذه أول تغريدة من سلسلة قصيرة.",
        "التغريدة الثانية تأتي من نفس جلسة المتصفح عبر زر إضافة منشور، فلا ينكسر الثريد.",
        "والثالثة خلاصة: الثريد يُنشر كوحدة واحدة بضغطة واحدة.",
    ]
    xm = XPlatformMCP()
    res = xm.call(MCPRequest(employee_id="live", tool="post_thread",
                             params={"texts": parts},
                             context={"cookie_label": cookie_label}))
    check("نشر ثريد حقيقي على X", res.success,
          str(res.data.get("tweet_url") if res.success else res.error)[:200])


def check_execution_plan_consistency() -> None:
    from copy import deepcopy
    from types import SimpleNamespace
    from unittest.mock import patch

    from app.aeos.capabilities.base import CapabilityResult
    from app.aeos.cognition.phases import DecidePhase, ExecutePhase, PersonaGuardPhase
    from app.aeos.decision.engine import Decision
    from app.aeos.skills.base import ProposedAction

    def decision(cap, suffix):
        return Decision(ProposedAction(
            capability=cap, params={"tweet_url": f"https://x.com/edu/status/{suffix}",
                                    "handle": "@edu", "original_text": "أدوات التعليم"},
            rationale="فرصة مفيدة للجمهور"), score=0.8, explanation="اختيار الاختبار")

    def fixture(chosen, alternates=(), fail=False):
        ctx = make_context(make_config(), with_signals=False)
        events, calls = [], []
        ctx.progress_cb = lambda text, intent: events.append((intent, text))
        ctx.scratch["decision_alternates"] = list(alternates)

        def execute(request, context):
            calls.append(deepcopy(request))
            if fail:
                return CapabilityResult.fail("تعذّر تأكيد التنفيذ", request.name)
            return CapabilityResult.ok(request.name)

        ctx.capabilities = SimpleNamespace(execute=execute)
        DecidePhase(SimpleNamespace(decide=lambda proposals, context: chosen)).run(ctx)
        return ctx, events, calls

    reply, repost, like = (decision("reply_to_user", "1"),
                           decision("retweet_post", "2"), decision("like_post", "3"))
    ctx, events, calls = fixture([reply, repost], [like])
    check("لا إعلان نهائي قبل مراجعة الحارس",
          not any(intent == "aeos_cycle_deciding" for intent, _ in events))

    def reject_public(context, decisions, indices, reasons):
        rejected = {i for i in indices if decisions[i].action.capability != "like_post"}
        reasons.update({i: "خارج مجال الشخصية" for i in rejected})
        return rejected

    with patch.object(PersonaGuardPhase, "_deterministic_screen", side_effect=reject_public):
        PersonaGuardPhase().run(ctx)
    ExecutePhase().run(ctx)
    announcements = [text for intent, text in events if intent == "aeos_cycle_deciding"]
    check("الخطة المعلنة هي البديل الذي نُفّذ لا القرار المرفوض",
          len(announcements) == 1 and "إعجاب" in announcements[0]
          and "الرد على تغريدة" not in announcements[0]
          and [c.name for c in calls] == ["like_post"])
    check("سبب تعديل الاختيار ظاهر للمستخدم",
          any("خارج مجال الشخصية" in text for _, text in events))
    plan = ctx.scratch.get("execution_plan") or []
    results = ctx.scratch.get("execution_results") or []
    check("كل نتيجة مرتبطة بإجراء معتمد وهدفه",
          len(plan) == len(results) == 1
          and bool(plan[0].get("action_id"))
          and plan[0]["action_id"] == results[0].get("action_id")
          and results[0].get("target") == like.action.params["tweet_url"])
    check("حفظ القرار الأولي مستقلاً عن الخطة المعتمدة",
          [d["capability"] for d in ctx.scratch.get("initial_decisions", [])]
          == ["reply_to_user", "retweet_post"])

    ctx, events, calls = fixture([reply])
    with patch.object(PersonaGuardPhase, "_deterministic_screen", side_effect=reject_public):
        PersonaGuardPhase().run(ctx)
    ExecutePhase().run(ctx)
    check("الرفض الكامل يعلن عدم التنفيذ ولا يَعِد برد",
          not calls and any("لا يوجد إجراء" in text for _, text in events)
          and not any("الرد على تغريدة" in text for intent, text in events
                      if intent == "aeos_cycle_deciding"))

    ctx, events, calls = fixture([reply], fail=True)
    ExecutePhase().run(ctx)
    check("فشل الأداة لا يُحسب نجاحاً ولا يولّد إجراء بديلاً",
          len(calls) == 1 and not ctx.scratch["execution_results"][0]["success"]
          and not ctx.config.runtime_state.counters.get("replies_today")
          and any("تعذّر تأكيد التنفيذ" in text for _, text in events))

    ctx, events, calls = fixture([reply])
    ctx.scratch["phase_errors"] = ["guard"]
    ExecutePhase().run(ctx)
    check("تعطل المراجعة يمنع تنفيذ خطة غير معتمدة", not calls)

    ctx, events, calls = fixture([decision("like_post", "4"), decision("like_post", "5")])
    ctx.config.business_rules["daily_likes_target"] = 1
    ExecutePhase().run(ctx)
    check("إعادة فحص الميزانية بين الأدوات تمنع تجاوز الحد اليومي",
          len(calls) == 1 and ctx.config.runtime_state.counters.get("likes_today") == 1
          and len(ctx.scratch["execution_results"]) == 2
          and ctx.scratch["execution_results"][1].get("status") == "skipped")

    from app.aeos.cognition import critique
    from app.aeos.cognition.phases import EvaluatePhase, GraphMemoryPhase, SupportPhase

    EvaluatePhase().run(ctx)
    SupportPhase().run(ctx)
    findings = critique.deterministic_findings(ctx)
    graph_actions = GraphMemoryPhase._actions(ctx)
    check("عدم التنفيذ بسبب الحصة ليس فشلاً للأداة في التقييم أو الذاكرة",
          ctx.scratch["evaluation"]["failures"] == 0
          and ctx.scratch["evaluation"]["skipped"] == 1
          and findings["failures"] == 0 and findings["skipped"] == 1
          and graph_actions[1]["النتيجة"] == "لم يُنفّذ"
          and "سبب_عدم_التنفيذ" in graph_actions[1]
          and not ctx.scratch.get("help_request"))

    ctx, events, calls = fixture([reply, repost])

    def mutate_request(request, context):
        calls.append(deepcopy(request))
        request.params["tweet_url"] = "changed-by-handler"
        context.scratch["decisions"].clear()
        return CapabilityResult.ok(request.name)

    ctx.capabilities.execute = mutate_request
    ExecutePhase().run(ctx)
    check("التنفيذ يستخدم نسخة مستقلة ولا يغيّر القرار المحفوظ",
          [c.name for c in calls] == ["reply_to_user", "retweet_post"]
          and ctx.scratch["execution_plan"][0]["params"]["tweet_url"] == reply.action.params["tweet_url"]
          and ctx.scratch["initial_decisions"][0]["params"]["tweet_url"] == reply.action.params["tweet_url"]
          and "active_action" not in ctx.scratch)

    from app.aeos.cognition.cycle import CognitiveCycle

    ctx, events, calls = fixture([reply])

    def broken_guard(context):
        raise RuntimeError("guard test failure")

    report = CognitiveCycle([SimpleNamespace(name="guard", run=broken_guard), ExecutePhase()]).run(ctx)
    check("تقرير الدورة يحفظ القرار والخطة والنتيجة حتى عند تعطل الحارس",
          not calls and report["phase_errors"] == ["guard"]
          and bool(report.get("initial_decisions")) and bool(report.get("execution_plan"))
          and report["results"][0].get("status") == "skipped")


def check_decision_review_consistency() -> None:
    from types import SimpleNamespace

    from app.aeos.decision.engine import WeightedUtilityDecisionEngine
    from app.aeos.skills.base import ProposedAction

    ctx = make_context(make_config(llm_decision_review=True, max_actions_per_cycle=2,
                                   pace_weight=0, repeat_penalty=0), with_signals=False)
    proposals = [ProposedAction(capability="like_post", expected_impact=0.8,
                                params={"tweet_url": f"https://x.com/a/status/{i}"})
                 for i in (1, 2)]
    ctx.llm = SimpleNamespace(available=True, complete_json=lambda **kwargs: {"selected": [0, 0]})
    engine = WeightedUtilityDecisionEngine()
    decisions = engine.decide(proposals, ctx)
    check("مراجعة النموذج لا تكرر تنفيذ المرشح نفسه",
          len({d.action.params["tweet_url"] for d in decisions}) == len(decisions))

    def unavailable(**kwargs):
        raise RuntimeError("model unavailable")

    ctx.llm.complete_json = unavailable
    try:
        decisions = engine.decide(proposals, ctx)
        survived = bool(decisions)
    except RuntimeError:
        survived = False
    check("تعطل مراجعة النموذج يحافظ على الاختيار الحتمي", survived)

    ctx.llm = None
    ctx.config.runtime_state.flags["action_type_history"] = [["like_post"]] * 3
    proposals.append(ProposedAction(capability="retweet_post", expected_impact=0.9,
                                    params={"tweet_url": "https://x.com/b/status/3"}))
    engine.decide(proposals, ctx)
    check("بدائل الحارس لا تعيد نوعاً استُبعد بسبب التكرار المتتالي",
          all(d.action.capability != "like_post"
              for d in ctx.scratch.get("decision_alternates", [])))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", default="", help="cookie_label للنشر الحقيقي")
    ap.add_argument("--handle", default="", help="يوزر الحساب (بلا @)")
    args = ap.parse_args()

    print("=== 1) استراتيجية المحتوى: محاور وصيغ وتدوير ===")
    check_strategy_rotation()
    print("\n=== 2) التعلّم من الأداء الفعلي ===")
    check_learning_from_performance()
    print("\n=== 3) محاذاة الأهداف الحقيقية ===")
    check_goal_alignment()
    print("\n=== 4) النشر والتفاعل معاً (إصلاح الخطأ البنيوي) ===")
    check_publish_and_engage_together()
    print("\n=== 5) الثريد كصيغة أولى ===")
    check_thread_proposal()
    print("\n=== 5b) ترشيح المواضيع الضعيفة ===")
    check_weak_topic_filter()
    print("\n=== 5c) سقوف الدورة حقيقية لا وهمية ===")
    check_cycle_limits_are_real()
    print("\n=== 5d) بوابة جودة المحتوى ===")
    check_quality_gate()
    print("\n=== 5e) جودة الصور والنمط البصري ===")
    check_media_quality()
    print("\n=== 5f) حجز المتصفح للعمليات التفاعلية ===")
    check_browser_slot()
    print("\n=== 5g) لا نجاح كاذب في الاقتباس ===")
    check_no_false_success_on_status_page()
    print("\n=== 5g2) شاشة خطأ X تُشفى تلقائياً ===")
    check_x_error_screen_recovery()
    print("\n=== 5g3) سباق مودال الرد (القناع قبل المودال) ===")
    check_reply_modal_race()
    print("\n=== 5g4) الرد ضمن حد X ===")
    check_reply_fits_x_limit()
    print("\n=== 5g5) تغيّر يوزر الحساب على X ===")
    check_handle_change_followed()
    print("\n=== 5g6) خطة استخدام خوادم MCP ===")
    check_mcp_usage_plan()
    check_specialty_trends_drive_cycle()
    print("\n=== 5g7) ميديا Pixabay للمنشور (stock_media) ===")
    check_stock_media()
    print("\n=== 5h) الحالة العاطفية من الذاكرة المعرفية لا من التعليمات ===")
    check_emotion_comes_from_graph_memory()
    print("\n=== 5i) تنقية حقائق الرسم المعرفي ===")
    check_fact_dedup_and_diversity()
    print("\n=== 5j) تسلسل الوصول إلى Kuzu (منع انهيار العملية) ===")
    check_kuzu_access_is_serialized()
    print("\n=== 6) أدوات المبدع مُسجّلة وموصولة ===")
    check_capabilities_registered()
    print("\n=== 7) قراءة عدّادات X (عربي/إنجليزي) ===")
    check_metrics_parser()
    print("\n=== 8) سجل الأداء والذاكرة المعرفية ===")
    check_ledger_and_graph_episode()
    print("\n=== 9) سياسة المحتوى البصري ===")
    check_image_policy()
    print("\n=== 10) حماية الحذف ===")
    check_delete_guard()
    print("\n=== 11) اتساق القرار المعتمد والتنفيذ ===")
    check_execution_plan_consistency()
    check_decision_review_consistency()

    if args.live:
        run_live(args.live, args.handle or args.live)

    failed = [n for n, ok, _ in _results if not ok]
    print(f"\n=== النتيجة: {len(_results) - len(failed)}/{len(_results)} نجحت ===")
    for n in failed:
        print("  FAIL:", n)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
