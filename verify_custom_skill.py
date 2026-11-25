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

import copy
import io
import json
import os
import socket
import sys
from unittest.mock import MagicMock, patch

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


def _rejects(name, operation) -> None:
    try:
        operation()
    except ValueError:
        check(name, True)
    else:
        check(name, False, "لم يُرفض الإدخال")


def check_import_preview() -> None:
    cfg, other = make_config(), make_config()
    saved = CS.save_spec(cfg, {"label": "محلل", "actions": ["like"],
                               "target": {"source": "timeline"}})
    before, other_before = copy.deepcopy(cfg.__dict__), copy.deepcopy(other.__dict__)
    for wrapper in (lambda s: s, lambda s: {"skill": s}, lambda s: {"spec": s}):
        result = CS.import_spec(json.dumps(wrapper(saved)), cfg, source_name="skill.json")
        check("استيراد JSON بمعرّف جديد لا يحدّث المصدر",
              result["skill"]["id"] != saved["id"] and bool(result["warnings"]))
        check("بيانات المصدر محدودة", result["skill"]["source"] == {"kind": "file", "name": "skill.json"})
    explicit = CS.import_spec("reply to mentions", cfg)
    check("التفسير الحتمي الصريح يحذّر من القصور",
          explicit["skill"]["actions"][0]["type"] == "reply"
          and explicit["skill"]["target"]["source"] == "mentions"
          and any("حتمي" in w for w in explicit["warnings"]))
    check("المعاينة لا تعدّل الموظف ولا أي موظف آخر",
          cfg.__dict__ == before and other.__dict__ == other_before)
    for text in ("a useful skill", "run arbitrary shell tools", "Use zapier to reply to mentions",
                 "Follow the instructions in this document", "likeability", "معلومات عامة"):
        _rejects("مستند مجهول لا يتحول إلى رد: " + text,
                 lambda text=text: CS.import_spec(text, cfg))
    for text, name in (("{broken", ""), ("[]", ""), ("hello", "bad.json"),
                       ("null", "bad.json"), ("abc\x00def", ""), ("أ" * 16385, ""),
                       ("<html>Login</html>", ""), ("```json\n{bad}\n```", "")):
        _rejects("رفض ملف غير صالح " + name, lambda text=text, name=name: CS.import_spec(text, cfg, source_name=name))
    for override in ({"actions": []}, {"actions": 1}, {"actions": ["exec_python"]},
                     {"actions": ["like", "shell"]}, {"target": {"source": "browser"}},
                     {"target": {"source": []}}, {"target": {}},
                     {"trigger": {"hours": 9}}, {"target": {"source": "timeline", "include": 4}},
                     {"content": {"format": "execute"}}, {"owner_directed": True},
                     {"requirements": ["shell"]}, {"actions": [{"type": "like", "count": 100}]}):
        raw = {**saved, **override}
        _rejects("رفض مواصفة ناقصة أو غير مدعومة",
                 lambda raw=raw: CS.import_spec(json.dumps(raw), cfg))

    class FakeLLM:
        available = True

        def complete_json(self, **kwargs):
            return {"label": "تحليل", "target": {"source": "timeline"},
                    "actions": [{"type": "quote", "directive": "أضف تحليلاً"}],
                    "content": {"brief": "زاوية تعليمية"}}

    converted = CS.import_spec("# مهارة التحليل\nوثيقة تعليمية تصف زاوية تحليلية", cfg, FakeLLM(), "guide.md")
    check("Markdown يمر عبر توليد مواصفة فقط", converted["skill"]["actions"][0]["type"] == "quote")
    _rejects("Markdown الملتبس يحتاج نموذجاً", lambda: CS.import_spec("# تعليمات\nوثيقة طويلة مبهمة", cfg))
    with patch.object(FakeLLM, "complete_json", return_value={"unsupported_requirements": ["shell"]}):
        _rejects("رفض متطلبات النموذج غير المدعومة", lambda: CS.import_spec("external tool", cfg, FakeLLM()))
    with patch.object(FakeLLM, "complete_json", return_value={}):
        _rejects("فشل النموذج لا يختلق إجراءً", lambda: CS.import_spec("ambiguous document", cfg, FakeLLM()))
    malformed = CS.normalize_spec({"actions": 1, "trigger": {"hours": 1},
                                  "target": {"include": 1, "handles": {}}, "source": []})
    check("التطبيع آمن مع قوائم JSON معطوبة", malformed["actions"] == [] and malformed["trigger"]["hours"] == [])
    metadata = CS.normalize_spec({"source": {"kind": "file", "name": "n" * 500, "code": "evil"}})
    check("التطبيع يحذف مفاتيح المصدر غير المعروفة ويحد الاسم",
          set(metadata["source"]) == {"kind", "name"} and len(metadata["source"]["name"]) == 200)
    _rejects("لا حفظ لمهارة بلا إجراء", lambda: CS.save_spec(cfg, {"label": "فارغة", "actions": ["shell"]}))
    for index in range(11):
        CS.save_spec(cfg, {"label": f"skill {index}", "actions": ["like"]})
    full = copy.deepcopy(cfg.__dict__)
    _rejects("الحد الأقصى لا يحذف مهارة قديمة", lambda: CS.save_spec(cfg, {"label": "overflow", "actions": ["like"]}))
    check("رفض تجاوز الحد لا يغيّر الإعدادات", full == cfg.__dict__ and len(CS.list_specs(cfg)) == 12)
    changed = {**saved, "description": "تحديث"}
    CS.save_spec(cfg, changed)
    check("التحديث يعمل عند بلوغ الحد", len(CS.list_specs(cfg)) == 12 and CS.list_specs(cfg)[0]["description"] == "تحديث")
    long_cfg = make_config()
    long = CS.save_spec(long_cfg, {"label": "long" * 30, "actions": ["like"]})
    CS.save_spec(long_cfg, {**long, "description": "updated"})
    check("معرّف الاسم الطويل يبقى ثابتاً عند التحديث", len(CS.list_specs(long_cfg)) == 1 and CS.list_specs(long_cfg)[0]["description"] == "updated")


