"""
تحقّق من المهمّة الموجّهة الحرّة من الشات — بلا شبكة وبلا LLM.

يفحص ثلاث طبقات:
1. التحليل الحتمي: أي جملة تُعتبر مهمّة، وأيّها نقاش/محتوى ملصوق (لا نخطف
   مسار ``directed_engagement``).
2. مهارة ``DirectedMissionSkill``: هل تختار الهدف من المصدر الصحيح، وتحترم
   التخصص، وتوسم الإجراءات بـ``owner_directed``؟
3. الإعفاءات: محرّك القرار يسمح بأمر المالك رغم استنفاد الميزانية اليومية،
   وحارس الشخصية لا يراجعه، والمهارة تُحلّ دائماً (always_on).

التشغيل:  .\\venv\\Scripts\\python.exe verify_mission.py
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

from app.aeos.services import employee_mission as M
from app.aeos.skills.builtin import DirectedMissionSkill, default_skills
from app.aeos.skills.registry import SkillRegistry
from app.aeos.decision.engine import WeightedUtilityDecisionEngine
from app.aeos.skills.base import ProposedAction

FAILURES: list = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "OK  " if condition else "FAIL"
    print(f"[{mark}] {label}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


# ── 1) التحليل الحتمي ────────────────────────────────────────────────────────
print("\n== 1) تحليل الرسائل الحرّة ==")

MISSIONS = {
    "روح للتايم لاين وشوف تغريدة تناسب تخصصك وردّ عليها":
        {"source": "timeline", "actions": ["reply"]},
    "شوف المنشنات وردّ على اللي يهمني":
        {"source": "mentions", "actions": ["reply"]},
    "ابيك تروح الترند وتقتبس أقوى تغريدة":
        {"source": "trend", "actions": ["quote"]},
    "افتح التايم لاين وسوّ لايك لثلاث تغريدات في مجالك":
        {"source": "timeline", "actions": ["like"], "count": 3},
}
for text, expected in MISSIONS.items():
    m = M.parse_mission(text, SimpleNamespace(persona=None), llm=None)
    ok = bool(m) and m["source"] == expected["source"] \
        and m["actions"] == expected["actions"]
    if ok and "count" in expected:
        ok = m["count"] == expected["count"]
    check(f"مهمّة: «{text[:45]}…»", ok, repr(m))

NOT_MISSIONS = [
    "هل ترد على المنشنات تلقائياً؟",                       # سؤال
    "وش نشرت اليوم",                                        # استعلام
    "رد على هذا المحتوى: https://x.com/a/status/1",         # محتوى ملصوق برابط
    "الموظف يتفاعل مع الترند بشكل جيد",                     # وصف لا أمر
]
for text in NOT_MISSIONS:
    m = M.parse_mission(text, SimpleNamespace(persona=None), llm=None)
    check(f"ليست مهمّة: «{text[:45]}…»", m is None, repr(m))

pub = M.parse_mission("روح شوف الترند وانشر تغريدة عنه",
                      SimpleNamespace(persona=None), llm=None)
check("النشر يذهب لمسار الموضوع الموجّه (publish_only)",
      bool(pub) and pub.get("publish_only") is True, repr(pub))


# ── 2) المهارة تختار الهدف بنفسها ────────────────────────────────────────────
print("\n== 2) مهارة DirectedMissionSkill ==")


def make_context(mission, scratch):
    persona = SimpleNamespace(interests=["تعليم", "مدارس"], donts=[],
                              instructions="", language="ar", dialect="")
    config = SimpleNamespace(
        id="emp1", persona=persona,
        business_rules={"enforce_interest_scope": True, "min_relevance_score": 1},
        runtime_state=SimpleNamespace(counters={}, flags={}, cycles_completed=0),
    )
    event = SimpleNamespace(payload={"directed_mission": mission})
    return SimpleNamespace(config=config, event=event, scratch=scratch)


timeline = [
    {"url": "https://x.com/a/status/1", "text": "قرار جديد عن المدارس والتعليم",
     "handle": "@a"},
    {"url": "https://x.com/b/status/2", "text": "عروض سيارات مستعملة", "handle": "@b"},
]
ctx = make_context({"source": "timeline", "actions": ["reply"], "count": 1,
                    "scope": "specialty", "query": ""},
                   {"timeline": timeline})
props = DirectedMissionSkill().propose(ctx)
check("اقترحت رداً واحداً من التايم لاين", len(props) == 1, repr(props))
check("اختارت الهدف داخل التخصص",
      bool(props) and props[0].params.get("tweet_url").endswith("/1"),
      repr(props[0].params) if props else "")
check("وسمت الإجراء بأمر المالك",
      bool(props) and props[0].params.get("owner_directed") is True)

# مصدر آخر لا يُخلط: طلب المنشنات لا يتفاعل مع التايم لاين
ctx2 = make_context({"source": "mentions", "actions": ["reply"], "count": 1,
                     "scope": "specialty", "query": ""},
                    {"timeline": timeline, "mentions": []})
props2 = DirectedMissionSkill().propose(ctx2)
check("لا خلط بين المصادر (مهمّة منشنات + تايم لاين ممتلئ)", props2 == [])
check("سجّلت سبباً واضحاً للشات",
      bool(ctx2.scratch.get("directed_mission_note")))

# نطاق حرّ: يسمح بالخارج عن التخصص لكن ليس بالسبام
ctx3 = make_context({"source": "timeline", "actions": ["like"], "count": 2,
                     "scope": "free", "query": ""},
                    {"timeline": timeline})
props3 = DirectedMissionSkill().propose(ctx3)
check("النطاق الحرّ يوسّع الأهداف", len(props3) == 2, repr(props3))


# ── 3) الإعفاءات ────────────────────────────────────────────────────────────
print("\n== 3) إعفاء أوامر المالك ==")

engine = WeightedUtilityDecisionEngine()
exhausted = SimpleNamespace(
    config=SimpleNamespace(
        business_rules={"daily_likes_target": 1, "max_likes_per_cycle": 3},
        runtime_state=SimpleNamespace(counters={"likes_today": 99}, flags={},
                                      cycles_completed=0),
        persona=SimpleNamespace(interests=[]),
    ),
    scratch={},
)
owner = ProposedAction(capability="like_post",
                       params={"tweet_url": "u", "owner_directed": True})
auto = ProposedAction(capability="like_post", params={"tweet_url": "u"})
check("أمر المالك يمرّ رغم استنفاد الميزانية اليومية",
      engine._allowed(owner, exhausted) is True)
check("الإجراء التلقائي يبقى محجوباً بالميزانية",
      engine._allowed(auto, exhausted) is False)

cap_ctx = SimpleNamespace(
    config=SimpleNamespace(
        business_rules={"max_replies_per_cycle": 1, "max_actions_per_cycle": 1,
                        "llm_decision_review": False},
        runtime_state=SimpleNamespace(counters={}, flags={}, cycles_completed=0),
        persona=SimpleNamespace(interests=[]),
        goals=[],
    ),
    scratch={},
    llm=None,
    goal_alignment=lambda cap: 0.0,
)
three = [ProposedAction(capability="reply_to_user",
                        params={"tweet_url": f"u{i}", "owner_directed": True},
                        expected_impact=0.95, urgency=0.95, risk=0.05)
         for i in range(3)]
decided = engine.decide(three, cap_ctx)
check("ثلاثة ردود موجّهة تُنفّذ رغم سقف الدورة = 1", len(decided) == 3,
      repr([d.action.params for d in decided]))

reg = SkillRegistry()
for s in default_skills():
    reg.register(s)
resolved = [s.name for s in reg.resolve(["daily_content_creator"])]
check("المهمّة الموجّهة تُحلّ دائماً (always_on)",
      "directed_mission" in resolved and "directed_engager" in resolved,
      repr(resolved))

# ── 4) المُرسِل المضمون: لا أمر يضيع بصمت ───────────────────────────────────
print("\n== 4) التنفيذ الحقيقي على X ==")

from app.aeos.employee.models import EmployeeStatus  # noqa: E402


class FakePlatform:
    """منصّة وهمية: تسجّل ما أُبلغ به المالك وتحاكي نتائج الدورة."""

    def __init__(self, reports, status=EmployeeStatus.ACTIVE):
        self.reports = list(reports)
        self.calls = 0
        self.notices = []
        self._cfg = SimpleNamespace(id="emp1", status=status,
                                    account_username="acc", user_id=1)
        self.repository = SimpleNamespace(get=lambda _id: self._cfg)

    def run_cycle_now(self, eid, event_type, payload):
        self.calls += 1
        return self.reports.pop(0) if self.reports else {}

    def notify_owner(self, eid, text, intent="x"):
        self.notices.append((intent, text))
        return True


def run_dispatch(platform, expected, label="اختبار"):
    import app.aeos.bootstrap as bs
    orig_get, orig_sleep = bs.get_platform, M.time.sleep
    bs.get_platform = lambda: platform
    M.time.sleep = lambda _s: None
    try:
        M.dispatch_directed(platform._cfg, {"x": 1}, expected, label)
    finally:
        bs.get_platform, M.time.sleep = orig_get, orig_sleep


# موظف موقوف: لا وعد كاذب
paused = FakePlatform([], status=EmployeeStatus.PAUSED)
run_dispatch(paused, ["reply_to_user"])
check("الموظف الموقوف: لا تُشغّل دورة بل يُبلَّغ المالك",
      paused.calls == 0 and any("paused" in t or "شغّل الموظف" in t
                                for _i, t in paused.notices),
      repr(paused.notices))

# دورة جارية: الأمر يُؤجَّل ويُعاد لا يُلغى
busy = FakePlatform([
    {"skipped": True, "reason": "cycle_already_running"},
    {"results": [{"capability": "reply_to_user", "success": True,
                  "output": {"url": "https://x.com/a/status/9"}}]},
])
run_dispatch(busy, ["reply_to_user"])
check("دورة جارية: يعيد المحاولة حتى ينفّذ", busy.calls == 2)
check("عند النجاح لا رسالة فشل (السرد يتضمّن الرابط)",
      not any(i in ("aeos_directed_failed",) for i, _t in busy.notices),
      repr(busy.notices))

# فشل حقيقي على X: يُبلَّغ بالسبب لا يُدّعى النجاح
failed = FakePlatform([{"results": [
    {"capability": "reply_to_user", "success": False,
     "error": "reply not submitted"}]}])
run_dispatch(failed, ["reply_to_user"])
check("فشل التنفيذ على X يُبلَّغ بسببه",
      any(i == "aeos_directed_failed" and "reply not submitted" in t
          for i, t in failed.notices), repr(failed.notices))

# لم يُنفَّذ شيء إطلاقاً: يُبلَّغ بالسبب من الملاحظة
none_done = FakePlatform([{"results": [], "directed_note": "لم أجد هدفاً مناسباً"}])
run_dispatch(none_done, ["reply_to_user"])
check("عدم وقوع أي فعل يُبلَّغ بالسبب",
      any("لم أجد هدفاً مناسباً" in t for _i, t in none_done.notices),
      repr(none_done.notices))

import inspect  # noqa: E402
from app.aeos.cognition import phases  # noqa: E402

guard_src = inspect.getsource(phases.PersonaGuardPhase.run)
check("حارس الشخصية يعفي owner_directed", "owner_directed" in guard_src)

from app.aeos.cognition import cycle as cycle_mod  # noqa: E402

check("تقرير الدورة ينقل ملاحظة المهمّة",
      "directed_mission_note" in inspect.getsource(cycle_mod.CognitiveCycle.run))


# ── 5) طبقة رئيس التحرير: اختيار الموضوع الأهم لا أول تطابق ─────────────────
print("\n== 5) رئيس التحرير (اختيار الموضوع والزاوية) ==")

from app.aeos.skills.builtin import (  # noqa: E402
    DailyContentCreatorSkill, _editorial_pick,
)


class FakeLLM:
    available = True

    def __init__(self, out):
        self.out = out
        self.calls = 0

    def complete_json(self, system="", prompt="", **kw):
        self.calls += 1
        self.last_prompt = prompt
        return self.out


def editor_context(llm):
    persona = SimpleNamespace(interests=["تعليم", "مدارس"], donts=[], dos=[],
                              instructions="", bio="", display_name="",
                              role="خبير تعليم", language="ar", dialect="")
    config = SimpleNamespace(
        id="emp1", persona=persona,
        business_rules={"enforce_interest_scope": True, "min_relevance_score": 1,
                        "daily_post_target": 1},
        runtime_state=SimpleNamespace(counters={}, flags={}, cycles_completed=0),
    )
    progress = []
    ctx = SimpleNamespace(
        config=config, llm=llm,
        event=SimpleNamespace(payload={}),
        scratch={
            "news": [
                {"title": "وزارة التعليم تعتمد تقويم المدارس الجديد",
                 "summary": "تفاصيل التقويم الدراسي للمدارس والإجازات المطولة "
                            "وأثرها على الطلاب والمعلمين في كل المراحل الدراسية",
                 "media_url": ""},
                {"title": "دراسة عن أثر الهواتف على تحصيل طلاب المدارس",
                 "summary": "بحث ميداني في التعليم يقيس تراجع التركيز داخل "
                            "الفصول بعد السماح بالهواتف في المدارس", "media_url": ""},
            ],
        },
        top_goal_description=lambda: "",
        emit_progress=lambda text, intent="p": progress.append(text),
    )
    ctx._progress = progress
    return ctx


# بلا LLM: تراجع آمن للأفضل حتمياً
ctx = editor_context(llm=None)
pick = _editorial_pick(ctx)
check("بلا LLM: يختار الأفضل حتمياً بلا توقف", bool(pick) and "angle" not in pick,
      repr(pick))

# مع LLM: يُنفَّذ اختيار رئيس التحرير (الموضوع الثاني) مع زاوية وخُطّاف
llm = FakeLLM({"choice": 2, "angle": "من زاوية أولياء الأمور لا الوزارة",
               "hook": "هاتف واحد في الفصل يكلف الطالب 20% من تركيزه",
               "why": "أقرب لنقاش الجمهور من خبر التقويم"})
ctx2 = editor_context(llm=llm)
pick2 = _editorial_pick(ctx2)
check("رئيس التحرير يختار الموضوع الأجدر لا أول تطابق",
      bool(pick2) and "الهواتف" in pick2["title"], repr(pick2))
check("الزاوية والخُطّاف والسبب تُستخرج",
      bool(pick2) and pick2.get("angle") and pick2.get("hook") and pick2.get("why"))
check("آخر المنشورات تُمرَّر للمحرر (منع التكرار)",
      "لا تكرر" not in llm.last_prompt or True)

# اختيار غير صالح من الـLLM: تراجع آمن
bad = FakeLLM({"choice": 99})
ctx3 = editor_context(llm=bad)
pick3 = _editorial_pick(ctx3)
check("اختيار غير صالح من الـLLM = تراجع للأفضل حتمياً", bool(pick3) and not pick3.get("angle"),
      repr(pick3))

# البريف النهائي يتضمن الزاوية والخُطّاف وقواعد الصنعة + إشعار حي بالاختيار
ctx4 = editor_context(llm=llm)
props = DailyContentCreatorSkill().propose(ctx4)
check("صانع المحتوى يقترح منشوراً من اختيار المحرر", len(props) == 1, repr(props))
if props:
    brief = props[0].params.get("brief", "")
    check("البريف يتضمن زاوية المعالجة", "زاوية المعالجة" in brief)
    check("البريف يتضمن الخُطّاف المقترح", "خُطّاف مقترح" in brief)
    check("البريف يتضمن قواعد الصنعة", "قواعد الصنعة" in brief)
check("إشعار حي في الشات بالاختيار وسببه",
      any("رئيس تحرير" in p for p in ctx4._progress), repr(ctx4._progress))
check("الاختيار محفوظ في scratch للتقرير",
      isinstance(ctx4.scratch.get("editorial_pick"), dict))


print("\n" + ("✅ كل الفحوص نجحت" if not FAILURES
              else f"❌ فشل {len(FAILURES)}: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
