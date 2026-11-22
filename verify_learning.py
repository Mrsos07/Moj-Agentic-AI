"""
verify_learning.py — التعلّم الذاتي من الدورات، بلا شبكة وبلا X.

الحالة المرجعية من البيانات الحية: @liketoday5 مرّت 13 دورة من 25 بلا فعل لأن مهارة
الاقتباس تقترح محتوى سياسياً معاصراً والحارس يرفضه كل مرة، بينما التقييم يكرّر
«لا تكتفِ بالانتظار». يجب أن يتعلّم الموظف من الرفض نفسه فيغيّر سلوكه.

    .\\venv\\Scripts\\python.exe verify_learning.py
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("TOPIC_SEMANTIC", "0")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

# قاعدة مؤقتة: كانت هذه الفحوص تكتب في data/app.db الحقيقية (طبقات الذاكرة تستخدم SessionLocal الافتراضي)
from app.db.testing import use_temp_database  # noqa: E402
import app.db.database as _DB  # noqa: E402
from app.db.models import AIEmployee as _AIE  # noqa: E402

use_temp_database()
# الموظف الذي تكتب الفحوص ذاكرته (verify_creator.make_config) — PostgreSQL يفرض الـFK
with _DB.SessionLocal() as _s:
    _s.add(_AIE(id="verify-creator", platform="x", account_username="mazen_edu", cookie_label="mazen_edu"))
    _s.commit()

from app.aeos.cognition import critique as C                       # noqa: E402
from app.aeos.cognition import learning as L                       # noqa: E402
from app.aeos.decision.engine import WeightedUtilityDecisionEngine  # noqa: E402
from app.aeos.employee import strategy                             # noqa: E402
from app.aeos.skills.base import ProposedAction                    # noqa: E402
from verify_creator import make_config, make_context               # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


def quote(source="content_quoter", url="https://x.com/spagov/status/1"):
    return ProposedAction(capability="quote_post", source=source, expected_impact=0.9, urgency=0.8,
                          risk=0.2, params={"tweet_url": url, "original_text": "بيان سياسي معاصر",
                                            "handle": "@spagov"})


def otd_post():
    return ProposedAction(capability="publish_content", source="on_this_day", expected_impact=0.6,
                          urgency=0.5, risk=0.1, params={"brief": "حدث تاريخي"})


def engine():
    return WeightedUtilityDecisionEngine()


def ctx_for(cfg):
    ctx = make_context(cfg, with_signals=False)
    return ctx


def guard_reject(cfg, arm, reason="تبنٍّ علني لمحتوى سياسي معاصر خارج النمط التاريخي"):
    ctx = ctx_for(cfg)
    ctx.scratch["guard_report"] = {"reviewed": 1, "rejected": ["x"], "rejected_arms": [
        {"arm": arm, "reason": reason}]}
    ctx.scratch["execution_results"] = []
    out = L.record_cycle(ctx)
    cfg.runtime_state.cycles_completed += 1
    return out


print("1) الذراع = نوع الفعل × المهارة التي اقترحته")
check("arm_of يجمع الفعل والمصدر", L.arm_of(quote()) == "quote_post|content_quoter")
custom = ProposedAction(capability="like_post", source="custom_skill_runner", params={"_skill": "custom_x"})
check("المهارة المخصّصة تُميَّز بمعرّفها", L.arm_of(custom) == "like_post|custom:custom_x")
from app.aeos.planner.planner import SkillBasedPlanner  # noqa: E402
from app.aeos.skills.registry import SkillRegistry  # noqa: E402


class _S:
    name, claim_priority, always_on, uses_capabilities = "fake_skill", 0, True, []

    def propose(self, context):
        return [ProposedAction(capability="like_post", params={"tweet_url": "u"})]


reg = SkillRegistry()
reg.register(_S())
props = SkillBasedPlanner(reg).plan(ctx_for(make_config()))
check("المخطِّط يوسم كل اقتراح بمهارته", props[0].source == "fake_skill", props[0].source)

print("2) حالة liketoday5: رفض متكرّر ← تعليق مؤقت ← يختار البديل")
cfg = make_config(max_actions_per_cycle=1, daily_post_target=3, llm_decision_review=False)
cfg.runtime_state.cycles_completed = 100
ctx = ctx_for(cfg)
first = engine().decide([quote(), otd_post()], ctx)
check("قبل التعلّم: الاقتباس (منفعة أعلى) هو المختار", first[0].action.capability == "quote_post")
summaries = [guard_reject(cfg, "quote_post|content_quoter") for _ in range(3)]
check("الرفض الثالث المتتالي يعلّق الذراع", L.is_suppressed(cfg, "quote_post|content_quoter")
      and summaries[-1]["suspended"], summaries[-1])
ctx = ctx_for(cfg)
after = engine().decide([quote(), otd_post()], ctx)
check("بعد التعلّم: يختار منشور «في مثل هذا اليوم» بدل الاقتباس المرفوض",
      after[0].action.capability == "publish_content", [d.action.capability for d in after])
check("المحرّك يعلن الذراع المعلّق", "quote_post|content_quoter" in ctx.scratch["suspended_arms"])
until = L.state(cfg)["arms"]["quote_post|content_quoter"]["suppressed_until"]
# الرفض الثالث سُجّل في الدورة (الحالية − 1)، والتعليق 3 دورات منها
check("مدة التعليق الأولى 3 دورات", until - (cfg.runtime_state.cycles_completed - 1) == 3, until)
cfg.runtime_state.cycles_completed = until
check("بعد انقضاء المدة: فرصة تجربة جديدة", not L.is_suppressed(cfg, "quote_post|content_quoter"))
for _ in range(3):
    guard_reject(cfg, "quote_post|content_quoter")
rec = L.state(cfg)["arms"]["quote_post|content_quoter"]
check("تراجع أُسّي: التعليق الثاني 6 دورات", rec["strikes"] == 2
      and rec["suppressed_until"] - (cfg.runtime_state.cycles_completed - 1) == 6, rec)
v, meta = L.value(cfg, "quote_post|content_quoter")
check("قيمة الذراع المرفوض سالبة", v < -0.2, v)
owner = quote()
owner.params["owner_directed"] = True
check("أمر المالك يتجاوز التعليق", L.adjustment(cfg, owner)[0] == 0.0)

print("3) النشر الأصلي (هوية الموظف) لا يُعلَّق أبداً")
cfg2 = make_config()
for _ in range(6):
    guard_reject(cfg2, "publish_content|on_this_day", "يجمع عدة أحداث")
check("6 رفضات للنشر ← لا تعليق", not L.is_suppressed(cfg2, "publish_content|on_this_day"))
check("لكن قيمته تنخفض", L.value(cfg2, "publish_content|on_this_day")[0] < 0)

print("4) النجاح يرفع الذراع ويمحو الإنذارات")
cfg3 = make_config(llm_decision_review=False, max_actions_per_cycle=1)
ctx3 = ctx_for(cfg3)
for _ in range(5):
    L.record(cfg3, "reply_to_user|community_engager", "ok")
check("قيمة ذراع ناجح موجبة", L.value(cfg3, "reply_to_user|community_engager")[0] > 0.2)
rec = L.record(cfg3, "quote_post|content_quoter", "rejected")
rec["strikes"] = 2
L.record(cfg3, "quote_post|content_quoter", "ok")
check("نجاح بعد إنذارات يخفّضها", L.state(cfg3)["arms"]["quote_post|content_quoter"]["strikes"] == 1)
a = ProposedAction(capability="reply_to_user", source="community_engager", expected_impact=0.6,
                   urgency=0.5, risk=0.2, params={"tweet_url": "https://x.com/u/status/9"})
b = ProposedAction(capability="reply_to_user", source="timeline_engager", expected_impact=0.6,
                   urgency=0.5, risk=0.2, params={"tweet_url": "https://x.com/v/status/8"})
pick = engine().decide([b, a], ctx3)
check("بتساوي كل شيء: يُفضَّل الذراع الذي نجح سابقاً", pick[0].action.source == "community_engager",
      [d.action.source for d in pick])
check("التفسير يذكر القيمة المتعلَّمة", "learned=" in pick[0].explanation, pick[0].explanation)

print("5) العائد المتأخر: تفاعل المنشورات الحقيقي")
cfg4 = make_config()
for i, (arm, eng) in enumerate([("publish_content|trend_participation", 90),
                                ("publish_content|trend_participation", 110),
                                ("publish_content|daily_content_creator", 10),
                                ("publish_content|daily_content_creator", 12)]):
    strategy.record_published(cfg4, url=f"https://x.com/me/status/{i}", text="t", pillar="p",
                              format_id="insight", arm=arm)
    p = strategy._ledger(cfg4)["posts"][f"https://x.com/me/status/{i}"]
    p.update(measured=True, engagement=eng)
for arm in ("publish_content|trend_participation", "publish_content|daily_content_creator"):
    L.record(cfg4, arm, "ok")
hi = L.value(cfg4, "publish_content|trend_participation")[0]
lo = L.value(cfg4, "publish_content|daily_content_creator")[0]
check("الذراع الذي يُثمر تفاعلاً أعلى قيمةً من المماثل ضعيف التفاعل", hi > lo, (hi, lo))
check("سجل الأداء يحفظ الذراع مع المنشور",
      strategy._ledger(cfg4)["posts"]["https://x.com/me/status/0"]["arm"] == "publish_content|trend_participation")

print("6) التقييم: تشخيص الخمول، المنشنات المعلّقة، درجة مستقرة")
cfgi = make_config()
ci = ctx_for(cfgi)
ci.scratch["execution_results"] = []
ci.scratch["guard_report"] = {"reviewed": 1, "rejected": ["x"], "rejected_arms": [
    {"arm": "quote_post|content_quoter", "reason": "سياسي معاصر خارج النمط"}]}
f = C.deterministic_findings(ci)
keys = {i["key"] for i in f["issues"]}
check("خمول بسبب الحارس ← blocked_by_guard لا «لا تنتظر»",
      f["idle_reason"] == "guard_blocked" and "blocked_by_guard" in keys and "idle_cycle" not in keys, keys)
check("توجيهه يذكر سبب الرفض", any("سياسي معاصر" in i["directive"] for i in f["issues"]))
cb = make_config(daily_post_target=1, daily_replies_target=1, daily_likes_target=0,
                 daily_retweets_target=0, daily_follows_target=0, daily_quotes_target=0,
                 daily_bookmarks_target=0)
cb.runtime_state.counters.update(posts_today=1, replies_today=1)
cxb = ctx_for(cb)
cxb.scratch["execution_results"] = []
fb = C.deterministic_findings(cxb)
check("الحصص مكتملة ← budget_done بلا مشكلة ولا عقوبة",
      fb["idle_reason"] == "budget_done" and not fb["issues"] and C._heuristic_score(fb) >= 7.5,
      (fb["idle_reason"], fb["issues"], C._heuristic_score(fb)))
cm = make_config()
cm.runtime_state.flags["engaged_urls"] = ["https://x.com/a/status/1"]
cxm = ctx_for(cm)
cxm.scratch["mentions"] = [{"url": "https://x.com/a/status/1", "text": "شكراً لك"}]
cxm.scratch["execution_results"] = [{"capability": "like_post", "success": True}]
fm = C.deterministic_findings(cxm)
check("منشن رُدّ عليه سابقاً ← ليس «تجاهلاً» (قيس 90 إنذاراً كاذباً)",
      fm["mentions_pending"] == 0 and "ignored_mentions" not in {i["key"] for i in fm["issues"]})
cxm.scratch["mentions"].append({"url": "https://x.com/b/status/2", "text": "سؤال جديد لك"})
fm2 = C.deterministic_findings(cxm)
check("منشن جديد بلا رد ← يُكتشف", "ignored_mentions" in {i["key"] for i in fm2["issues"]})


class FakeLLM:
    available = True

    def complete_json(self, system, prompt, temperature=0.3):
        return {"score": 2, "verdict": "متشدد", "directives": ["توجيه"]}


cg = make_config()
cxg = ctx_for(cg)
cxg.llm = FakeLLM()
cxg.scratch["execution_results"] = [{"capability": "reply_to_user", "success": True, "target": "t"},
                                    {"capability": "like_post", "success": True}]
crit = C.build_critique(cxg)
check("درجة النموذج المتشددة (2) لا تهيمن: مزيج 60% أرقام + 40% نموذج",
      crit["llm_score"] == 2 and crit["score"] == round(0.6 * crit["rule_score"] + 0.4 * 2, 1)
      and crit["score"] > 4, crit["score"])

print("7) منحنى التعلّم وفاعلية الدروس")
cc = make_config()
for s in [2, 3, 2, 3, 2, 6, 7, 7, 8, 7]:
    curve = L.record_score(cc, s)
check("المنحنى يكشف التحسّن", curve["trend"] == "up" and curve["recent_avg"] > curve["previous_avg"], curve)
cl = make_config()
issue = [{"key": "topic_repetition", "directive": "غيّر الموضوع"}]
L.track_lessons(cl, issue)
for _ in range(3):
    L.track_lessons(cl, [])
check("مشكلة اختفت 3 دورات بعد درسها ← درس نافع",
      L.state(cl)["lessons"]["topic_repetition"]["status"] == "effective")
ce = make_config()
esc = []
for _ in range(6):
    esc += L.track_lessons(ce, [{"key": "ignored_mentions", "directive": "أجب أولاً"}])
check("مشكلة تكرّرت 5 مرات بعد درسها ← غير نافع ويُصعَّد مرة واحدة",
      L.state(ce)["lessons"]["ignored_mentions"]["status"] == "ineffective" and len(esc) == 1, esc)

print("8) الدروس المكرّرة بصياغات أخرى تُدمج (من بيانات emaad الحية)")
real = ["انشر منشوراً سياسياً واحداً عن اليمن خلال الدورة، يبدأ بعنوان صادم ثم سطرين تحليل.",
        "انشر منشوراً واحداً عن اليمن بصيغة: خبر صادم + رقم + جهة + نتيجة مباشرة.",
        "انشر منشوراً سياسياً واحداً عن اليمن بعنوان صادم ثم سطرين يشرحان الرقم أو المكان أو الجهة."]
existing = [{"id": 1, "content": real[0]}]
check("صيغة ثانية للفكرة نفسها ← مكرّرة", L.near_duplicate(real[2], existing) is not None)
check("درس مختلف فعلاً ← ليس مكرّراً",
      L.near_duplicate("اختم بجملة نتيجية حاسمة: ماذا يعني هذا التطور للسعودية والتحالف؟", existing) is None)

print("9) الدورة كاملة: التعلّم يُسجَّل والدرس مقروء")
from app.aeos.cognition.phases import LearnPhase  # noqa: E402
from app.aeos.memory.manager import MemoryManager  # noqa: E402

cf = make_config()
cxf = ctx_for(cf)
cxf.memory = MemoryManager()
cxf.scratch["guard_report"] = {"rejected_arms": [{"arm": "quote_post|content_quoter", "reason": "سياسي معاصر"}]}
cxf.scratch["execution_results"] = [{"capability": "reply_to_user", "success": True, "status": "succeeded",
                                     "arm": "reply_to_user|community_engager"}]
cxf.scratch["evaluation"] = {"actions": 1, "successes": 1, "failures": 0}
LearnPhase().run(cxf)
check("LearnPhase يسجّل النجاح والرفض في سجل التجربة",
      L.state(cf)["arms"]["reply_to_user|community_engager"]["ok"] == 1
      and L.state(cf)["arms"]["quote_post|content_quoter"]["rej"] == 1)
check("الدرس جملة مفهومة لا قالب «executed X»",
      "نجح" in cxf.scratch["lesson"] and "رفض الحارس" in cxf.scratch["lesson"], cxf.scratch["lesson"])
rep = L.report(cf)
check("تقرير التعلّم يعرض الأذرع مرتّبة بالقيمة", rep["arms"][0]["arm"] == "quote_post|content_quoter"
      and rep["arms"][-1]["arm"] == "reply_to_user|community_engager", rep["arms"])

print("10) «في مثل هذا اليوم» يفضّل الأحداث القديمة (سبب الرفض المقيس)")
import app.aeos.skills.builtin as B  # noqa: E402

events = [{"year": y, "text": f"حدث سياسي تاريخي سنة {y}"} for y in (2009, 2008, 2001, 1990, 1932, 1916, 1890)]
B._fetch_on_this_day_events = lambda: events
co = make_config(on_this_day_mode=True, daily_post_target=3)
co.persona.interests = ["politics"]
acts = B.OnThisDaySkill().propose(ctx_for(co))
brief = acts[0].params["brief"] if acts else ""
check("الموجز بلا أحداث 2008–2009 المعاصرة", acts and "2009" not in brief and "2008" not in brief, brief[:200])

print()
if FAILED:
    print(f"❌ {len(FAILED)} فحص فشل: {FAILED}")
    sys.exit(1)
print("✅ كل فحوص التعلّم الذاتي نجحت")