def check_readiness_and_directives() -> None:
    cfg = make_config()
    spec = CS.normalize_spec({"label": "جاهزية", "target": {"source": "mentions"}, "actions": ["reply"]})
    before = copy.deepcopy(cfg.__dict__)
    check("الجاهزية لا تدعي التنفيذ", CS.skill_readiness(cfg, spec)["ready"] and bool(CS.skill_readiness(cfg, spec)["warnings"]))
    check("فحص الجاهزية لا يغيّر الإعدادات", before == cfg.__dict__)
    cfg.mcp_servers = ["x"]
    result = CS.skill_readiness(cfg, spec)
    check("الرد يحتاج content ومصدر الرصد الفعلي", not result["ready"] and result["missing_mcp"] == ["content", "playwright_x"])
    cfg.mcp_servers = ["content", "playwright_x"]
    check("المنصة غير الموصولة تمنع الجاهزية", CS.skill_readiness(cfg, spec)["missing_mcp"] == ["x"])
    cfg = make_config(max_replies_per_cycle=0)
    check("السقف صفر يمنع الجاهزية", CS.skill_readiness(cfg, spec)["blocked_capabilities"] == ["reply_to_user"])
    cfg = make_config(blocked_capabilities=["read_notifications", "reply_to_user"])
    check("محظورات الإجراء والمصدر تمنع الجاهزية", CS.skill_readiness(cfg, spec)["blocked_capabilities"] == ["read_notifications", "reply_to_user"])
    check("لا جاهزية بلا إجراءات", not CS.skill_readiness(cfg, {"label": "empty", "actions": []})["ready"])
    for action in ("quote", "post", "thread"):
        cfg = make_config()
        cfg.mcp_servers.remove("content")
        candidate = CS.normalize_spec({"label": action, "target": {"source": "timeline"}, "actions": [action]})
        check("توليد المحتوى يتطلب content: " + action, CS.skill_readiness(cfg, candidate)["missing_mcp"] == ["content"])
    for action in ("reply", "quote", "post", "thread"):
        cfg = _skill_cfg(description="هدف المهارة التعليمي",
                         actions=[{"type": action, "directive": "توجيه الإجراء"}],
                         content={"brief": "موجز المحتوى"})
        proposals = CS.CustomSkillRunner().propose(make_context(cfg, with_signals=True))
        params = proposals[0].params if proposals else {}
        directive = params.get("skill_directive" if action in ("reply", "quote") else "brief", "")
        check("يحتفظ بتوجيهي المهارة والفعل: " + action,
              "توجيه الإجراء" in directive and "موجز المحتوى" in directive)
        check("مبرر القرار يوضح الهدف والتوجيه: " + action,
              bool(proposals) and all(s in proposals[0].rationale for s in ("هدف المهارة", "توجيه الإجراء", "موجز المحتوى")))
        if action in ("reply", "quote"):
            check("لا يُحوّل التوجيه إلى brief المنطقي: " + action, "brief" not in params and len(directive) <= 1201)
        check("لا صلاحية owner_directed: " + action, "owner_directed" not in params)


