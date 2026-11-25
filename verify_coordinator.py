"""
تحقّق المنسّق العام (app/aeos/services/orchestrator.py) — بلا شبكة وبلا LLM حقيقي
وبلا أي فعل على X: عميل LLM مزيّف يصدر نداءات أدوات مُبرمجة، وكل ما يكتب أو
ينشر مُستبدَل بمسجّل.

    .\\venv\\Scripts\\python.exe verify_coordinator.py
"""
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("PYTHONUTF8", "1")
sys.stdout.reconfigure(encoding="utf-8")

from app.aeos.employee.models import EmployeeConfig, EmployeeStatus, Persona  # noqa: E402
from app.aeos.services import orchestrator as O  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


# ── فريق مزيّف ──
TEAM = [
    EmployeeConfig(id="e1", user_id=1, platform="x", account_username="alpha",
                   persona=Persona(display_name="أحمد", interests=["tech"])),
    EmployeeConfig(id="e2", user_id=1, platform="x", account_username="beta",
                   persona=Persona(display_name="سارة", interests=["history"])),
    EmployeeConfig(id="orphan", user_id=None, platform="x", account_username="unowned"),
    EmployeeConfig(id="e3", user_id=2, platform="x", account_username="gamma"),
    EmployeeConfig(id="e4", user_id=1, platform="x", account_username="old",
                   status=EmployeeStatus.ARCHIVED),
]
O.employees_for.__globals__["_platform"] = lambda: SimpleNamespace(
    repository=SimpleNamespace(list_all=lambda: TEAM, get=lambda i: next(c for c in TEAM if c.id == i)),
    llm=None,
)

from scripts.security_fixture import setup, reviewed_fixture_actions
setup(1, [("e1", "alpha"), ("e2", "beta")])
reviewed_fixture_actions(1, {"chat.order_publish", "chat.update_employee", "campaign.dispatch"}).start()
print("1) الفريق والملكية")
handles = [c.account_username for c in O.employees_for(1)]
check("فريق المالك فقط، مع استبعاد المؤرشف ومجهول الملكية", handles == ["alpha", "beta"], handles)
check("حل بالاسم المعروض", O.resolve_employee("سارة", 1).account_username == "beta")
check("موظف مستخدم آخر غير قابل للحل", O.resolve_employee("gamma", 1) is None)

print("2) التوجيه الهجين")
m = O.mentioned_employees("@alpha و @beta انشروا عن الذكاء", 1)
check("ذِكر موظفَين ⇒ رسالة فريق", O.is_fleet_message("x", m))
check("«كل الموظفين» ⇒ رسالة فريق", O.is_fleet_message("خل كل الموظفين ينشرون", []))
check("«حالة الموظف» (مفرد) ليست رسالة فريق", not O.is_fleet_message("حالة الموظف", []))
one = O.mentioned_employees("@alpha انشر عن الطقس", 1)
check("@ لموظف واحد يُزال من نص الأمر",
      O.strip_mentions("@alpha انشر عن الطقس", one) == "انشر عن الطقس")
check("@ لحساب ليس موظفاً لا يُحتسب", O.mentioned_employees("رد على @someone", 1) == [])


# ── عميل LLM مزيّف ──
def _call(i, name, args):
    return SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=name, arguments=json.dumps(args)))


class FakeClient:
    def __init__(self, script):
        self.script, self.seen = list(script), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.seen.append(kw)
        step = self.script.pop(0)
        msg = SimpleNamespace(content=step if isinstance(step, str) else "",
                              tool_calls=None if isinstance(step, str) else step)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


dispatched = []
O._Tools._dispatch = lambda self, cfg, payload, caps, label: (
    dispatched.append((cfg.account_username, payload, caps)), self._mark(cfg))

print("3) حلقة الأدوات")
check("بلا LLM ⇒ None (يكمل المسار القديم)",
      O.handle("مرحبا", 1, client_factory=lambda: (None, None)) is None)

fake = FakeClient([
    [_call(1, "order_publish", {"employee": "alpha", "topic": "الذكاء الاصطناعي"}),
     _call(2, "order_publish", {"employee": "@beta", "topic": "الذكاء الاصطناعي"}),
     _call(3, "order_publish", {"employee": "ghost", "topic": "x"})],
    "أسندت المهمة لأحمد وسارة.",
])
out = O.handle("خل الفريق ينشر عن الذكاء الاصطناعي", 1, client_factory=lambda: (fake, "m"))
check("يعيد رد المنسّق النهائي", out and out["message"] == "أسندت المهمة لأحمد وسارة.")
check("أُسند الأمر للموظفَين", [d[0] for d in dispatched] == ["alpha", "beta"], dispatched)
check("النشر يُتحقّق منه بقدرتي النشر",
      dispatched[0][2] == ["publish_content", "publish_thread"])
