"""
verify_critique.py — تحقّق من حارس التقييم والتحسين الذاتي.

يفحص أن الحارس: يكتشف الخلل الحقيقي بالأرقام، يُصدر درجة معقولة، يخزّن التقييم
والتاريخ، يشدّد نبرته على المشكلة المتكرّرة، ويُحقن توجيهاته فعلياً في تعليمات
الدورة التالية — كل ذلك **بلا نموذج لغوي** (المسار الحتمي) ومعه.

    python verify_critique.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.aeos.cognition import critique as C                     # noqa: E402
from app.aeos.employee import strategy                           # noqa: E402
from verify_creator import make_config, make_context             # noqa: E402

_results = []


def check(name: str, passed: bool, detail: str = "") -> bool:
    _results.append((name, passed, detail))
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    return passed


def _bad_cycle_ctx():
    """دورة سيئة صراحةً: رفض حارس عالٍ، فشل، تكرار، هاشتاقات، تجاهل منشنات."""
    cfg = make_config()
    ctx = make_context(cfg, with_signals=False)
    ctx.scratch["recent_posts"] = [
        "التوتر الأمريكي الإيراني وأثره على أسعار النفط وأمن الخليج والطاقة",
    ]
    ctx.scratch["mentions"] = [
        {"handle": "@a", "text": "سؤال مهم", "url": "https://x.com/a/status/1"},
        {"handle": "@b", "text": "استفسار", "url": "https://x.com/b/status/2"},
    ]
    ctx.scratch["content_plan"] = {"pillar": "politics", "format": "thread", "parts": 4}
    ctx.scratch["execution_results"] = [
        {"capability": "publish_content", "success": True,
         "output": {"text": "التوتر الأمريكي الإيراني وأثره على أسعار النفط وأمن "
                            "الخليج والطاقة #نفط #سياسة #الخليج..."}},
        {"capability": "like_post", "success": False, "error": "timeout"},
        {"capability": "retweet_post", "success": False, "error": "not found"},
    ]
    ctx.scratch["guard_report"] = {
        "reviewed": 4, "rejected": ["x", "y", "z"],
        "reasons": {"0": "خارج نطاق التخصص", "1": "يخالف المحظورات"},
    }
    ctx.scratch["evaluation"] = {"actions": 3, "successes": 1, "failures": 2}
    ctx.scratch["lesson"] = "دورة متوسطة"
    return cfg, ctx


def _good_cycle_ctx():
    cfg = make_config()
    ctx = make_context(cfg, with_signals=False)
    ctx.scratch["recent_posts"] = ["موضوع مختلف تماماً عن التعليم الرقمي والمناهج"]
    ctx.scratch["mentions"] = [{"handle": "@a", "text": "سؤال",
                               "url": "https://x.com/a/status/1"}]
    ctx.scratch["content_plan"] = {"pillar": "politics", "format": "thread", "parts": 4}
    ctx.scratch["graph_facts"] = ["حقيقة من الذاكرة"]
    ctx.scratch["emotion_state"] = {"mood": "متوازن"}
    ctx.scratch["execution_results"] = [
        {"capability": "publish_thread", "success": True,
         "output": {"texts": [
             "خطّاف قوي يفتح موضوعاً محدّداً عن أثر التصعيد على أسواق الطاقة بأرقام واضحة.",
             "الفكرة الثانية تبني على الأولى وتضيف معلومة ملموسة عن مضيق هرمز والإمدادات.",
             "الخلاصة العملية وسؤال يفتح نقاشاً حقيقياً مع الجمهور المتخصص. #طاقة"]}},
        {"capability": "reply_to_user", "success": True,
         "output": {"reply_text": "إضافة محدّدة على طرحك مع رقم مرجعي."}},
    ]
    ctx.scratch["evaluation"] = {"actions": 2, "successes": 2, "failures": 0}
    ctx.scratch["guard_report"] = {"reviewed": 2, "rejected": [], "reasons": {}}
    ctx.scratch["lesson"] = "دورة جيدة"
    return cfg, ctx


def check_findings_detect_problems() -> None:
    cfg, ctx = _bad_cycle_ctx()
    f = C.deterministic_findings(ctx)
    keys = {i["key"] for i in f["issues"]}
    print("      المشكلات المكتشفة:", sorted(keys))
    for key, label in [
        ("guard_rejections", "رفض الحارس المرتفع"),
        ("execution_failures", "فشل التنفيذ"),
        ("too_many_hashtags", "هاشتاقات زائدة"),
        ("topic_repetition", "تكرار الموضوع"),
        ("ignored_mentions", "تجاهل المنشنات"),
        ("plan_ignored", "عدم اتّباع خطة الصيغة"),
        ("truncated_post", "منشور مقطوع"),
    ]:
        check(f"يكتشف: {label}", key in keys)
    check("يحسب نسبة رفض الحارس", f["guard_rejection_rate"] == 0.75,
          str(f["guard_rejection_rate"]))
    check("يحسب نسبة الفشل", f["failure_rate"] == round(2 / 3, 2), str(f["failure_rate"]))
    check("يقيس تشابه الموضوع مع السابق", f["topic_repetition"] >= 0.5,
          str(f["topic_repetition"]))
    check("لكل مشكلة توجيه تصحيحي",
          all(i.get("directive") for i in f["issues"]), "")


def check_no_false_alarms() -> None:
    cfg, ctx = _good_cycle_ctx()
    f = C.deterministic_findings(ctx)
    keys = {i["key"] for i in f["issues"]}
    print("      المشكلات على دورة جيدة:", sorted(keys) or "لا شيء")
    check("لا يخترع مشكلات في دورة جيدة", not keys, str(sorted(keys)))
    check("يرى أن الخطة نُفّذت (ثريد فعلي)", f["plan_followed"])
    check("يرى استخدام الذاكرة المعرفية", f["graph_facts_used"] == 1)


def check_scores() -> None:
    _, bad_ctx = _bad_cycle_ctx()
    _, good_ctx = _good_cycle_ctx()
    bad = C.build_critique(bad_ctx)
    good = C.build_critique(good_ctx)
    print(f"      درجة السيئة {bad['score']} | درجة الجيدة {good['score']}")
    check("الدورة الجيدة تأخذ درجة أعلى من السيئة", good["score"] > bad["score"],
          f"{good['score']} > {bad['score']}")
    check("درجة الدورة السيئة منخفضة فعلاً", bad["score"] <= 5.0, str(bad["score"]))
    check("درجة الدورة الجيدة مرتفعة", good["score"] >= 7.0, str(good["score"]))
    check("التقييم يعمل بلا نموذج لغوي (مسار حتمي)", bad["source"] == "rules",
          bad["source"])
    check("التقييم السيئ يحمل توجيهات عملية", len(bad["directives"]) >= 3,
          f"{len(bad['directives'])} توجيهات")

    idle_cfg = make_config()
    idle_ctx = make_context(idle_cfg, with_signals=False)
    idle_ctx.scratch["execution_results"] = [
        {"capability": "wait", "success": True, "output": {"waited": True}}]
    idle = C.build_critique(idle_ctx)
    check("دورة الانتظار تُعاقب", idle["score"] <= 5.5, str(idle["score"]))
    check("ويُطلب منها التنفيذ لا الانتظار",
          any("الانتظار" in d for d in idle["directives"]), str(idle["directives"]))


def check_storage_and_feed_forward() -> None:
    cfg, ctx = _bad_cycle_ctx()
    result = C.build_critique(ctx)
    C.store_critique(cfg, result)

    flags = cfg.runtime_state.flags
    check("التقييم يُخزَّن في حالة الموظف", "cycle_critique" in flags)
    check("تاريخ التقييمات يُبنى", len(flags.get("critique_history") or []) == 1,
          str(flags.get("critique_history")))
    check("عدّاد المشكلات يبدأ", bool(flags.get("recurring_issues")),
          str(flags.get("recurring_issues")))

    block = C.directives_block(cfg)
    check("تُبنى كتلة توجيهات غير فارغة", bool(block.strip()))
    check("الكتلة تذكر الدرجة", str(result["score"]) in block)
    check("الكتلة تحترم تعليمات المستخدم صراحةً",
          "دون مخالفة تعليمات المستخدم" in block)

    prompt = ctx.persona_prompt()
    check("التوجيهات تُحقن فعلياً في persona_prompt",
          "توجيهات التحسين" in prompt and result["directives"][0][:40] in prompt)

    # المشكلة المتكرّرة تُشدَّد بعد ثلاث مرات، وتتلاشى إن عُولجت
    for _ in range(2):
        C.store_critique(cfg, C.build_critique(ctx))
    check("المشكلة تصير متكرّرة بعد 3 دورات",
          "مشكلات تكرّرت منك" in C.directives_block(cfg),
          str(cfg.runtime_state.flags.get("recurring_issues")))

    clean_ctx = make_context(cfg, with_signals=False)
    clean_ctx.scratch.update({
        "execution_results": [{"capability": "publish_content", "success": True,
                               "output": {"text": "منشور نظيف بمضمون حقيقي ومحدّد "
                                                  "وطويل بما يكفي ليكون ذا قيمة فعلية."}}],
        "guard_report": {"reviewed": 1, "rejected": [], "reasons": {}},
        "graph_facts": ["x"], "recent_posts": [],
        "content_plan": {"pillar": "politics", "format": "insight", "parts": 1},
    })
    before = dict(cfg.runtime_state.flags.get("recurring_issues") or {})
    C.store_critique(cfg, C.build_critique(clean_ctx))
    after = dict(cfg.runtime_state.flags.get("recurring_issues") or {})
    check("المشكلة المُعالَجة تتلاشى تدريجياً",
          after.get("guard_rejections", 0) < before.get("guard_rejections", 99),
          f"{before} → {after}")

    report = C.critique_report(cfg)
    check("تقرير التقييم يحسب المتوسط والاتجاه",
          report["avg_score"] is not None and report["score_trend"] in
          ("up", "down", "flat", "unknown"),
          f"avg={report['avg_score']} trend={report['score_trend']}")
    check("التقرير يعرض التوجيهات السارية",
          bool(report["directives_in_effect"].strip()))


def check_engagement_trend() -> None:
    cfg = make_config()
    measured = []
    for i in range(5):                      # الأقدم: أداء عالٍ
        url = f"https://x.com/me/status/old{i}"
        strategy.record_published(cfg, url=url, text="t", pillar="p", format_id="insight")
        measured.append({"url": url, "likes": 100, "views": 0})
    for i in range(5):                      # الأحدث: أداء منخفض
        url = f"https://x.com/me/status/new{i}"
        strategy.record_published(cfg, url=url, text="t", pillar="p", format_id="insight")
        measured.append({"url": url, "likes": 5, "views": 0})
    strategy.update_metrics(cfg, measured)
    trend = C._engagement_trend(cfg)
    check("يكتشف هبوط التفاعل عبر الدورات", trend["direction"] == "down", str(trend))

    ctx = make_context(cfg, with_signals=False)
    ctx.scratch["execution_results"] = [
        {"capability": "publish_content", "success": True,
         "output": {"text": "منشور طويل بما يكفي وفيه مضمون حقيقي ومحدّد للجمهور."}}]
    ctx.scratch["guard_report"] = {"reviewed": 1, "rejected": [], "reasons": {}}
    ctx.scratch["graph_facts"] = ["x"]
    keys = {i["key"] for i in C.deterministic_findings(ctx)["issues"]}
    check("الهبوط يولّد توجيه تغيير الصيغة/المحور", "engagement_down" in keys,
          str(sorted(keys)))


def check_focus_closes_the_loop() -> None:
    """الحلقة مغلقة: صيغة يرشّحها الحارس تؤثّر فعلاً في خطة الدورة القادمة."""
    from app.aeos.cognition.phases import StrategyPhase
    from app.aeos.employee.strategy import CONTENT_FORMATS

    cfg = make_config(content_formats=["insight", "story", "thread"])
    # نصّ حرّ (كما أعاده النموذج فعلياً) يجب أن يُرفض ولا يُخزَّن كصيغة
    C.store_critique(cfg, {"cycle": 1, "score": 3.0, "directives": [],
                           "avoid": [], "issue_keys": [],
                           "focus": {"pillar": "x", "format": "تغريدات رسمية متوسطة"}})
    check("لا يُقبل نصّ حرّ كمعرّف صيغة", C.suggested_format(cfg) == "",
          repr(C.suggested_format(cfg)))

    C.store_critique(cfg, {"cycle": 2, "score": 3.0, "directives": [],
                           "avoid": [], "issue_keys": [],
                           "focus": {"pillar": "x", "format": "story"}})
    check("يُقبل المعرّف الصالح", C.suggested_format(cfg) == "story")

    plan = strategy.choose_plan(cfg, prefer_format="story")
    check("ترشيح الحارس يوجّه اختيار الصيغة", plan["format"] == "story",
          f"اختار {plan['format']}")
    check("السبب يوضّح أن الحارس رشّحها", "حارس التقييم" in plan["why"], plan["why"])

    ctx = make_context(cfg, with_signals=False)
    StrategyPhase().run(ctx)
    check("StrategyPhase تقرأ ترشيح الحارس تلقائياً",
          (ctx.scratch.get("content_plan") or {}).get("format") == "story",
          str((ctx.scratch.get("content_plan") or {}).get("format")))

    # لكن الأداء الضعيف جداً يجب أن يتفوّق على الترشيح (الترشيح ليس أمراً مطلقاً)
    # التسجيل متداخل عن قصد: تسجيل أربع «زبدات» متتالية يجعل سقف التتابع
    # (max_same_choice_streak) يستبعدها هذه المرة، فنقيس التنويع لا الأداء.
    cfg2 = make_config(content_formats=["insight", "story"])
    measured = []
    for i in range(4):
        s_url, i_url = f"https://x.com/me/status/s{i}", f"https://x.com/me/status/i{i}"
        strategy.record_published(cfg2, url=s_url, text="t", pillar="p", format_id="story")
        measured.append({"url": s_url, "likes": 0, "views": 0})
        strategy.record_published(cfg2, url=i_url, text="t", pillar="p", format_id="insight")
        measured.append({"url": i_url, "likes": 800, "views": 40000})
    strategy.update_metrics(cfg2, measured)
    plan2 = strategy.choose_plan(cfg2, prefer_format="story")
    check("الأداء المُثبت يتفوّق على ترشيح الحارس", plan2["format"] == "insight",
          f"اختار {plan2['format']}")
    check("كل معرّفات الصيغ معروضة للنموذج",
          set(C._valid_format_ids()) == set(CONTENT_FORMATS))


def check_llm_path() -> None:
    """المسار مع نموذج لغوي: نقد نصّي حقيقي فوق الأرقام."""
    from app.aeos.core.llm import build_llm_provider

    llm = build_llm_provider()
    if not llm.available:
        print("      (لا مفتاح OPENROUTER_API_KEY — تخطّي فحص مسار LLM)")
        return
    cfg, ctx = _bad_cycle_ctx()
    rule_directives = [i["directive"]
                       for i in C.deterministic_findings(ctx)["issues"]]
    ctx.llm = llm
    result = C.build_critique(ctx)
    print("      الحكم:", str(result.get("verdict"))[:160])
    for d in result["directives"][:6]:
        print("       •", d[:130])
    check("مسار LLM يعمل ويدمج القواعد", result["source"] == "llm+rules", result["source"])
    check("النموذج يُصدر حكماً نصياً", bool(str(result.get("verdict") or "").strip()))
    kept = [d for d in rule_directives[:5] if d in result["directives"]]
    check("التوجيهات الحتمية محفوظة كلها (النموذج لا يُلغيها)",
          len(kept) == len(rule_directives[:5]),
          f"{len(kept)}/{len(rule_directives[:5])} محفوظة")
    check("الدرجة داخل المدى المسموح", 0.0 <= result["score"] <= 10.0, str(result["score"]))


def main() -> int:
    print("=== 1) الفحوص الحتمية تكتشف الخلل الحقيقي ===")
    check_findings_detect_problems()
    print("\n=== 2) لا إنذارات كاذبة على دورة جيدة ===")
    check_no_false_alarms()
    print("\n=== 3) الدرجات تميّز الجيد من السيئ ===")
    check_scores()
    print("\n=== 4) التخزين والتغذية الأمامية للدورة القادمة ===")
    check_storage_and_feed_forward()
    print("\n=== 5) اتجاه التفاعل عبر الدورات ===")
    check_engagement_trend()
    print("\n=== 6) إغلاق الحلقة: ترشيح الحارس يوجّه الاستراتيجية ===")
    check_focus_closes_the_loop()
    print("\n=== 7) مسار النقد بالنموذج اللغوي ===")
    check_llm_path()

    failed = [n for n, ok, _ in _results if not ok]
    print(f"\n=== النتيجة: {len(_results) - len(failed)}/{len(_results)} نجحت ===")
    for n in failed:
        print("  FAIL:", n)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