def check_safe_fetch() -> None:
    public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

    class Response:
        def __init__(self, body=b"reply to mentions", status=200, **headers):
            self.status = status
            self.headers = {"Content-Type": "text/plain", **headers}
            self.body = io.BytesIO(body)

        def getheader(self, key):
            return self.headers.get(key)

        def read1(self, count):
            return self.body.read(count)

    def fetch(response, url="https://example.org/skill.md", dns=None):
        connection = MagicMock()
        connection.getresponse.side_effect = response if isinstance(response, list) else [response]
        with patch.object(CS.socket, "getaddrinfo", return_value=public, side_effect=dns), patch.object(
                CS, "_PinnedHTTPSConnection", return_value=connection) as transport:
            result = CS.fetch_skill_document(url)
            check("جلب بلا كوكيز أو تفويض", "Cookie" not in connection.request.call_args.kwargs["headers"]
                  and "Authorization" not in connection.request.call_args.kwargs["headers"])
            return result, transport.call_args

    _rejects("مهلة DNS منتهية لا تبدأ طلباً", lambda: CS._resolve_public("example.org", 0))
    _rejects("مهلة النقل محدودة", lambda: fetch([TimeoutError("timeout")]))
    result, call = fetch(Response())
    check("جلب نص عام بنقل محاكى مثبت العنوان", result == "reply to mentions" and call.args[1] == public[0]
          and 0 < call.args[2] <= CS._FETCH_TIMEOUT)
    result, call = fetch(Response(), "https://github.com/owner/repo/blob/main/skill.md")
    check("تحويل GitHub blob إلى raw", call.args[0] == "raw.githubusercontent.com")
    for url in ("http://example.org/doc", "https://localhost/doc", "https://127.0.0.1/doc",
                "https://10.0.0.1/doc", "https://169.254.169.254/doc", "https://[::1]/doc",
                "https://user:pass@example.org/doc", "https://example.org:444/doc",
                "https://example.org\\@127.0.0.1/doc"):
        _rejects("حظر رابط غير عام " + url, lambda url=url: fetch(Response(), url))
    _rejects("حظر أي عنوان خاص ضمن إجابة DNS", lambda: fetch(Response(), dns=[public + private]))
    _rejects("حظر DNS خاص", lambda: fetch(Response(), dns=[private]))
    _rejects("حظر تحويل إلى عنوان خاص", lambda: fetch(Response(status=302, Location="https://127.0.0.1/doc")))
    _rejects("إعادة فحص DNS لكل تحويل تمنع rebinding", lambda: fetch(
        [Response(status=302, Location="/next"), Response()], dns=[public, private]))
    _rejects("عدد تحويلات محدود", lambda: fetch([Response(status=302, Location="/next") for _ in range(4)]))
    _rejects("حظر الحجم المعلن الكبير", lambda: fetch(Response(**{"Content-Length": "32769"})))
    _rejects("حظر الحجم المتدفق الكبير", lambda: fetch(Response(body=b"a" * 32769)))
    _rejects("حظر HTML", lambda: fetch(Response(body=b"<html>login</html>")))
    _rejects("حظر صفحة تسجيل الدخول", lambda: fetch(Response(**{"Content-Type": "text/html"})))
    _rejects("حظر الأرشيف", lambda: fetch(Response(**{"Content-Type": "application/zip"})))
    _rejects("حظر ضغط النقل", lambda: fetch(Response(**{"Content-Encoding": "gzip"})))
    _rejects("حظر الملفات الثنائية", lambda: fetch(Response(body=b"abc\x00def")))
    _rejects("حظر الترميز غير الصالح", lambda: fetch(Response(body=b"\xff\xfe")))
    result, _ = fetch(Response(body=b"a" * 32768))
    check("الحد الأقصى مسموح دون تجاوزه", len(result.encode("utf-8")) == 32768)
    sock, tls = MagicMock(), MagicMock()
    sock.getpeername.return_value = ("93.184.216.34", 443)
    with patch.object(CS.socket, "socket", return_value=sock), patch.object(
            CS.ssl, "create_default_context", return_value=tls), patch.object(
            CS.socket, "getaddrinfo", side_effect=AssertionError("DNS must not run during connect")):
        connection = CS._PinnedHTTPSConnection("example.org", public[0], 2)
        connection.connect()
        check("الاتصال المثبت لا يعيد DNS ويحافظ على SNI والتحقق من الشهادة",
              sock.connect.call_args.args == (("93.184.216.34", 443),)
              and tls.wrap_socket.call_args.kwargs["server_hostname"] == "example.org")
        sock.getpeername.return_value = ("127.0.0.1", 443)
        _rejects("رفض اتصال اختلف عنوانه المثبت", connection.connect)