check("dispatched يعيد الموظفين المكلّفين", out["dispatched"] == ["alpha", "beta"])
tool_msgs = [m for m in fake.seen[-1]["messages"] if m["role"] == "tool"]
check("موظف مجهول ⇒ خطأ يعود للـLLM بقائمة الفريق",
      "error" in tool_msgs[2]["content"])
team_data = next(json.loads(m["content"])["value"] for m in fake.seen[0]["messages"]
                 if m["role"] == "user" and '"untrusted_data": "team"' in m["content"])
check("الفريق بيانات مقيدة بالمالك وخارج تعليمات النظام",
      [e["handle"] for e in team_data["employees"]] == ["alpha", "beta"])
check("لا أداة حذف", not any("delete" in t["function"]["name"] for t in O.TOOLS))

print("4) التعديل يعيد قراءة الإعداد بين الخطوات")
from app.aeos.services import employee_commands as C  # noqa: E402

calls = []
C._handle_update_persona = lambda p, cfg, e: calls.append(("persona", e)) or "p"
C._handle_set_schedule = lambda p, cfg, e: calls.append(("schedule", e)) or "s"
C._handle_teach = lambda p, cfg, e, raw: calls.append(("teach", e)) or "t"
msg = "خلّ اهتمامات أحمد الذكاء الاصطناعي وخلّه يشتغل كل ٣٠ دقيقة"
res = json.loads(O._Tools(1, msg).call("update_employee", json.dumps(
    {"employee": "alpha", "interests": ["ai"], "interval_minutes": 30, "add_instruction": "ركّز"})))
check("كل الحقول طُبّقت بالترتيب", [c[0] for c in calls] == ["persona", "schedule", "teach"], calls)
check("حقل فارغ ⇒ خطأ",
      "error" in json.loads(O._Tools(1, msg).call("update_employee", '{"employee": "alpha"}')))

# الحالة المقاسة فعلاً: الموديل ملأ كل حقول المخطط لطلب «كل 45 دقيقة»
calls.clear()
res = json.loads(O._Tools(1, "خل أحمد يشتغل كل 45 دقيقة").call("update_employee", json.dumps({
    "employee": "alpha", "interests": ["tech"], "tone": "كاتب", "dialect": "خليجية",
    "interval_minutes": 45, "active_hours_start": 0, "active_hours_end": 23,
    "daily_post_target": 1, "add_instruction": ""})))
check("حقول لم يطلبها المالك تُتجاهل (لا شخصية، لا ساعات، لا تغيير للهدف)",
      calls == [("schedule", {"interval_minutes": 45})], calls)
check("المُتجاهَل يُبلَّغ للموديل", "dialect" in res.get("ignored_unrequested_fields", []), res)
check("أداة غير معروفة مرفوضة", "error" in json.loads(O._Tools(1).call("__init__", "{}")))

print("5) فشل الشبكة بعد تنفيذ أداة لا يُخفي ما نُفّذ")


class Boom(FakeClient):
    def _create(self, **kw):
        if self.seen:
            raise RuntimeError("network")
        return super()._create(**kw)


dispatched.clear()
boom = Boom([[_call(1, "order_publish", {"employee": "alpha", "topic": "x"})]])
out = O.handle("انشر", 1, client_factory=lambda: (boom, "m"))
check("يعيد تنبيهاً بما نُفّذ", out and "order_publish" in out["message"] and out["dispatched"] == ["alpha"])

print("6) وضع الموظف المخاطَب مباشرة (@ أو محادثته)")
alpha = O.resolve_employee("alpha", 1)
schemas = O._employee_tool_schemas()
names = {t["function"]["name"] for t in schemas}
check("أدوات الموظف بلا إدارة الفريق (لا list_employees/clone/attach/forget)",
      not names & {"list_employees", "clone_identity", "attach_tool", "forget"} and "order_mission" in names)
check("مخطط أدوات الموظف بلا وسيط employee",
      all("employee" not in t["function"]["parameters"].get("properties", {}) for t in schemas))
dispatched.clear()
et = O._Tools(1, "انشر عن الطقس", only_employee=alpha)
r = json.loads(et.call("order_publish", json.dumps({"employee": "beta", "topic": "الطقس"})))
check("طلب تغيير هوية الموظف يُرفض", "error" in r and not dispatched)
r = json.loads(et.call("order_publish", json.dumps({"topic": "الطقس"})))
check("تستمد الأداة الموظف من السياق الموثق",
      r.get("employee") == "alpha" and [d[0] for d in dispatched] == ["alpha"])
