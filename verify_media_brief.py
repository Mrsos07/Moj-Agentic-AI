"""
verify_media_brief.py — توجيه المنسّق بموضوع ← بحث عميق ← رسالة إعلامية ← كل موظف يكتب منها
بزاويته (لا نشر أعمى). بلا شبكة: Tavily والنموذج مُحاكيان.

    .\\venv\\Scripts\\python.exe verify_media_brief.py
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace as NS

os.environ.setdefault("TOPIC_SEMANTIC", "0")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from app.aeos.mcp.servers import core_mcp  # noqa: E402
from app.aeos.services import media_brief as MB  # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


CALLS = []
FEW = {"on": False}


def fake_tavily(query, **kw):
    CALLS.append((query, kw))
    n = 2 if (FEW["on"] and kw.get("time_range") == "day") else 6
    return {"answer": "ملخص المحرك", "results": [
        {"url": f"https://news{i}.example/a/{kw.get('time_range')}", "title": f"خبر {i}",
         "content": f"تفاصيل الخبر رقم {i} عن ذوباب وتعز", "published_date": "Wed, 14 Oct 2026 06:00:00 GMT"}
        for i in range(n)]}


class FakeLLM:
    available = True

    def __init__(self):
        self.calls = 0

    def complete_json(self, system, prompt, *, temperature=0.3):
        self.calls += 1
        self.last = (system, prompt)
        return {"headline": "معارك متصاعدة في ذوباب", "summary": ["جملة أولى [1].", "جملة ثانية [2]."],
                "latest": [{"when": "Wed, 14 Oct 2026 06:00:00 GMT", "what": "هجوم شمال المطار", "src": "[1]"}],
                "facts": [{"fact": "غارات على تعزيزات", "src": 2}, {"fact": "انسحاب من الصراري", "src": "1, 3"}],
                "numbers": [{"value": "13", "meaning": "هدفاً اعتُرض", "src": 1}],
                "statements": [{"who": "العليمي", "quote": "إنهاء الانقلاب قرار نافذ", "src": 2}],
                "background": "خلفية مختصرة.", "angles": ["زاوية أمنية", "زاوية سياسية", "زاوية إنسانية"],
                "do_not_claim": ["أرقام القتلى غير المؤكدة"], "hashtags": ["#فجر_اليمن"]}

    def complete(self, *a, **k):
        return ""


os.environ["TAVILY_API_KEY"] = "tvly-test"
core_mcp.tavily_search = fake_tavily
llm = FakeLLM()

print("1) البحث العميق والرسالة الإعلامية")
b = MB.build("وجّه الموظفين للنشر عن آخر تطورات الحرب على الحوثيين", user_id=1, llm=llm)
q, kw = CALLS[0]
check("البحث عميق: advanced + أخبار + آخر يوم + ملخّص المزوّد", kw.get("depth") == "advanced"
      and kw.get("topic") == "news" and kw.get("time_range") == "day" and kw.get("include_answer"), kw)
check("الاستعلام بلا كلمات التوجيه («وجّه الموظفين للنشر عن»)", q == "آخر تطورات الحرب على الحوثيين", q)
check("المحرّر يُلزَم بالمصادر فقط وبفصل غير المؤكَّد", "من المصادر المرفقة فقط" in llm.last[0]
      and "do_not_claim" in llm.last[0] and "[1]" in llm.last[1])
check("الرسالة: عنوان ومصادر مرقّمة", b["ok"] and b["headline"] and len(b["sources"]) == 6)
n_calls = len(CALLS)
b2 = MB.build("وجّه الموظفين للنشر عن آخر تطورات الحرب على الحوثيين", user_id=1, llm=llm)
check("موظف ثانٍ بالموضوع نفسه ← بلا بحث جديد (ذاكرة 30 دقيقة)", b2 is b and len(CALLS) == n_calls)
MB.build("وجّه الموظفين للنشر عن آخر تطورات الحرب على الحوثيين", user_id=2, llm=llm)
check("مستخدم آخر ← رسالته الخاصة (لا مشاركة بين المالكين)", len(CALLS) == n_calls + 1)
FEW["on"] = True
CALLS.clear()
MB.build("خبر قليل التغطية", user_id=1, llm=llm)
FEW["on"] = False
check("نتائج اليوم قليلة ← يوسّع لأسبوع ويدمج", [c[1].get("time_range") for c in CALLS] == ["day", "week"])

print("2) ما يتسلّمه كل موظف")
a1, a2 = MB.assign_angle(b, 1, "emp_a"), MB.assign_angle(b, 1, "emp_b")
m1, m2 = MB.for_employee(b, a1), MB.for_employee(b, a2)
check("زاوية مختلفة لكل موظف (لا يكرّرون بعضهم)", a1 != a2 and "زاوية أمنية" in m1 and "زاوية سياسية" in m2)
check("الحقائق والأرقام والتصريحات بمصادرها [n] بصيغة واحدة", "غارات على تعزيزات [2]" in m1
      and "[1][3]" in m1 and "13 — هدفاً اعتُرض [1]" in m1 and "العليمي" in m1 and "[[" not in m1)
check("الخلاصة جملاً لا «['…']» والتاريخ مختصر", "['" not in m1 and "14/10 06:00" in m1)
check("ما لا يجوز ادّعاؤه + تعليمات الاقتباس وعدم الاختراع", "أرقام القتلى غير المؤكدة" in m1
      and "لا تضف أي حقيقة أو رقم ليس في الرسالة" in m1)
check("الموظف نفسه يحتفظ بزاويته عند إعادة التكليف", MB.assign_angle(b, 1, "emp_a") == a1)
nb = MB.build("موضوع بلا نموذج", user_id=9, llm=None)
check("بلا LLM ← موجز حتمي من المصادر (عنوان + حقائق)", nb["headline"] == "خبر 0" and nb["facts"])

print("3) المنسّق: order_publish")
from app.aeos.services import orchestrator as O  # noqa: E402

sent = []
t = O._Tools(1, "")
t._cfg = lambda args: NS(account_username=args["employee"], user_id=1, id="e-" + args["employee"])
t._dispatch = lambda cfg, payload, caps, label: sent.append((cfg.account_username, payload))
_real_platform = O._platform
O._platform = lambda: NS(llm=llm)
# منذ 2026-10-25 «مهمة للفريق» تمرّ بخادم نواه حين يكون حيّاً (order_publish يطلق توجيهاً في الخلفية
# ويعيد status=building). هذا القسم يفحص المحرّك الاحتياطي (Tavily + المحرّر) فنعطّل نواه صراحةً.
from app.aeos.services import nawah_brief as NB  # noqa: E402

_nb_enabled, _nb_peek, _nb_prepare = NB.enabled, NB.peek, NB.prepare
NB.enabled = lambda: False
r1 = t.order_publish({"employee": "emp_x", "topic": "تطورات ذوباب اليوم"})
r2 = t.order_publish({"employee": "emp_y", "topic": "تطورات ذوباب اليوم"})
check("التكليف يحمل الرسالة الإعلامية ومصادرها", "media_brief" in sent[0][1] and sent[0][1]["media_brief_items"])
check("وموجز للمالك (عنوان، زوايا، مصادر) ليعرضه المنسّق", r1["media_brief"]["headline"] and r1["media_brief"]["sources"])
check("موظفان بالموضوع نفسه ← زاويتان مختلفتان", r1["employee_angle"] != r2["employee_angle"], (r1["employee_angle"], r2["employee_angle"]))
r3 = t.order_publish({"employee": "emp_z", "topic": "بلا بحث", "deep_research": False})
check("deep_research=false (طلب المالك صراحةً) ← بلا رسالة", "media_brief" not in sent[-1][1] and r3["dispatched"])
core_mcp.tavily_search = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("tavily down"))
r4 = t.order_publish({"employee": "emp_w", "topic": "موضوع جديد تماماً"})
core_mcp.tavily_search = fake_tavily
check("تعذّر البحث ← التكليف لا يُلغى، ويُبلَّغ المالك بالسبب", r4["dispatched"] and "media_brief_error" in r4
      and "media_brief" not in sent[-1][1])

print("3b) المنسّق مع نواه حيّة: التوجيه يُطلق في الخلفية ولا يعلّق الشات")
prepared = []
NB.enabled = lambda: True
NB.peek = lambda topic, uid: None
NB.prepare = lambda topic, uid, employees=None: (prepared.append(topic), "building")[1]
r5 = t.order_publish({"employee": "emp_n", "topic": "موضوع لنواه"})
check("نواه حيّة ← prepare في الخلفية وstatus=building والحمولة موسومة nawah_brief بلا رسالة بعد",
      prepared == ["موضوع لنواه"] and r5["media_brief"] == {"engine": "nawah", "status": "building"}
      and sent[-1][1].get("nawah_brief") is True and "media_brief" not in sent[-1][1] and "نواه" in r5["note"])
ready = {"ok": True, "topic": "موضوع لنواه", "message": "الرسالة", "facts": ["ح1"], "sources": [{"site": "okaz.com.sa"}],
         "voices": {"emp_n": {"angle": "زاوية الموظف", "draft": "مسودة"}}, "created_at": 0}
NB.peek = lambda topic, uid: ready
r6 = t.order_publish({"employee": "emp_n", "topic": "موضوع لنواه"})
check("توجيه جاهز ← ملخّصه للمالك وزاوية الموظف", r6["media_brief"]["engine"] == "nawah" and r6["media_brief"]["message"] == "الرسالة"
      and r6["employee_angle"] == "زاوية الموظف" and len(prepared) == 1)
NB.enabled, NB.peek, NB.prepare = _nb_enabled, _nb_peek, _nb_prepare
O._platform = _real_platform
p = {"directed_topic": "تطورات ذوباب اليوم"}
MB.attach(p, NS(account_username="pinned", user_id=1), llm)
check("أمر الحساب المثبّت/مهمّة النشر ← تُلحق الرسالة قبل الدورة (attach)", "media_brief" in p and "media_brief_items" in p)

print("4) الموظف يكتب من الرسالة")
from app.aeos.capabilities.handlers import _quality_issues  # noqa: E402
from app.core.system_loader import skill_module  # noqa: E402
from verify_creator import make_config, make_context  # noqa: E402
from app.aeos.core.events import Event, EventType  # noqa: E402

ctx = make_context(make_config(), with_signals=False)
ctx.event = Event(EventType.MANUAL, employee_id="e", source="t",
                  payload={"directed_topic": "تطورات ذوباب", "media_brief": m1,
                           "media_brief_items": MB.research_items(b)})
props = skill_module("daily_content_creator").DailyContentCreatorSkill().propose(ctx)
pp = props[0].params if props else {}
check("المهارة تقترح نشراً من الرسالة (بلا بحث سطحي إضافي)", pp.get("media_brief") == m1
      and not pp.get("research_query") and pp.get("owner_directed"))
check("والبحث مسجّل في الدورة بنوع media_brief (للمخرج البصري وسجل الدورة)",
      ctx.scratch["research"]["items"] and ctx.scratch["research_log"][-1]["kind"] == "media_brief")
post = "ذوباب ليست جبهة فقط بل اختبار لتثبيت المكاسب بعد الهجوم شمال المطار وغارات التحالف على التعزيزات"
full = pp.get("brief", "") + "\n\n" + m1 + " ذوباب جبهة اختبار تثبيت المكاسب الهجوم شمال المطار غارات التحالف التعزيزات"
check("منشور يقتبس حقائق الرسالة لا يُعلَّم «إعادة صياغة التعليمات» (كان يُقارن بالبريف كاملاً)",
      "echo_brief" in _quality_issues(post, ctx, full) and "echo_brief" not in _quality_issues(post, ctx, pp.get("brief", "")))

print("\n— حملة المنسّق: بحث ← رسالة ← مقترح لكل موظف ← توجيه بعد الموافقة")
from app.aeos.services import media_brief as _MB  # noqa: E402
from app.aeos.services import orchestrator as _O  # noqa: E402

_brief = {"ok": True, "topic": "t", "headline": "عنوان", "summary": "خلاصة", "angles": ["أمنية", "سياسية"],
          "facts": [{"fact": "حقيقة", "src": 1}], "sources": [{"n": 1, "title": "x", "url": "https://a.com/1",
                                                               "site": "a.com", "date": ""}]}
_builds = []
_orig_build = _MB.build
_MB.build = lambda topic, **k: (_builds.append(topic), _brief)[1]
_emps = [NS(account_username="pol", persona=NS(role="محلل سياسي", interests=["politics"], tone="جاد"),
            status=NS(value="active")),
         NS(account_username="spt", persona=NS(role="رياضي", interests=["football"], tone="خفيف"),
            status=NS(value="active"))]
_orig_emps, _orig_platform = _O.employees_for, _O._platform
_O.employees_for = lambda uid: _emps


class _LLM:
    available = True

    def complete_json(self, system, prompt, temperature=0.2):
        return {"proposals": [
            {"employee": "@pol", "topic": "الردع", "angle": "قراءة سياسية", "format": "thread", "hook": "لماذا؟"},
            {"employee": "spt", "topic": "-", "angle": "-", "skip": True, "why": "خارج تخصصه"},
            {"employee": "ghost", "topic": "دخيل"}]}


_O._platform = lambda: NS(llm=_LLM())
_T = _O._Tools(1, "")
_sent = []
_T._dispatch = lambda cfg, payload, caps, label: _sent.append((cfg.account_username, caps[0], payload))
try:
    _r = _T.plan_campaign({"topic": "حملة اختبار"})
    _props = {p["employee"]: p for p in _r["proposals"]}
    check("الخطة: رسالة كاملة + مقترح لكل موظف بلا توجيه قبل الموافقة",
          _r["media_brief"]["headline"] == "عنوان" and set(_props) == {"pol", "spt"} and not _sent
          and "أوجّههم" in _r["next"], str(_r)[:300])
    check("موظف خارج اقتراح النموذج لا يُخترع (ghost) والبعيد عن تخصصه skip",
          "ghost" not in _props and _props["spt"]["skip"])
    _r2 = _T.plan_campaign({"topic": "حملة اختبار", "dispatch": True})
    check("الموافقة تعيد استخدام الخطة نفسها (بحث واحد)", len(_builds) == 1, _builds)
    check("التوجيه للمناسبين فقط بزاوية الخطة وصيغتها", _r2["dispatched"] == ["pol"] and _sent[0][1] == "publish_thread"
          and "الردع — قراءة سياسية" in _sent[0][2]["media_brief"], str(_sent)[:300])
    _MB.build = lambda topic, **k: (_ for _ in ()).throw(RuntimeError("x"))  # لن يُستدعى: الخطة مخزّنة
    _sent.clear()
    _T.plan_campaign({"topic": "حملة اختبار", "dispatch": True, "only": ["spt"]})
    check("only يحصر التوجيه (والمستبعَد بـskip لا يُوجَّه)", _sent == [])
finally:
    _MB.build, _O.employees_for, _O._platform = _orig_build, _orig_emps, _orig_platform

print()
if FAILED:
    print(f"❌ {len(FAILED)} فحص فشل: {FAILED}")
    sys.exit(1)
print("✅ كل فحوص الموجز الإعلامي نجحت")