def check_skill_api_isolation() -> None:
    import json
    from copy import deepcopy
    from types import SimpleNamespace
    from unittest.mock import patch
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.aeos.api import routes as R

    configs = {}
    for employee_id, owner in (("a", 1), ("b", 1), ("c", 2)):
        cfg = make_config()
        cfg.id, cfg.user_id = employee_id, owner
        configs[employee_id] = cfg
    writes = []

    def save(cfg):
        configs[cfg.id] = deepcopy(cfg)
        writes.append(cfg.id)
        return cfg

    platform = SimpleNamespace(repository=SimpleNamespace(
        get=lambda eid: deepcopy(configs.get(eid)), save=save), llm=None)
    app = FastAPI()
    app.include_router(R.router)
    spec = {"label": "Imported", "target": {"source": "timeline"},
            "actions": [{"type": "like"}]}
    with patch.object(R, "get_platform", return_value=platform), TestClient(app) as client:
        check("واجهة المهارات ترفض الطلب غير الموثق",
              client.get("/api/aeos/employees/a/skills").status_code in (401, 403))
        app.dependency_overrides[R.get_current_user] = lambda: SimpleNamespace(id=1)
        with patch.object(CS, "fetch_skill_document") as fetch:
            denied = [
                client.get("/api/aeos/employees/c/skills"),
                client.post("/api/aeos/employees/c/skills/generate", json={"prompt": "reply to mentions"}),
                client.post("/api/aeos/employees/c/skills/import", json={"url": "https://example.org/skill.md"}),
                client.post("/api/aeos/employees/c/skills", json={"spec": spec}),
                client.patch("/api/aeos/employees/c/skills/custom_test", json={"enabled": True}),
                client.delete("/api/aeos/employees/c/skills/custom_test"),
            ]
            check("كل عمليات المهارة تتحقق من ملكية الموظف قبل العمل",
                  all(r.status_code == 404 for r in denied) and not writes and not fetch.called)
        preview = client.post("/api/aeos/employees/a/skills/import",
                              json={"content": json.dumps(spec), "filename": "skill.json"})
        check("استيراد الملف معاينة معزولة بلا حفظ",
              preview.status_code == 200 and preview.json()["saved"] is False
              and preview.json()["employee_id"] == "a" and not writes)
        draft = preview.json()["skill"]
        other = client.post("/api/aeos/employees/a/skills/import",
                            json={"content": json.dumps(spec)}).json()["skill"]
        check("المعاينات المتزامنة لا تتشارك معرّف مهارة قابل للاستبدال", draft["id"] != other["id"])
        created = client.post("/api/aeos/employees/a/skills", json={"spec": draft})
        check("التفعيل يحفظ للموظف المقصود فقط",
              created.status_code == 200 and writes == ["a"]
              and len(CS.list_specs(configs["a"])) == 1
              and not CS.list_specs(configs["b"]) and not CS.list_specs(configs["c"]))
        check("المهارة لا تُضاف إلى قائمة المهارات العالمية أو المثبتة",
              configs["a"].installed_skills == make_config().installed_skills)
        listed = client.get("/api/aeos/employees/a/skills").json()["custom_skills"][0]
        check("القائمة تعرض الجاهزية والاستخدام والأصل",
              listed["readiness"]["ready"] and listed["runs_today"] == 0
              and listed["source"]["kind"] == "file")
        check("موظف آخر لن يجد المهارة ولو عرف معرّفها",
              client.patch(f"/api/aeos/employees/b/skills/{draft['id']}",
                           json={"enabled": False}).status_code == 404)
        with patch.object(CS, "fetch_skill_document", return_value=json.dumps(spec)) as fetch:
            result = client.post("/api/aeos/employees/a/skills/import",
                                 json={"url": "https://example.org/skill.json"})
            check("جلب الرابط يمر عبر الجالب الآمن ويظل معاينة",
                  result.status_code == 200 and not result.json()["saved"]
                  and fetch.call_count == 1 and writes == ["a"])
        check("مصدر واحد مطلوب للجلب",
              client.post("/api/aeos/employees/a/skills/import", json={}).status_code == 400
              and client.post("/api/aeos/employees/a/skills/import", json={
                  "content": "reply to mentions", "url": "https://example.org/x"}).status_code == 400)
        check("الإنشاء المجهول لا يختلق مهارة رد",
              client.post("/api/aeos/employees/a/skills/generate",
                          json={"prompt": "Run an arbitrary custom script"}).status_code == 400)
        generated = client.post("/api/aeos/employees/a/skills/generate",
                                json={"prompt": "reply to mentions"})
        check("الإنشاء الصريح يعرض ما فهمه قبل الحفظ",
              generated.status_code == 200 and not generated.json()["saved"]
              and generated.json()["warnings"] and writes == ["a"])
        configs["a"].mcp_servers = []
        blocked = client.post("/api/aeos/employees/a/skills", json={"spec": other})
        check("الخادم نفسه يمنع تفعيل مهارة أدواتها ناقصة", blocked.status_code == 400 and writes == ["a"])
        check("إيقاف مهارة يبقى ممكناً بعد فصل أدواتها",
              client.patch(f"/api/aeos/employees/a/skills/{draft['id']}",
                           json={"enabled": False}).status_code == 200)
        check("إعادة تفعيل المهارة تتحقق من الجاهزية مجدداً",
              client.patch(f"/api/aeos/employees/a/skills/{draft['id']}",
                           json={"enabled": True}).status_code == 400)