check("أداة خارج قائمة الموظف مرفوضة", "error" in json.loads(et.call("list_employees", "{}")))
fake = FakeClient(["أهلاً! أنا أحمد، اليوم نشرت منشوراً واحداً. أقترح: ..."])
out = O.handle(O.CALL_ONLY, 1, employee=alpha, client_factory=lambda: (fake, "m"))
sysmsg = fake.seen[0]["messages"][0]["content"]
check("الموظف يتحدث بنفسه: موجّه بصيغة المتكلم ومالكه هو المخاطِب",
      out and "مالك" in sysmsg and '"account": "alpha"' in fake.seen[0]["messages"][1]["content"])
check("النداء بلا رسالة له تعليمات ترحيب واقتراح", O.CALL_ONLY in sysmsg)
check("الموظف يُمنع من طلب رابط تغريدة بلا داعٍ", "لا تطلب رابط تغريدة" in sysmsg)
check("أدوات الموظف هي المرسلة للموديل", {t["function"]["name"] for t in fake.seen[0]["tools"]} == names)

print("7) «الوكلاء» = الفريق (رسائل المالك المسجّلة حرفياً، 2026-11-12)")
for msg in ("خل كل الوكلاء ينشرون عن #الحوثي_يستهدف_قبله_المسلمين", "خل كل الوكلاء ينشرون عن  تقارب الحوثي مع اسرائيل",
            "شغّل دورة عمل الآن لكل الوكلاء النشطين", "وجّه وكلائي للنشر عن الاقتصاد", "انشروا من كل الحسابات",
            "خل الحسابات كلها تتكلم عن العيد", "make all agents post about AI"):
    check(f"رسالة فريق: «{msg[:40]}»", O.is_fleet_message(msg, []))
for msg in ("خل الوكيل ينشر عن الطقس", "انشر عن AI agents الجديدة", "اعرض الحسابات", "حالة الموظف"):
    check(f"ليست رسالة فريق: «{msg}»", not O.is_fleet_message(msg, []))

print("8) رد المالك على خطة حملة معروضة يعود للمنسّق ولو من محادثة موظف")
from app.aeos.services import campaigns as CP  # noqa: E402
from app.aeos.services import media_brief as MB  # noqa: E402

MB.build = lambda topic, user_id=None, llm=None, query="": {
    "ok": True, "topic": topic, "headline": "عنوان الرسالة", "summary": "خلاصة", "angles": ["زاوية 1", "زاوية 2"],
    "facts": [], "latest": [], "numbers": [], "statements": [], "do_not_claim": [],
    "sources": [{"n": 1, "title": "مصدر", "url": "https://news.example/a", "site": "news.example", "date": ""}]}
O.clear_pending_plan(1)
check("بلا خطة معروضة: «نعم» لا تُخطف من الموظف", not O.routes_to_coordinator("نعم قم بالنشر", [], 1, "emp_alpha"))
T = O._Tools(1, "خل الفريق ينشر عن العيد", session_id="emp_alpha")
plan = T.plan_campaign({"topic": "العيد"})
check("الخطة بلا dispatch تُسجَّل «تنتظر الموافقة»", O.pending_plan(1) and O.pending_plan(1)["topic"] == "العيد"
      and "dispatched" not in plan)
for msg in ("نعم قم بالنشر", "نعم", "وجّههم", "بس @beta", "لا الغ الحملة", "ok"):
    check(f"«{msg}» من محادثة موظف ⇒ المنسّق", O.routes_to_coordinator(msg, [], 1, "emp_alpha"))
check("رسالة عادية للموظف لا تُخطف ولو وُجدت خطة", not O.routes_to_coordinator("كيف كان تفاعلك اليوم؟", [], 1, "emp_alpha"))
for msg in ("انشر عن الطقس", "قم بالرد حول الخبر", "اي موظف نشر اليوم؟"):
    check(f"أمر/سؤال جديد للموظف لا يُعدّ موافقة: «{msg}»", not O.routes_to_coordinator(msg, [], 1, "emp_alpha"))
for msg in ("نعم انشروا عن الموضوع", "اي نفّذ", "انشروا"):
    check(f"موافقة صريحة: «{msg}»", O.routes_to_coordinator(msg, [], 1, "emp_alpha"))
