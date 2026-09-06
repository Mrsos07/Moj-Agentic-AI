"""
verify_custom_skill.py — تحقّق من «اصنع مهارة لموظفك من برومبت».

يفحص، بلا شبكة وبلا نموذج لغوي وبلا أي نشر:

1. التوليد الحتمي: برومبت عربي حرّ ⇒ مواصفة صالحة (مصدر + أفعال + توجيه).
2. الأمان: كل ما هو خارج القوائم البيضاء يُحذف، والأرقام تُقصّ إلى مداها.
3. التشغيل: المهارة تقترح إجراءات حقيقية على أهداف مطابقة لشروطها فقط.
4. الحدود: تحترم الفلاتر، والمحظورات، والسبام، وما سبق التفاعل معه.
5. الانضباط: لا تتجاوز الحارس ولا الميزانيات (لا ``owner_directed`` أبداً).
6. المؤقّتات: كل N دورة، ساعات التشغيل، والحد اليومي.
7. التخزين: الحفظ/الإيقاف/الحذف، وبقاؤها بعد حفظ البرسونا من الواجهة.

    .\\venv\\Scripts\\python.exe verify_custom_skill.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.aeos.skills import custom as CS                          # noqa: E402
from verify_creator import make_config, make_context              # noqa: E402

_results = []


def check(name: str, passed: bool, detail: str = "") -> bool:
    _results.append((name, passed, detail))
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    return passed


# ─────────────────── 1) التوليد من برومبت (بلا LLM) ───────────────────
def check_generation_without_llm() -> None:
    cfg = make_config()
    spec = CS.generate_spec(
        "روح للتايم لاين واقتبس أقوى تغريدة عن الذكاء الاصطناعي وردّ عليها",
        cfg, llm=None)
    types = [a["type"] for a in spec["actions"]]
    check("يولّد مهارة صالحة بلا نموذج لغوي (احتياطي حتمي)",
          bool(spec.get("id")) and bool(types), str(types))
    check("يقرأ المصدر من الطلب (تايم لاين)",
          spec["target"]["source"] == "timeline", spec["target"]["source"])
    check("يقرأ الأفعال العربية بسوابقها (اقتبس/ردّ)",
          {"quote", "reply"} <= set(types), str(types))

    spec2 = CS.generate_spec("ردّ على كل من يخاطبني في المنشنات", cfg, llm=None)
    check("مصدر المنشنات يُلتقط", spec2["target"]["source"] == "mentions",
          spec2["target"]["source"])

    spec3 = CS.generate_spec("انشر ثريد أسبوعي عن أدوات التعليم", cfg, llm=None)
    t3 = [a["type"] for a in spec3["actions"]]
    check("«ثريد» يُلغي «منشور» المفرد (لا ازدواج)",
          "thread" in t3 and "post" not in t3, str(t3))
    check("مهارة النشر تحمل توجيه كتابة", bool(spec3["content"]["brief"]))


def check_generation_from_llm_payload() -> None:
    """مخرجات نموذج لغوي (مُحاكاة) تمرّ بنفس التطبيع."""
    class FakeLLM:
        available = True

        def complete_json(self, **kw):
            return {
                "label": "محلّل الترند",
                "description": "يقتبس أقوى تغريدة في الترند بتعليق",
                "trigger": {"every_cycles": 3, "hours": [9, 21], "max_per_day": 2},
                "target": {"source": "trend", "include": ["ذكاء اصطناعي"],
                           "exclude": ["رياضة"], "min_chars": 80},
                "actions": [{"type": "quote", "count": 1,
                             "directive": "أضف زاوية تحليلية"}],
                "priority": {"impact": 0.8, "urgency": 0.7, "risk": 0.3},
            }

    cfg = make_config()
    spec = CS.generate_spec("اقتبس أقوى تغريدة في الترند", cfg, FakeLLM())
    check("مخرجات النموذج تُقبل وتُطبَّع",
          spec["target"]["source"] == "trend"
          and spec["trigger"]["every_cycles"] == 3
          and spec["actions"][0]["type"] == "quote",
          f"{spec['label']} / {spec['trigger']}")
    check("البرومبت الأصلي يُحفظ مع المهارة", bool(spec["prompt"]))
    check("الوصف العربي يُبنى للمالك", "متى تعمل" in CS.describe_spec(spec))


# ─────────────────── 2) الأمان: القوائم البيضاء ───────────────────
def check_whitelist_hardening() -> None:
    spec = CS.normalize_spec({
        "label": "خبيثة",
        "target": {"source": "os.system", "include": ["x"]},
        "actions": [
            {"type": "delete_all", "count": 999},
            {"type": "exec_python", "count": 1},
            {"type": "like", "count": 999},
        ],
        "content": {"format": "__import__"},
        "trigger": {"every_cycles": -5, "hours": [99, "x", 7], "max_per_day": 9999},
        "priority": {"impact": 50, "risk": -3},
        "owner_directed": True,
    })
    types = [a["type"] for a in spec["actions"]]
    check("الأفعال المجهولة تُحذف", types == ["like"], str(types))
    check("المصدر المجهول يعود إلى الافتراضي",
          spec["target"]["source"] == "any", spec["target"]["source"])
    check("الأعداد تُقصّ إلى مداها",
          spec["actions"][0]["count"] <= 5 and spec["trigger"]["every_cycles"] >= 1
          and spec["trigger"]["max_per_day"] <= 50,
          f"count={spec['actions'][0]['count']} every={spec['trigger']['every_cycles']}")
    check("الساعات غير الصالحة تُصفّى", spec["trigger"]["hours"] == [7],
          str(spec["trigger"]["hours"]))
    check("الصيغة المجهولة تُفرَّغ", spec["content"]["format"] == "")
    check("الأولويات تبقى داخل 0..1",
          0 < spec["priority"]["impact"] <= 0.95 and spec["priority"]["risk"] >= 0)
    check("لا يمكن للمواصفة أن تمنح نفسها صلاحية أمر المالك",
          "owner_directed" not in spec, str(list(spec.keys())))


# ─────────────────── 3) التشغيل: اقتراح حقيقي ───────────────────
def _skill_cfg(**spec_over):
    cfg = make_config()
    base = {
        "label": "مهارة اختبار",
        "target": {"source": "timeline", "specialty_only": False},
        "actions": [{"type": "like", "count": 1}],
    }
    base.update(spec_over)
    CS.save_spec(cfg, base)
    return cfg


def check_runner_proposes() -> None:
    cfg = _skill_cfg(actions=[{"type": "reply", "count": 1},
                              {"type": "like", "count": 1}])
    ctx = make_context(cfg, with_signals=True)
    props = CS.CustomSkillRunner().propose(ctx)
    caps = sorted({p.capability for p in props})
    check("المهارة تقترح إجراءات حقيقية على أهداف موجودة",
          caps == ["like_post", "reply_to_user"], str(caps))
    check("كل مقترح يحمل هدفاً فعلياً (رابط تغريدة)",
          all((p.params or {}).get("tweet_url") for p in props))
    check("المقترحات موسومة بمعرّف المهارة (لاحتساب حصتها)",
          all((p.params or {}).get("_skill", "").startswith("custom_") for p in props))
    check("المهارة لا تمنح نفسها صلاحية أمر المالك",
          not any((p.params or {}).get("owner_directed") for p in props))


def check_runner_respects_filters() -> None:
    # كلمة غير موجودة في أي تغريدة ⇒ لا هدف
    cfg = _skill_cfg(target={"source": "timeline", "specialty_only": False,
                             "include": ["كلمة_لا_توجد_إطلاقاً"]})
    ctx = make_context(cfg, with_signals=True)
    props = CS.CustomSkillRunner().propose(ctx)
    check("فلتر «يجب أن تحتوي» يُحترم", not props, f"{len(props)} مقترح")
    check("وتُسجَّل ملاحظة تشرح سبب عدم التنفيذ",
          bool(ctx.scratch.get("custom_skill_notes")),
          str(ctx.scratch.get("custom_skill_notes")))

    cfg2 = _skill_cfg(target={"source": "timeline", "specialty_only": False,
                              "exclude": ["الذكاء"]})
    ctx2 = make_context(cfg2, with_signals=True)
    check("فلتر الاستبعاد يمنع الهدف المخالف",
          not CS.CustomSkillRunner().propose(ctx2))

    cfg3 = _skill_cfg(target={"source": "timeline", "specialty_only": False,
                              "handles": ["ai_daily"]})
    ctx3 = make_context(cfg3, with_signals=True)
    props3 = CS.CustomSkillRunner().propose(ctx3)
    check("قائمة الحسابات المسموحة تُحترم",
          bool(props3) and all("ai_daily" in (p.params or {}).get("handle", "")
                               for p in props3),
          str([(p.params or {}).get("handle") for p in props3]))


def check_runner_respects_hard_filters() -> None:
    """الفلاتر الثابتة تسبق رغبة المالك: سبام ومحظورات وتكرار."""
    cfg = _skill_cfg(target={"source": "timeline", "specialty_only": False})
    ctx = make_context(cfg, with_signals=False)
    ctx.scratch["timeline"] = [
        {"url": "https://x.com/ad/status/1", "handle": "@ad",
         "text": "خصم خاص! اشتر الآن عبر رابط الشراء واربح جائزة كبرى اليوم."},
        {"url": "https://x.com/sport/status/2", "handle": "@sport",
         "text": "الرياضة اليوم: مباراة قوية بين الفريقين في الدوري المحلي."},
    ]
    props = CS.CustomSkillRunner().propose(ctx)
    check("الإعلانات/السبام مستبعدة حتى لو ألغى المالك قيد التخصص",
          not any("/ad/" in (p.params or {}).get("tweet_url", "") for p in props))
    check("ما يخالف محظورات الشخصية (الرياضة) مستبعد",
          not any("/sport/" in (p.params or {}).get("tweet_url", "") for p in props),
          str([(p.params or {}).get("tweet_url") for p in props]))


def check_publish_skill() -> None:
    cfg = _skill_cfg(
        label="ثريد أسبوعي",
        actions=[{"type": "thread", "count": 1}],
        content={"brief": "اشرح أداة تعليمية واحدة بخطوات عملية"},
    )
    ctx = make_context(cfg, with_signals=True)
    props = CS.CustomSkillRunner().propose(ctx)
    check("مهارة النشر تقترح ثريداً",
          [p.capability for p in props] == ["publish_thread"],
          str([p.capability for p in props]))
    brief = (props[0].params or {}).get("brief", "") if props else ""
    check("توجيه المالك يدخل موجز الكتابة حرفياً",
          "أداة تعليمية واحدة" in brief)
    check("والموجز مؤرضن بموضوع حقيقي من مصادر الدورة",
          "موضوع حقيقي" in brief, brief[:80])


# ─────────────────── 4) المؤقّتات ───────────────────
def check_triggers() -> None:
    cfg = _skill_cfg(trigger={"every_cycles": 3})
    ctx = make_context(cfg, with_signals=True)
    spec = CS.list_specs(cfg)[0]

    cfg.runtime_state.cycles_completed = 3
    check("«كل 3 دورات» تعمل في دورها", CS.should_run(cfg, spec, ctx) == "")
    cfg.runtime_state.cycles_completed = 4
    check("ولا تعمل في غير دورها", CS.should_run(cfg, spec, ctx) != "")

    cfg2 = _skill_cfg(trigger={"max_per_day": 1})
    ctx2 = make_context(cfg2, with_signals=True)
    spec2 = CS.list_specs(cfg2)[0]
    check("قبل الاستهلاك تعمل", CS.should_run(cfg2, spec2, ctx2) == "")
    CS.note_run(cfg2, spec2["id"])
    check("وبعد بلوغ الحد اليومي تتوقف",
          "اليومي" in CS.should_run(cfg2, spec2, ctx2),
          CS.should_run(cfg2, spec2, ctx2))

    cfg3 = _skill_cfg(trigger={"requires_mentions": True})
    ctx3 = make_context(cfg3, with_signals=False)
    spec3 = CS.list_specs(cfg3)[0]
    check("«فقط عند وجود منشنات» تُحترم",
          CS.should_run(cfg3, spec3, ctx3) == "لا منشنات واردة")

    cfg4 = _skill_cfg()
    ctx4 = make_context(cfg4, with_signals=True)
    spec4 = CS.list_specs(cfg4)[0]
    spec4["enabled"] = False
    check("المهارة الموقوفة لا تعمل", CS.should_run(cfg4, spec4, ctx4) == "موقوفة")
    check("ولا تقترح شيئاً", not CS.CustomSkillRunner().propose(ctx4))


# ─────────────────── 5) التخزين ───────────────────
def check_storage() -> None:
    cfg = make_config()
    a = CS.save_spec(cfg, {"label": "أولى", "actions": [{"type": "like"}]})
    b = CS.save_spec(cfg, {"label": "أولى", "actions": [{"type": "reply"}]})
    check("مهارتان بنفس الاسم لا تدهسان بعضهما", a["id"] != b["id"],
          f"{a['id']} / {b['id']}")
    check("كلتاهما محفوظتان", len(CS.list_specs(cfg)) == 2)

    b["label"] = "معدّلة"
    CS.save_spec(cfg, b)
    check("الحفظ بنفس المعرّف تحديث لا إضافة",
          len(CS.list_specs(cfg)) == 2
          and any(s["label"] == "معدّلة" for s in CS.list_specs(cfg)))

    CS.set_enabled(cfg, a["id"], False)
    check("الإيقاف يعمل",
          not [s for s in CS.list_specs(cfg) if s["id"] == a["id"]][0]["enabled"])
    check("الحذف يعمل", CS.delete_spec(cfg, a["id"]) and len(CS.list_specs(cfg)) == 1)
    check("حذف معرّف غير موجود لا ينهار", CS.delete_spec(cfg, "nope") is False)

    # حفظ البرسونا من الواجهة يدمج القواعد (update) — يجب ألا يمحو المهارات
    cfg.business_rules.update({"max_likes_per_cycle": 5, "daily_post_target": 2})
    check("حفظ إعدادات البرسونا لا يمحو المهارات المخصّصة",
          len(CS.list_specs(cfg)) == 1)


def check_registered_and_always_on() -> None:
    from app.aeos.skills.builtin import default_skills

    names = [s.name for s in default_skills()]
    check("المشغّل مسجّل ضمن المهارات الافتراضية", "custom_skills" in names)
    runner = [s for s in default_skills() if s.name == "custom_skills"][0]
    check("ويعمل دائماً (لا يعتمد على installed_skills القديمة)",
          runner.always_on is True)


def check_planner_pipeline() -> None:
    """المسار الكامل: المخطّط يجمع مقترح المهارة ومحرّك القرار قد ينفّذه."""
    from app.aeos.decision.engine import WeightedUtilityDecisionEngine
    from app.aeos.planner.planner import SkillBasedPlanner
    from app.aeos.skills.registry import SkillRegistry
    from app.aeos.skills.builtin import default_skills

    cfg = _skill_cfg(label="مهارة المسار",
                     actions=[{"type": "like", "count": 1}],
                     priority={"impact": 0.9, "urgency": 0.9, "risk": 0.05})
    cfg.installed_skills = []          # لا مهارة مدمجة: المقترح من المخصّصة فقط
    cfg.business_rules["llm_decision_review"] = False
    ctx = make_context(cfg, with_signals=True)

    registry = SkillRegistry()
    for sk in default_skills():
        registry.register(sk)
    proposals = SkillBasedPlanner(registry, None).plan(ctx)
    from_custom = [p for p in proposals if (p.params or {}).get("_skill")]
    check("المخطّط يجمع مقترحات المهارة المخصّصة", bool(from_custom),
          f"{len(from_custom)} مقترح")

    decided = WeightedUtilityDecisionEngine().decide(proposals, ctx)
    check("محرّك القرار ينفّذها كأي مهارة أخرى",
          any((d.action.params or {}).get("_skill") for d in decided),
          str([d.action.capability for d in decided]))


def main() -> int:
    print("=" * 74)
    print("verify_custom_skill — مهارة لكل موظف من برومبت  (بلا شبكة، بلا LLM)")
    print("=" * 74)

    print("\n[1] التوليد من البرومبت")
    check_generation_without_llm()
    check_generation_from_llm_payload()

    print("\n[2] الأمان — القوائم البيضاء")
    check_whitelist_hardening()

    print("\n[3] التشغيل والحدود")
    check_runner_proposes()
    check_runner_respects_filters()
    check_runner_respects_hard_filters()
    check_publish_skill()

    print("\n[4] المؤقّتات")
    check_triggers()

    print("\n[5] التخزين والتسجيل والمسار الكامل")
    check_storage()
    check_registered_and_always_on()
    check_planner_pipeline()

    failed = [n for n, ok, _ in _results if not ok]
    print("\n" + "=" * 74)
    print(f"النتيجة: {len(_results) - len(failed)}/{len(_results)} نجحت")
    for name in failed:
        print(f"  FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