def check_skill_persistence_during_cycle() -> None:
    import json
    from app.aeos.employee.repository import SqlEmployeeRepository
    from app.db.models import AIEmployee

    repo = SqlEmployeeRepository()
    cfg = make_config()
    row = AIEmployee(id=cfg.id)
    repo._apply(row, cfg)
    cycle = repo._to_domain(row)
    first, second = repo._to_domain(row), repo._to_domain(row)
    CS.save_spec(first, {"label": "First", "actions": [{"type": "like"}]})
    CS.save_spec(second, {"label": "Second", "actions": [{"type": "reply"}]})
    repo._apply(row, first)
    repo._apply(row, second)
    repo._apply(row, cycle)
    saved = json.loads(row.business_rules_json)[CS.STORE_KEY]
    check("حفظ دورة قديمة لا يمحو المهارات المضافة أثناء عملها",
          {s["label"] for s in saved} == {"First", "Second"})
    stale = repo._to_domain(row)
    deleting = repo._to_domain(row)
    CS.delete_spec(deleting, saved[0]["id"])
    repo._apply(row, deleting)
    repo._apply(row, stale)
    check("حفظ دورة قديمة لا يعيد مهارة حُذفت",
          len(json.loads(row.business_rules_json)[CS.STORE_KEY]) == 1)