check("مستخدم آخر لا تمسّه خطة غيره", not O.routes_to_coordinator("نعم", [], 2, "emp_gamma"))
fake = FakeClient(["جاهز"])
O.handle("نعم", 1, client_factory=lambda: (fake, "m"), session_id="emp_alpha")
sysmsg = fake.seen[0]["messages"][0]["content"]
check("موجّه المنسّق يرى الخطة المنتظرة بموضوعها الحرفي", any("«العيد»" in m["content"] and m["role"] == "user" for m in fake.seen[0]["messages"]))
check("موجّه المنسّق فيه التاريخ والوقت", "بتوقيت الرياض" in sysmsg)

print("9) التوجيه بعد الموافقة ← حملة متابَعة ← خلاصة واحدة للمحادثة التي أمر منها")
delivered = []
CP._deliver = lambda camp, text: delivered.append((camp.get("session_id"), text))
dispatched.clear()
out = T.plan_campaign({"topic": "العيد", "dispatch": True})
cid = out.get("campaign_id", "")
check("الموافقة تمسح الخطة المنتظرة", O.pending_plan(1) is None)
check("كل موظف يتلقى معرّف الحملة في أمره", cid and all(d[1].get("campaign_id") == cid for d in dispatched)
      and [d[0] for d in dispatched] == ["alpha", "beta"], dispatched)
CP.record(cid, "alpha", True, url="https://x.com/alpha/status/1", kind="publish_thread")
check("لا خلاصة قبل اكتمال الجميع", delivered == [])
CP.record(cid, "beta", False, detail="الموظف paused — فعّله ثم أعد الطلب")
check("خلاصة واحدة عند الاكتمال، في المحادثة التي أمر منها", len(delivered) == 1 and delivered[0][0] == "emp_alpha")
text = delivered[0][1] if delivered else ""
check("الخلاصة: مَن نشر + نوعه + الرابط، ومَن فشل + السبب",
      "نُفّذ 1 من 2" in text and "ثريد https://x.com/alpha/status/1" in text and "❌ @beta: الموظف paused" in text, text)
CP.record(cid, "beta", True, url="https://x.com/beta/status/2")
check("نتيجة متأخرة بعد الإغلاق لا ترسل خلاصة ثانية", len(delivered) == 1)
cid2 = CP.start(1, "session_1", "حملة بطيئة", ["alpha", "beta"], timeout=0.05)
CP.record(cid2, "alpha", True, url="https://x.com/alpha/status/3", kind="publish_content")
import time as _t  # noqa: E402

_t.sleep(0.3)
check("بعد المهلة: خلاصة جزئية تذكر مَن لم يصل خبره",
      len(delivered) == 2 and "⏳ @beta" in delivered[1][1] and "منشور https://x.com/alpha/status/3" in delivered[1][1],
      delivered[-1:])
CP.record("camp_unknown", "alpha", True)
check("معرّف حملة مجهول لا يكسر شيئاً", len(delivered) == 2)

print("10) النتيجة تحمل رابط X نفسه")
from app.aeos.services import employee_mission as EM  # noqa: E402

notes = []
plat = SimpleNamespace(notify_owner=lambda eid, text, intent="": notes.append(text))
ok = EM._report_outcome(plat, "e1", {"results": [{"capability": "publish_thread", "success": True,
                                                   "output": {"tweet_url": "https://x.com/alpha/status/9"}}]},
                        ["publish_thread", "publish_content"], "نشر")
check("نجاح ⇒ ok + الرابط + النوع، بلا إشعار فشل",
      ok == {"ok": True, "url": "https://x.com/alpha/status/9", "kind": "publish_thread", "detail": ""} and notes == [])
bad = EM._report_outcome(plat, "e1", {"results": [{"capability": "publish_content", "success": False,
                                                    "error": "انتهت المهلة"}]}, ["publish_content"], "نشر")
check("فشل ⇒ ok=False + السبب + إشعار المالك", bad["ok"] is False and "انتهت المهلة" in bad["detail"] and notes)
from app.aeos.runtime.manager import _action_line  # noqa: E402

line = _action_line({"capability": "publish_content", "success": True,
                     "output": {"text": "منشور", "tweet_url": "https://x.com/alpha/status/9"}})
check("سطر الدورة للنشر فيه الرابط", "🔗 https://x.com/alpha/status/9" in line, line)
check("الثريد والاقتباس لهما وصف عربي (لا اسم القدرة الخام)",
      "نشر ثريداً" in _action_line({"capability": "publish_thread", "success": True, "output": {}})
      and "اقتبس تغريدة" in _action_line({"capability": "quote_post", "success": True, "output": {}}))

print()
if FAILED:
    print(f"❌ {len(FAILED)} فحص فشل: {FAILED}")
    sys.exit(1)
print("✅ كل فحوص المنسّق نجحت")
