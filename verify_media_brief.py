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

print()
if FAILED:
    print(f"❌ {len(FAILED)} فحص فشل: {FAILED}")
    sys.exit(1)
print("✅ كل فحوص الموجز الإعلامي نجحت")