def check_skill_execution_context() -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock
    from app.aeos.capabilities.base import CapabilityResult
    from app.aeos.cognition.phases import ExecutePhase, _action_guard_entry
    from app.aeos.decision.engine import Decision
    from app.aeos.mcp.base import MCPResponse

    from scripts.security_fixture import setup
    from app.db import database
    from app.db.models import AIEmployee
    cfg = make_config()
    setup(cfg.user_id, [(cfg.id, cfg.account_username)])
    with database.SessionLocal() as db:
        db.get(AIEmployee, cfg.id).mcp_servers_json = json.dumps(cfg.mcp_servers)
        db.commit()

    for kind in ("reply", "quote"):
        cfg = make_config()
        spec = CS.save_spec(cfg, {"label": "Procedure", "target": {"source": "timeline"},
                                 "content": {"brief": "COMPARE_EVIDENCE"},
                                 "actions": [{"type": kind, "directive": "STATE_UNCERTAINTY"}]})
        ctx = make_context(cfg)
        ctx.workspace = SimpleNamespace(employee_id=cfg.id)
        proposed = CS.CustomSkillRunner().propose(ctx)[0]
        ctx.scratch["decisions"] = [Decision(proposed, 0.8, "selected")]
        ctx.mcp = Mock()
        ctx.mcp.call.side_effect = lambda req: MCPResponse.ok(text="إضافة مفيدة") if req.tool == "generate" else MCPResponse.fail("mocked action")
        ExecutePhase().run(ctx)
        generated = next(call.args[0] for call in ctx.mcp.call.call_args_list if call.args[0].tool == "generate")
        check(f"تعليمات المهارة تصل فعلاً إلى نموذج {kind} دون تحويلها لاختصار",
              "COMPARE_EVIDENCE" in generated.params["system"]
              and "STATE_UNCERTAINTY" in generated.params["system"]
              and generated.params["max_tokens"] > 60)
        guarded = _action_guard_entry(0, ctx.scratch["decisions"][0])
        check(f"حارس الشخصية يراجع تعليمات مهارة {kind} أيضاً", "STATE_UNCERTAINTY" in guarded)
        check(f"فشل الأداة لا يدعي إتقاناً أو يستهلك حصة المهارة {kind}",
              CS.runs_today(cfg, spec["id"]) == 0 and not ctx.scratch["execution_results"][0]["success"])

    cfg = make_config()
    spec = CS.save_spec(cfg, {"label": "Quota", "trigger": {"max_per_day": 1},
                             "actions": [{"type": "like"}]})
    ctx = make_context(cfg)
    action = CS.CustomSkillRunner().propose(ctx)[0]
    ctx.scratch["decisions"] = [Decision(action, 0.8, "first"), Decision(action, 0.7, "second")]
    execute = Mock(return_value=CapabilityResult.ok("like_post"))
    ctx.capabilities = SimpleNamespace(execute=execute)
    ExecutePhase().run(ctx)
    check("حصة المهارة تُراجع بين الإجراءات ولا تتجاوز الموظف الواحد",
          execute.call_count == 1 and CS.runs_today(cfg, spec["id"]) == 1
          and ctx.scratch["execution_results"][1]["status"] == "skipped")
    other = make_context(make_config())
    other.scratch["decisions"] = [Decision(action, 0.8, "foreign skill")]
    other.capabilities = SimpleNamespace(execute=Mock())
    ExecutePhase().run(other)
    check("لا تنفيذ لمهارة موظف عبر سياق موظف آخر", not other.capabilities.execute.called)


def check_skill_browser(base_url: str) -> None:
    import json
    import re
    from urllib.parse import urlsplit
    from playwright.sync_api import sync_playwright, expect

    employee_ids = {"alpha": "ui-a", "beta": "ui-b"}
    stored = {"ui-a": [], "ui-b": [], "ui-new": []}
    calls, errors = [], []
    wrong_employee = False
    html = '''<html><head><link rel="icon" href="data:,"><link rel="stylesheet" href="/src/index.css"></head><body>
    <div id="root"></div><script type="module">
    import RefreshRuntime from '/@react-refresh';
    RefreshRuntime.injectIntoGlobalHook(window);
    window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type;
    window.__vite_plugin_react_preamble_installed__ = true;
    const React = (await import('/node_modules/.vite/deps/react.js')).default;
    const {createRoot} = (await import('/node_modules/.vite/deps/react-dom_client.js')).default;
    const Drawer = (await import('/src/components/AgentsDrawer.jsx')).default;
    createRoot(document.getElementById('root')).render(React.createElement(Drawer,
        {open:true,onClose:()=>{},onNotify:()=>{}}));
    </script></body></html>'''

    def api(route):
        req = route.request
        path = urlsplit(req.url).path
        body = req.post_data_json if req.post_data else {}
        calls.append((path, req.method, body))
        if path == "/api/x/accounts":
            data = {"accounts": [{"label": k} for k in employee_ids]}
        elif path == "/api/aeos/agents":
            data = {"agents": [{"account_username": label, "employee_id": eid,
                                "enabled": False, "status": "paused", "persona": {},
                                "mcp_servers": ["x", "content", "playwright_x"]}
                               for label, eid in employee_ids.items()]}
        elif path.endswith("/skills/generate") or path.endswith("/skills/import"):
            eid = path.split("/")[4]
            data = {"ok": True, "employee_id": "wrong-employee" if wrong_employee else eid,
                    "saved": False, "skill": {"id": "ui-skill", "label": "اختبار مهارة",
                    "actions": [{"type": "reply", "count": 1}], "trigger": {"every_cycles": 1}},
                    "summary": "يرد على المنشنات وفق التعليمات", "warnings": [],
                    "readiness": {"ready": "blocked" not in str(body), "missing_mcp": [],
                                  "blocked_capabilities": [], "warnings": []}}
        elif path.endswith("/skills"):
            eid = path.split("/")[4]
            if req.method == "POST":
                stored[eid].append(body["spec"])
                data = {"ok": True, "employee_id": eid, "skill": body["spec"]}
            else:
                data = {"ok": True, "employee_id": eid, "custom_skills": stored[eid]}
        else:
            data = {}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(data))

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.set_default_timeout(10000)
            page.on("response", lambda response: errors.append(f"HTTP {response.status}: {response.url}") if response.status >= 400 else None)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(f"{message.text} {message.location}") if message.type == "error" else None)
            page.route("**/api/**", api)
            page.route("**/@vite/client", lambda route: route.fulfill(content_type="application/javascript", body=
                "export const createHotContext=()=>({accept(){},on(){},prune(){},dispose(){},invalidate(){},data:{}});"
                "export const updateStyle=()=>{};export const removeStyle=()=>{};"
                "export const injectQuery=(u,q)=>u+(u.includes('?')?'&':'?')+q;"))
            page.route("**/favicon.ico", lambda route: route.fulfill(status=204))
            page.route(base_url.rstrip("/") + "/", lambda route: route.fulfill(content_type="text/html", body=html))
            page.goto(base_url)
            page.get_by_role("button", name="التعليمات والبرسونا", exact=True).first.click()
            editor = page.get_by_role("group", name="فهم المهارة ومعاينة التنفيذ للموظف @alpha", exact=True)
            expect(editor.get_by_label("وصف المهارة المطلوب إنشاؤها")).to_be_enabled()
            editor.get_by_label("وصف المهارة المطلوب إنشاؤها").fill("reply to mentions")
            editor.get_by_role("button", name="فهم المهارة ومعاينة التنفيذ", exact=True).click()
            activate = editor.get_by_role("button", name="تفعيل المهارة لـ @alpha فقط", exact=True)
            expect(activate).to_be_enabled()
            check("الواجهة تعرض الإنشاء كمعاينة بلا حفظ تلقائي", not stored["ui-a"])
            activate.click()
            expect(editor.get_by_role("region")).to_have_count(0)
            check("زر التفعيل يحفظ للموظف المحدد فقط", len(stored["ui-a"]) == 1 and not stored["ui-b"])
            page.get_by_role("button", name="جلب مهارة", exact=True).first.click()
            file_input = editor.get_by_label(re.compile("قراءة ملف مهارة"))
            file_input.set_input_files({"name": "SKILL.md", "mimeType": "text/markdown", "buffer": b"reply to mentions"})
            expect(editor.get_by_label("محتوى المهارة (JSON أو Markdown أو نص عادي)")).to_have_value("reply to mentions")
            editor.get_by_role("button", name="فهم المهارة ومعاينة التنفيذ", exact=True).click()
            expect(activate).to_be_enabled()
            check("رفع الملف يرسل محتواه للمعاينة فقط",
                  any(path.endswith("/skills/import") and body.get("filename") == "SKILL.md"
                      and body.get("content") == "reply to mentions" for path, method, body in calls)
                  and len(stored["ui-a"]) == 1)
            editor.get_by_role("button", name="رابط HTTPS", exact=True).click()
            expect(editor.get_by_role("region")).to_have_count(0)
            editor.get_by_label("رابط HTTPS خارجي لملف المهارة").fill("https://example.org/SKILL.md")
            editor.get_by_role("button", name="فهم المهارة ومعاينة التنفيذ", exact=True).click()
            expect(activate).to_be_enabled()
            check("جلب الرابط يرسل الرابط إلى واجهة الموظف ولا ينشر شيئاً",
                  any(path.endswith("/skills/import") and body == {"url": "https://example.org/SKILL.md"}
                      for path, method, body in calls) and len(stored["ui-a"]) == 1)
            editor.get_by_label("رابط HTTPS خارجي لملف المهارة").fill("https://example.org/blocked.md")
            editor.get_by_role("button", name="فهم المهارة ومعاينة التنفيذ", exact=True).click()
            expect(activate).to_be_disabled()
            check("الواجهة تمنع تفعيل المعاينة غير الجاهزة", activate.is_disabled())
            employee_ids["alpha"] = "ui-new"
            page.get_by_title("تحديث", exact=True).click()
            expect(editor.get_by_role("region")).to_have_count(0)
            expect(editor.get_by_label("وصف المهارة المطلوب إنشاؤها")).to_be_enabled()
            check("استبدال الموظف لا ينقل مسودة الموظف القديم", not stored["ui-new"])
            wrong_employee = True
            editor.get_by_label("وصف المهارة المطلوب إنشاؤها").fill("reply to mentions")
            editor.get_by_role("button", name="فهم المهارة ومعاينة التنفيذ", exact=True).click()
            expect(editor.get_by_role("alert")).to_be_visible()
            expect(editor.get_by_role("region")).to_have_count(0)
            check("الواجهة ترفض استجابة معاينة تخص موظفاً آخر", not stored["ui-new"])
            check("واجهة المهارات تعمل بلا أخطاء JavaScript", not errors, str(errors))
        except Exception:
            print("UI errors:", errors)
            print("UI API paths:", [(path, method) for path, method, _ in calls])
            print("UI text:", page.locator("body").inner_text()[:1200])
            raise
        finally:
            browser.close()


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
    check_import_preview()
    check_readiness_and_directives()
    check_safe_fetch()
    check_skill_api_isolation()
    check_skill_persistence_during_cycle()
    check_skill_execution_context()

    failed = [n for n, ok, _ in _results if not ok]
    print("\n" + "=" * 74)
    print(f"النتيجة: {len(_results) - len(failed)}/{len(_results)} نجحت")
    for name in failed:
        print(f"  FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
